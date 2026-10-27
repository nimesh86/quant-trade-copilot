"""
Autonomous Live Paper Trading Strategy Runner
─────────────────────────────────────────────
Runs 100% headless on the VPS server 24/7.
Monitors live 1-minute candles from the broker feed for active indices (default: SENSEX).
Evaluates the calibrated SMF Cloud strategy indicators on every candle close.
Automatically resolves ATM contracts and places paper trades via ContractTradingEngine
with zero dependency on the frontend UI or local machine.
"""

import os
import sys
import asyncio
import logging
import numpy as np
from datetime import datetime
from zoneinfo import ZoneInfo
from pathlib import Path
from typing import Dict, Any, Optional, List

ENGINE_DIR = Path(__file__).resolve().parent.parent
PROJECT_ROOT = ENGINE_DIR.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
if str(ENGINE_DIR) not in sys.path:
    sys.path.insert(0, str(ENGINE_DIR))

from trading_engine.execution.contract_engine import contract_engine
from trading_engine.adapters.breeze_adapter import default_breeze_adapter
from trading_engine.adapters.telegram_notifier import default_telegram_notifier
from trading_engine.config import get_index_spec

logger = logging.getLogger("autonomous_runner")
logger.setLevel(logging.INFO)


def _get_ist_now() -> datetime:
    try:
        return datetime.now(ZoneInfo("Asia/Kolkata"))
    except Exception:
        return datetime.now()


def is_market_trading_hours() -> bool:
    """Returns True if within active intraday market trading hours (09:25 to 15:15 IST)."""
    now = _get_ist_now()
    if now.weekday() >= 5:  # Saturday or Sunday
        return False
    mins = now.hour * 60 + now.minute
    # 09:25 (after 10m warmup) to 15:15 IST
    return (9 * 60 + 25) <= mins <= (15 * 60 + 15)


def calc_ema(arr: np.ndarray, length: int) -> np.ndarray:
    n = len(arr)
    ema = np.zeros(n)
    if n == 0:
        return ema
    ema[0] = arr[0]
    alpha = 2.0 / (length + 1)
    for i in range(1, n):
        ema[i] = alpha * arr[i] + (1 - alpha) * ema[i - 1]
    return ema


def calc_atr(highs: np.ndarray, lows: np.ndarray, closes: np.ndarray, length: int = 14) -> np.ndarray:
    n = len(closes)
    atr = np.zeros(n)
    if n == 0:
        return atr
    tr = np.zeros(n)
    tr[0] = highs[0] - lows[0]
    for i in range(1, n):
        tr[i] = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
    atr[0] = tr[0]
    for i in range(1, n):
        atr[i] = (atr[i - 1] * (length - 1) + tr[i]) / length
    return atr


def evaluate_smf_signals(candles: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """
    Evaluates SMF Cloud indicators on 1-minute candle series.
    Returns signal dict if a confirmed BUY or SRR_BUY occurs on the latest closed candle.
    """
    n = len(candles)
    if n < 25:
        return None

    opens = np.array([float(c["open"]) for c in candles])
    highs = np.array([float(c["high"]) for c in candles])
    lows = np.array([float(c["low"]) for c in candles])
    closes = np.array([float(c["close"]) for c in candles])
    volumes = np.array([float(c.get("volume", 0) or 1.0) for c in candles])

    f_ema = calc_ema(closes, 20)
    s_ema = calc_ema(closes, 50)
    b_main = (f_ema + s_ema) / 2.0
    atr = calc_atr(highs, lows, closes, 14)

    regimes = np.zeros(n, dtype=int)
    for i in range(n):
        buf = atr[i] * 0.5
        top = max(f_ema[i], s_ema[i])
        bot = min(f_ema[i], s_ema[i])
        if closes[i] > top + buf:
            regimes[i] = 1
        elif closes[i] < bot - buf:
            regimes[i] = -1
        else:
            regimes[i] = regimes[i - 1] if i > 0 else (1 if closes[i] >= b_main[i] else -1)

    # Check latest closed candle (index n - 1 or n - 2 if last is forming)
    last_i = n - 1
    cur_regime = regimes[last_i]
    prev_regime = regimes[last_i - 1] if last_i > 0 else 0

    is_switch_up = (cur_regime == 1 and prev_regime == -1)

    # Support Reversal Reclaim (SRR) detection
    s5 = max(0, last_i - 4)
    recent_dip = np.min(lows[s5:last_i + 1])
    is_srr = (cur_regime == 1 and recent_dip < b_main[last_i] and closes[last_i] >= b_main[last_i])

    if is_switch_up or is_srr:
        cur_c = closes[last_i]
        cur_atr = atr[last_i] if atr[last_i] > 0 else 5.0
        # Calibrated option stop: max 12 pts, min 8 pts
        sl_dist = round(max(8.0, min(14.0, cur_atr * 1.1)), 2)
        t1_dist = round(max(14.0, min(25.0, cur_atr * 1.6)), 2)
        t2_dist = round(t1_dist * 2.0, 2)

        return {
            "signal": "BUY",
            "type": "SRR_BUY" if is_srr and not is_switch_up else "SMF_SWITCH_BUY",
            "bar_time": candles[last_i].get("time"),
            "price": cur_c,
            "sl": round(cur_c - sl_dist, 2),
            "target1": round(cur_c + t1_dist, 2),
            "target2": round(cur_c + t2_dist, 2),
            "atr": cur_atr,
        }

    return None


class AutonomousStrategyRunner:
    """
    Autonomous 24/7 background strategy worker.
    Runs completely headless on VPS.
    """

    def __init__(self, target_index: str = "SENSEX", max_daily_trades: int = 5):
        self.target_index = target_index.upper()
        self.max_daily_trades = max_daily_trades
        self.is_running = False
        self.last_traded_bar_time: Optional[str] = None
        self._last_evaluated_time: Optional[str] = None

    async def run_loop(self):
        """Main continuous evaluation loop running every 5 seconds."""
        self.is_running = True
        logger.info(f"AutonomousStrategyRunner: Started headless monitor for {self.target_index} (Max {self.max_daily_trades} trades/day).")

        while self.is_running:
            try:
                await self.evaluate_step()
            except Exception as e:
                logger.error(f"AutonomousStrategyRunner error in step: {e}", exc_info=True)
            await asyncio.sleep(5.0)

    async def evaluate_step(self):
        # 1. Trading hours check
        if not is_market_trading_hours():
            return

        # 2. Daily trades limit check
        state = contract_engine.get_state()
        if state.get("today_trade_count", 0) >= self.max_daily_trades:
            return

        # 3. Active position check (strict single position constraint)
        if contract_engine.active_position or len(contract_engine.active_positions) > 0:
            return

        # 4. Pending orders check
        if len(contract_engine.pending_orders) > 0:
            return

        # 5. Resolve live spot price and ATM strike for target index
        from trading_engine.web.contract_data_manager import resolve_spot_price, get_option_chain_data, fetch_contract_candles
        spot = resolve_spot_price(self.target_index)
        if not spot or spot <= 0:
            return

        spec = get_index_spec(self.target_index)
        gap = float(spec.get("strike_gap", 100))
        atm_strike = int(round(spot / gap) * gap)

        # 6. Fetch option chain to find active ATM CE and PE contracts
        chain_data = get_option_chain_data(underlying=self.target_index, spot_price=spot)
        chain = chain_data.get("chain", [])
        if not chain:
            return

        atm_row = next((r for r in chain if r.get("is_atm") or int(r.get("strike", 0)) == atm_strike), None)
        if not atm_row:
            atm_row = chain[len(chain) // 2]

        ce_contract = atm_row.get("call_symbol")
        pe_contract = atm_row.get("put_symbol")

        # 7. Ensure contracts are actively subscribed to Breeze WebSocket feeds
        if ce_contract:
            default_breeze_adapter.subscribe_feeds(ce_contract)
        if pe_contract:
            default_breeze_adapter.subscribe_feeds(pe_contract)

        # 8. Evaluate SMF signals on ATM CE and PE 1-minute candles
        from sqlalchemy import create_engine
        db_url = os.environ.get("DB_URL") or "postgresql+psycopg2://postgres:postgres@localhost:5432/trading"
        db_engine = create_engine(db_url)

        candidates = [("CE", ce_contract), ("PE", pe_contract)]
        for opt_type, symbol in candidates:
            if not symbol:
                continue

            candles_data = fetch_contract_candles(symbol, db_engine=db_engine)
            candles = candles_data.get("candles", [])
            if len(candles) < 20:
                continue

            sig = evaluate_smf_signals(candles)
            if not sig:
                continue

            bar_time = str(sig.get("bar_time"))
            if bar_time == self.last_traded_bar_time:
                continue

            # Execute Paper Trade Order Autonomously
            self.last_traded_bar_time = bar_time
            cur_price = sig["price"]
            sl_price = sig["sl"]
            t1_price = sig["target1"]
            t2_price = sig["target2"]

            logger.info(
                f"🤖 [AUTONOMOUS TRADER] SIGNAL DETECTED on {symbol} ({opt_type}): "
                f"Entry @ ₹{cur_price:.2f} | SL ₹{sl_price:.2f} | T1 ₹{t1_price:.2f}"
            )

            res = contract_engine.place_order(
                symbol=symbol,
                side="BUY",
                order_type="MARKET",
                lots=1,
                sl=sl_price,
                target1=t1_price,
                target2=t2_price,
                trailing_mode="be_lock",
                current_market_price=cur_price,
            )

            if res.get("status") == "success":
                lot_units = contract_engine.get_index_lot_size(symbol)
                msg = (
                    f"🤖 <b>AUTONOMOUS LIVE PAPER TRADE EXECUTED</b>\n\n"
                    f"• <b>Contract:</b> {symbol} ({opt_type})\n"
                    f"• <b>Underlying:</b> {self.target_index} (Spot: ₹{spot:,.1f})\n"
                    f"• <b>Lots:</b> 1 Lot ({lot_units} Qty)\n"
                    f"• <b>Entry Fill:</b> ₹{cur_price:.2f}\n"
                    f"• <b>Initial Stop:</b> ₹{sl_price:.2f} (-{cur_price - sl_price:.1f} pts)\n"
                    f"• <b>Target 1 (50%):</b> ₹{t1_price:.2f} (+{t1_price - cur_price:.1f} pts)\n"
                    f"• <b>Trailing:</b> Breakeven Lock + Dynamic Ladder Floor\n"
                    f"• <b>Execution:</b> 100% Headless Background Server Process"
                )
                default_telegram_notifier.send_message(msg)
                logger.info(f"Autonomous trade placed successfully: {res}")
                break


autonomous_runner = AutonomousStrategyRunner(target_index="SENSEX", max_daily_trades=5)
