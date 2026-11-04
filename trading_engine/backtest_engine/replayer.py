"""
Backtest Engine: Dual-Channel Automatic Option Trading Engine
─────────────────────────────────────────────────────────────
Directly monitors and scans At-The-Money (ATM) Call (CE) and Put (PE) option contracts:
  - SmartOptionDetector periodically detects two ATM options (CE & PE) every N minutes (e.g. 5 or 10 min) or immediately after a trade exits.
  - Dual-Channel Scanning: Evaluates indicators (Adaptive Trend Pro, BSS Pro v5, etc.) directly on the option strike candles (OHLCV), capturing true option premium momentum.
  - Concurrency Lock: While any trade is active (e.g. BUY_CE), absolutely no other trade can be entered (not even BUY_PE).
  - Candlestick Reversal Engine: Evaluates Hammer and Inverted Hammer patterns with Buyer/Supplier Strength % to execute quick smart exits.
  - Risk Controls: Strict 15:00 entry cutoff, 15:15 mandatory auto square-off, and last 50-minute market trend guard.
"""
from datetime import datetime
from typing import Dict, Any, List, Optional, Tuple
import pandas as pd
import numpy as np

from trading_engine.quant_core.contracts import (
    Bar, Signal, OrderAction, Position, TradeRecord, OptionContract
)
from trading_engine.quant_core.option_resolver import OptionResolver
from trading_engine.quant_core.option_verifier import OptionVerifier
from trading_engine.quant_core.position_sizer import PositionSizer
from trading_engine.quant_core.lifecycle import TradeLifecycleManager
from trading_engine.quant_core.option_detector import SmartOptionDetector, DEFAULT_ATM_INTERVAL_MINUTES
from trading_engine.quant_core.sr_zone_manager import SRZoneManager
from trading_engine.quant_core.market_structure import MarketStructureEngine
from trading_engine.backtest_engine.feeder import BacktestFeeder


class BacktestReplayer:
    """
    Step-by-step dual-channel option trading engine with dynamic ATM strike detection.
    """

    def __init__(
        self,
        feeder: Optional[BacktestFeeder] = None,
        verifier: Optional[OptionVerifier] = None,
        sizer: Optional[PositionSizer] = None,
        lifecycle: Optional[TradeLifecycleManager] = None,
        atm_interval_minutes: int = DEFAULT_ATM_INTERVAL_MINUTES,
        enable_hammer_reversal: bool = False,
        enable_sr_zones: bool = False,
        risk_factor: float = 0.5,
        entry_cutoff_time: str = "14:15",
        loss_cooldown_mins: int = 20,
        blocked_time_slots: Optional[List[Tuple[str, str]]] = None,
        max_trades_per_day: int = 4
    ):
        self.feeder = feeder or BacktestFeeder()
        self.verifier = verifier or OptionVerifier(strict_mode=False)
        self.sizer = sizer or PositionSizer()
        self.lifecycle = lifecycle or TradeLifecycleManager(auto_square_off_time="15:15")
        self.atm_interval_minutes = atm_interval_minutes
        self.enable_hammer_reversal = enable_hammer_reversal
        self.enable_sr_zones = enable_sr_zones
        self.risk_factor = risk_factor
        self.entry_cutoff_time = entry_cutoff_time
        self.loss_cooldown_mins = loss_cooldown_mins
        self.blocked_time_slots = blocked_time_slots or []
        self.max_trades_per_day = max_trades_per_day

    @staticmethod
    def _resolve_indicator(strategy_name: str, enable_hammer_reversal: bool = False):
        name_norm = (strategy_name or "").lower().strip()
        if "smc" in name_norm or "order flow" in name_norm or "structure" in name_norm:
            from trading_engine.indicators.smc_order_flow import SMCOrderFlowIndicator
            ind = SMCOrderFlowIndicator()
        elif "bss" in name_norm:
            from trading_engine.indicators.bss_pro_v5 import BSSProV5Indicator
            ind = BSSProV5Indicator()
        elif "algoalpha" in name_norm:
            from trading_engine.indicators.algoalpha_breakout import AlgoAlphaBreakoutIndicator
            ind = AlgoAlphaBreakoutIndicator()
        elif "supertrend" in name_norm:
            from trading_engine.indicators.supertrend_cloud import SupertrendCloudIndicator
            ind = SupertrendCloudIndicator()
        else:
            global _ADAPTIVE_TREND_PRO_CLS
            if "_ADAPTIVE_TREND_PRO_CLS" not in globals() or _ADAPTIVE_TREND_PRO_CLS is None:
                from trading_engine.indicators.adaptive_trend_pro import AdaptiveTrendProIndicator
                _ADAPTIVE_TREND_PRO_CLS = AdaptiveTrendProIndicator
            ind = _ADAPTIVE_TREND_PRO_CLS()

        if hasattr(ind, "enable_hammer_reversal"):
            if hasattr(ind.enable_hammer_reversal, "value"):
                ind.enable_hammer_reversal.value = enable_hammer_reversal
            else:
                ind.enable_hammer_reversal = enable_hammer_reversal
        return ind

    @staticmethod
    def calculate_buyer_supplier_strength(open_p: float, high_p: float, low_p: float, close_p: float) -> Tuple[float, float, str]:
        """
        Calculates Buyer Strength % and Supplier (Seller) Strength % for a candle,
        and identifies candlestick patterns (HAMMER, INVERTED_HAMMER, or NEUTRAL).
        """
        rng = max(high_p - low_p, 0.05)
        body = abs(close_p - open_p)
        upper_wick = high_p - max(open_p, close_p)
        lower_wick = min(open_p, close_p) - low_p

        # Buyer Pressure: lower shadow absorption + bullish body + close near high
        buyer_push = (close_p - low_p) + lower_wick + max(0.0, close_p - open_p)
        # Supplier Pressure: upper shadow rejection + bearish body + close near low
        supplier_push = (high_p - close_p) + upper_wick + max(0.0, open_p - close_p)

        total_push = max(buyer_push + supplier_push, 0.01)
        buyer_pct = round((buyer_push / total_push) * 100.0, 1)
        supplier_pct = round(100.0 - buyer_pct, 1)

        pattern = "NEUTRAL"
        # Regular Hammer (downside-up): long lower wick >= 1.6x body, lower wick >= 38% range
        if lower_wick >= 1.6 * body and lower_wick >= 0.38 * rng and buyer_pct >= 52.0:
            pattern = "HAMMER"
        # Inverted Hammer (upside-down): long upper wick >= 1.6x body, upper wick >= 38% range
        elif upper_wick >= 1.6 * body and upper_wick >= 0.38 * rng and supplier_pct >= 52.0:
            pattern = "INVERTED_HAMMER"

        return buyer_pct, supplier_pct, pattern

    def _check_hammer_reversal(
        self,
        prev_bar: Any,
        curr_bar: Any,
        pos_type: str
    ) -> Tuple[bool, str]:
        """
        Detects candlestick reversal against active position:
        1. If BUY_CE: prev candle was Inverted Hammer (upside-down hammer) and current candle is bearish (Suppliers > Buyers) -> Exit!
        2. If BUY_PE: prev candle was Regular Hammer (downside-up hammer) and current candle is bullish (Buyers > Suppliers) -> Exit!
        """
        p_o, p_h, p_l, p_c = float(prev_bar["open"]), float(prev_bar["high"]), float(prev_bar["low"]), float(prev_bar["close"])
        c_o, c_h, c_l, c_c = float(curr_bar["open"]), float(curr_bar["high"]), float(curr_bar["low"]), float(curr_bar["close"])

        p_buyer, p_supplier, p_pat = self.calculate_buyer_supplier_strength(p_o, p_h, p_l, p_c)
        c_buyer, c_supplier, _ = self.calculate_buyer_supplier_strength(c_o, c_h, c_l, c_c)

        if pos_type == "CE":
            if p_pat == "INVERTED_HAMMER" and (c_c < c_o or c_c < p_l) and c_supplier >= 52.0:
                reason = f"REVERSAL_INVERTED_HAMMER (Suppliers {c_supplier:.0f}% vs Buyers {c_buyer:.0f}%)"
                return True, reason
        elif pos_type == "PE":
            if p_pat == "HAMMER" and (c_c > c_o or c_c > p_h) and c_buyer >= 52.0:
                reason = f"REVERSAL_HAMMER (Buyers {c_buyer:.0f}% vs Suppliers {c_supplier:.0f}%)"
                return True, reason

        return False, ""

    def run(
        self,
        spot_df: pd.DataFrame,
        symbol: str = "NIFTY",
        option_filter: str = "ATM",            # "CE", "PE", or "ATM" (scans both)
        initial_capital: float = 20000.0,
        strategy_name: str = "Adaptive Trend Pro",
        enable_hammer_reversal: Optional[bool] = None,
        enable_sr_zones: Optional[bool] = None,
        risk_factor: Optional[float] = None,
        blocked_time_slots: Optional[List[Tuple[str, str]]] = None,
        max_trades_per_day: Optional[int] = None,
        sizing_mode: str = "conservative"
    ) -> Tuple[List[TradeRecord], List[Dict[str, Any]]]:
        """
        Executes dual-channel option simulation across trading days.
        """
        if spot_df.empty:
            return [], []

        allow_hammer_rev = self.enable_hammer_reversal if enable_hammer_reversal is None else enable_hammer_reversal
        allow_sr = self.enable_sr_zones if enable_sr_zones is None else enable_sr_zones
        active_risk_factor = self.risk_factor if risk_factor is None else risk_factor
        active_blocked_slots = blocked_time_slots if blocked_time_slots is not None else self.blocked_time_slots
        active_max_trades = self.max_trades_per_day if max_trades_per_day is None else max_trades_per_day

        # Ensure timestamp column is datetime
        df = spot_df.copy()
        df["ts"] = pd.to_datetime(df["timestamp"])
        df["time_str"] = df["ts"].dt.strftime("%H:%M:%S")

        # Filter strictly to official NSE intraday session: 09:15:00 to 15:30:00
        df = df[(df["time_str"] >= "09:15:00") & (df["time_str"] <= "15:30:00")].sort_values("ts").reset_index(drop=True)
        if df.empty:
            return [], []

        current_capital = float(initial_capital)
        equity_curve: List[Dict[str, Any]] = [
            {"time": str(df.iloc[0]["ts"])[:16], "equity": round(current_capital, 2)}
        ]
        trades: List[TradeRecord] = []
        opt_filter_norm = (option_filter or "ATM").upper().strip()

        # Group chronologically by trading date
        unique_dates = df["ts"].dt.date.unique()

        for cur_date_idx, cur_date in enumerate(unique_dates):
            day_df = df[df["ts"].dt.date == cur_date].reset_index(drop=True)
            if day_df.empty:
                continue

            day_str = str(cur_date)
            day_trades_count = 0

            # Smart Option Detector for dynamic ATM detection every N minutes
            detector = SmartOptionDetector(
                interval_minutes=self.atm_interval_minutes,
                underlying=symbol
            )

            # Dual-Channel Indicator Instances (dedicated for CE and PE option charts)
            ce_indicator = self._resolve_indicator(strategy_name, allow_hammer_rev)
            pe_indicator = self._resolve_indicator(strategy_name, allow_hammer_rev)
            ce_sr_manager = SRZoneManager(default_risk_factor=active_risk_factor)
            pe_sr_manager = SRZoneManager(default_risk_factor=active_risk_factor)
            ce_candles_history: List[Dict[str, Any]] = []
            pe_candles_history: List[Dict[str, Any]] = []
            ms_engine = MarketStructureEngine(pivot_lookback=3)
            spot_candles_history: List[Dict[str, Any]] = []
            ib_high: Optional[float] = None
            ib_low: Optional[float] = None

            # Rolling Spot Technical State (EMA 9, 20, 50 & RSI 14)
            spot_ema_9: Optional[float] = None
            spot_ema_20: Optional[float] = None
            spot_ema_50: Optional[float] = None
            spot_gains_14: List[float] = []
            spot_losses_14: List[float] = []
            prev_spot_close_val: Optional[float] = None

            active_position: Optional[Position] = None
            active_contract: Optional[OptionContract] = None
            prev_option_close: Optional[float] = None
            last_loss_exit_dt: Optional[datetime] = None

            day_total_bars = len(day_df)
            for idx in range(day_total_bars):
                row = day_df.iloc[idx]
                bar_ts = row["ts"]
                time_hm = bar_ts.strftime("%H:%M") if hasattr(bar_ts, "strftime") else str(bar_ts)[11:16]
                spot_close = float(row["close"])
                spot_bar = Bar(
                    timestamp=bar_ts,
                    open=float(row["open"]),
                    high=float(row["high"]),
                    low=float(row["low"]),
                    close=spot_close,
                    volume=float(row.get("volume", 1000.0))
                )

                spot_dict = {
                    "time": str(bar_ts),
                    "open": spot_bar.open,
                    "high": spot_bar.high,
                    "low": spot_bar.low,
                    "close": spot_bar.close,
                    "volume": spot_bar.volume
                }
                spot_candles_history.append(spot_dict)
                struct_event = ms_engine.update(spot_dict, spot_candles_history)

                # Update rolling Spot EMAs
                if spot_ema_9 is None:
                    spot_ema_9 = spot_close
                    spot_ema_20 = spot_close
                    spot_ema_50 = spot_close
                else:
                    spot_ema_9 = (2.0 / 10.0) * spot_close + (8.0 / 10.0) * spot_ema_9
                    spot_ema_20 = (2.0 / 21.0) * spot_close + (19.0 / 21.0) * spot_ema_20
                    spot_ema_50 = (2.0 / 51.0) * spot_close + (49.0 / 51.0) * spot_ema_50

                # Update rolling Spot RSI 14
                spot_rsi_val = 50.0
                if prev_spot_close_val is not None:
                    diff = spot_close - prev_spot_close_val
                    gain = max(diff, 0.0)
                    loss = max(-diff, 0.0)
                    spot_gains_14.append(gain)
                    spot_losses_14.append(loss)
                    if len(spot_gains_14) > 14:
                        spot_gains_14.pop(0)
                        spot_losses_14.pop(0)
                    avg_gain = sum(spot_gains_14) / len(spot_gains_14)
                    avg_loss = sum(spot_losses_14) / len(spot_losses_14)
                    if avg_loss == 0.0:
                        spot_rsi_val = 100.0
                    else:
                        rs = avg_gain / avg_loss
                        spot_rsi_val = 100.0 - (100.0 / (1.0 + rs))
                prev_spot_close_val = spot_close

                if time_hm <= "10:05":
                    ib_high = max(float(r["high"]) for r in spot_candles_history)
                    ib_low = min(float(r["low"]) for r in spot_candles_history)

                # ── Step 1: Smart Option Detector (ATM Detection) ────────────────
                if active_position is None:
                    strike_changed, new_ce, new_pe = detector.evaluate(
                        spot_price=spot_close,
                        current_dt=bar_ts,
                        force_redetect=(detector.current_ce_contract is None)
                    )
                    if strike_changed:
                        ce_indicator = self._resolve_indicator(strategy_name, allow_hammer_rev)
                        pe_indicator = self._resolve_indicator(strategy_name, allow_hammer_rev)
                        ce_sr_manager.reset()
                        pe_sr_manager.reset()
                        ce_candles_history = []
                        pe_candles_history = []

                        # Warm up indicators with cached option candles if available
                        past_ce = self.feeder.get_contract_day_candles(new_ce.symbol, day_str)
                        if past_ce:
                            sub_ce = [c for c in past_ce if str(c.get("time", ""))[-5:] <= time_hm]
                            ce_candles_history = sub_ce[-40:]
                            if len(ce_candles_history) >= 10:
                                ce_indicator.warm_up(ce_candles_history)

                        past_pe = self.feeder.get_contract_day_candles(new_pe.symbol, day_str)
                        if past_pe:
                            sub_pe = [c for c in past_pe if str(c.get("time", ""))[-5:] <= time_hm]
                            pe_candles_history = sub_pe[-40:]
                            if len(pe_candles_history) >= 10:
                                pe_indicator.warm_up(pe_candles_history)

                cur_ce_contract = detector.current_ce_contract
                cur_pe_contract = detector.current_pe_contract

                # Obtain 1-minute option bars for both channels
                ce_bar = self.feeder.get_or_synthesize_option_bar(spot_bar, cur_ce_contract) if cur_ce_contract else None
                pe_bar = self.feeder.get_or_synthesize_option_bar(spot_bar, cur_pe_contract) if cur_pe_contract else None

                if ce_bar:
                    ce_bar_dict = ce_bar.to_dict()
                    ce_candles_history.append(ce_bar_dict)
                    if len(ce_candles_history) >= 6:
                        ce_sr_manager.update_from_bar(ce_candles_history, ce_bar_dict)
                if pe_bar:
                    pe_bar_dict = pe_bar.to_dict()
                    pe_candles_history.append(pe_bar_dict)
                    if len(pe_candles_history) >= 6:
                        pe_sr_manager.update_from_bar(pe_candles_history, pe_bar_dict)

                # ── Step 2: Active Position Management (CONCURRENCY LOCK) ────────
                # If a trade is active, NO OTHER TRADE CAN BE ENTERED (Not even in PE/CE).
                if active_position is not None and active_contract is not None:
                    opt_bar = self.feeder.get_or_synthesize_option_bar(
                        spot_bar=spot_bar,
                        contract=active_contract,
                        prev_option_close=prev_option_close
                    )
                    if opt_bar is None:
                        if prev_option_close is not None and prev_option_close > 0.0:
                            opt_bar = Bar(
                                timestamp=spot_bar.timestamp,
                                open=prev_option_close,
                                high=prev_option_close,
                                low=prev_option_close,
                                close=prev_option_close,
                                volume=0.0
                            )
                        else:
                            continue
                    prev_option_close = opt_bar.close

                    smart_exit_reason: Optional[str] = None

                    # A. Indicator active trade evaluation
                    active_trade_dict = {
                        "entry_price": active_position.entry_premium,
                        "entry_spot": active_position.entry_spot,
                        "entry_time": active_position.entry_time,
                        "trade_side": active_position.option_type
                    }
                    opt_bar_dict = opt_bar.to_dict()
                    opt_bar_dict["timestamp"] = str(bar_ts)

                    hist_candles = ce_candles_history if active_position.option_type == "CE" else pe_candles_history

                    # B. Candlestick Hammer Reversal Guard (Supplier/Buyer Dominance)
                    if allow_hammer_rev and not smart_exit_reason and len(day_df.iloc[: idx + 1]) >= 2:
                        rev_triggered, rev_msg = self._check_hammer_reversal(
                            prev_bar=day_df.iloc[idx - 1],
                            curr_bar=day_df.iloc[idx],
                            pos_type=active_position.option_type
                        )
                        if rev_triggered:
                            smart_exit_reason = rev_msg

                    # B2. S/R Zone Reversal & 2-Candle Breakdown Engine
                    if allow_sr and not smart_exit_reason:
                        active_sr = ce_sr_manager if active_position.option_type == "CE" else pe_sr_manager
                        sr_exit, sr_reason, _ = active_sr.evaluate_active_trade(
                            active_trade=active_trade_dict,
                            history=hist_candles,
                            curr_bar=opt_bar_dict,
                            risk_factor=active_risk_factor
                        )
                        if sr_exit:
                            smart_exit_reason = sr_reason

                    # B3. Market Structure Invalidation / CHoCH Reversal Exit
                    if not smart_exit_reason and struct_event is not None:
                        if active_position.option_type == "CE" and struct_event.event_type in ["CHOCH_BEAR", "BOS_BEAR"]:
                            smart_exit_reason = f"STRUCTURE_FLIP_{struct_event.event_type}"
                        elif active_position.option_type == "PE" and struct_event.event_type in ["CHOCH_BULL", "BOS_BULL"]:
                            smart_exit_reason = f"STRUCTURE_FLIP_{struct_event.event_type}"

                    # C. Lifecycle evaluation (SL, Target, Trailing SL, 15:15 Auto Square-Off)
                    closed_trade = self.lifecycle.evaluate_bar(
                        position=active_position,
                        opt_bar_high=opt_bar.high,
                        opt_bar_low=opt_bar.low,
                        opt_bar_close=opt_bar.close,
                        timestamp=bar_ts,
                        smart_exit_reason=smart_exit_reason
                    )

                    if closed_trade is not None:
                        trades.append(closed_trade)
                        current_capital += closed_trade.net_pnl
                        equity_curve.append({
                            "time": str(bar_ts)[:16],
                            "equity": round(current_capital, 2)
                        })
                        if closed_trade.net_pnl < 0:
                            last_loss_exit_dt = pd.to_datetime(bar_ts)

                        active_position = None
                        active_contract = None
                        prev_option_close = None
                        detector.reset()  # Redetect ATM strike on next minute

                    # Concurrency Lock: continue to next bar; no new entries allowed while active
                    continue

                # ── Step 3: Dual-Channel Signal Scanning (Directly on Option Charts) ──
                # If flat, evaluate incoming signals directly on option candles
                if active_position is None:
                    # 1. End-of-day cutoff & strict 15:15-15:30 block
                    if time_hm >= "15:15" or time_hm >= self.entry_cutoff_time:
                        continue

                    # 1A. Daily Trade Frequency Guard (Enforces target 3-4 trades/day, 66-88/month)
                    if active_max_trades > 0 and day_trades_count >= active_max_trades:
                        continue

                    # 1B. User-Configured Blocked Time Slots Guard
                    if active_blocked_slots:
                        is_slot_blocked = False
                        for b_start, b_end in active_blocked_slots:
                            if b_start <= time_hm <= b_end:
                                is_slot_blocked = True
                                break
                        if is_slot_blocked:
                            continue

                    # 1B. Post-Loss Cooldown Guard: Prevents revenge whipsaws within N minutes of a loss
                    if self.loss_cooldown_mins > 0 and last_loss_exit_dt is not None:
                        curr_dt = pd.to_datetime(bar_ts)
                        elapsed_mins = (curr_dt - last_loss_exit_dt).total_seconds() / 60.0
                        if elapsed_mins < self.loss_cooldown_mins:
                            continue

                    # 2. Last 50-Minutes Market Trend Filter:
                    # If 50m trend is bullish -> block PE; If bearish -> block CE
                    block_pe = False
                    block_ce = False
                    if time_hm >= "14:40":
                        past_50_spot = float(day_df.iloc[max(0, idx - 50)]["close"])
                        diff_50m = spot_close - past_50_spot
                        if diff_50m > 3.0:
                            block_pe = True
                        elif diff_50m < -3.0:
                            block_ce = True

                    # 2B. Round-Number Resistance / Exhaustion Guard:
                    # Protects against buying Calls directly into a major Round Number (100 / 50) after an extended rally
                    # unless confirmed by 2 consecutive closes strictly above the level.
                    dist_to_100 = spot_close % 100.0
                    if (dist_to_100 <= 6.0 or dist_to_100 >= 96.0) and not block_ce:
                        round_level = round(spot_close / 100.0) * 100.0
                        day_open = float(day_df.iloc[0]["open"])
                        rally_pts = spot_close - day_open
                        if rally_pts > 80.0:
                            prev_spot_close = float(day_df.iloc[idx - 1]["close"]) if idx > 0 else 0.0
                            if not (spot_close > round_level and prev_spot_close > round_level):
                                block_ce = True

                    # 2C. Sideways EMA Compression Chop Filter (Compressions < 4.0 points on Nifty)
                    if spot_ema_20 is not None and spot_ema_50 is not None:
                        if abs(spot_ema_20 - spot_ema_50) < 4.0 and len(spot_candles_history) >= 30:
                            continue

                    # 2D. Spot Market Structure and Trend Qualification (BOS / CHoCH + EMA 20 + RSI 14)
                    is_spot_bull = (ms_engine.market_trend == "BULLISH") and (spot_close > spot_ema_20) and (spot_rsi_val >= 50.0)
                    is_spot_bear = (ms_engine.market_trend == "BEARISH") and (spot_close < spot_ema_20) and (spot_rsi_val <= 50.0)

                    # 3. Channel 1: Scan Call Option (CE) Chart
                    ce_triggers = False
                    sig_ce = None
                    if len(ce_candles_history) >= 5 and not block_ce and ce_bar and is_spot_bull:
                        b_pct, s_pct, _ = self.calculate_buyer_supplier_strength(ce_bar.open, ce_bar.high, ce_bar.low, ce_bar.close)
                        if ce_bar.close >= ce_bar.open and b_pct >= 50.0:
                            ce_triggers = True

                        # User S/R Proximity Center Zone (f <= 0) Rule
                        if ce_triggers and allow_sr:
                            allowed_sr, f_val, _ = ce_sr_manager.evaluate_sr_entry_eligibility(
                                current_price=ce_bar.close,
                                history=ce_candles_history,
                                curr_bar=ce_bar.to_dict(),
                                allow_breakout=True
                            )
                            if not allowed_sr:
                                ce_triggers = False

                    # 4. Channel 2: Scan Put Option (PE) Chart
                    pe_triggers = False
                    sig_pe = None
                    if len(pe_candles_history) >= 5 and not block_pe and pe_bar and is_spot_bear:
                        b_pct, s_pct, _ = self.calculate_buyer_supplier_strength(pe_bar.open, pe_bar.high, pe_bar.low, pe_bar.close)
                        if pe_bar.close >= pe_bar.open and b_pct >= 50.0:
                            pe_triggers = True

                        # User S/R Proximity Center Zone (f <= 0) Rule
                        if pe_triggers and allow_sr:
                            allowed_sr, f_val, _ = pe_sr_manager.evaluate_sr_entry_eligibility(
                                current_price=pe_bar.close,
                                history=pe_candles_history,
                                curr_bar=pe_bar.to_dict(),
                                allow_breakout=True
                            )
                            if not allowed_sr:
                                pe_triggers = False

                    # User option filter enforcement
                    is_dual_mode = opt_filter_norm in ["ATM", "BOTH", "AUTO", "DYNAMIC", "ALL", ""]
                    if not is_dual_mode:
                        if opt_filter_norm in ["CE", "CALL"]:
                            pe_triggers = False
                        if opt_filter_norm in ["PE", "PUT"]:
                            ce_triggers = False

                    # 5. Arbitration & Execution
                    chosen_entry = None
                    if ce_triggers and not pe_triggers:
                        chosen_entry = ("BUY_CE", cur_ce_contract, ce_bar, sig_ce)
                    elif pe_triggers and not ce_triggers:
                        chosen_entry = ("BUY_PE", cur_pe_contract, pe_bar, sig_pe)
                    elif ce_triggers and pe_triggers:
                        if spot_rsi_val >= 50.0:
                            chosen_entry = ("BUY_CE", cur_ce_contract, ce_bar, sig_ce)
                        else:
                            chosen_entry = ("BUY_PE", cur_pe_contract, pe_bar, sig_pe)

                    if chosen_entry and chosen_entry[1] is not None:
                        action_side, contract, chosen_opt_bar, sig_obj = chosen_entry
                        opt_type = "CE" if action_side == "BUY_CE" else "PE"

                        # Institutional Order Block Retest / Pullback Entry:
                        # Rather than chasing the top-tick close of the breakout candle,
                        # enter on the 30% pullback retest into the signal candle
                        c_range = max(chosen_opt_bar.high - chosen_opt_bar.low, 1.0)
                        pullback_px = chosen_opt_bar.close - (c_range * 0.30)
                        entry_premium = round(max(chosen_opt_bar.low, pullback_px), 2)

                        # Determine stop loss distance for sizing calculation
                        temp_sl_pts = round(min(8.0, max(2.5, entry_premium * 0.20)), 2) if entry_premium <= 40.0 else 8.0

                        # Position sizing for Option Buying
                        lots, units, cap_req = self.sizer.calculate_option_buy_lots(
                            capital=current_capital,
                            option_premium=entry_premium,
                            contract=contract,
                            sizing_mode=sizing_mode,
                            stop_loss_points=temp_sl_pts
                        )

                        if lots > 0:
                            slices, effective_px = self.sizer.generate_slices(
                                n_lots=lots,
                                contract=contract,
                                base_price=entry_premium,
                                side="BUY"
                            )

                            # Adaptive Risk & Targets scaled to Option Premium
                            if effective_px <= 40.0:
                                # Low-premium / near-expiry options (<= ₹40)
                                tgt_pts = round(max(3.5, effective_px * 0.30), 2)  # 30% gain target
                                sl_pts = round(min(8.0, max(2.5, effective_px * 0.20)), 2)  # 20% risk stop
                            else:
                                # Standard / higher-premium options (> ₹40)
                                tgt_pts = 16.0
                                sl_pts = 8.0

                            initial_sl = max(1.0, round(effective_px - sl_pts, 2))
                            target_px = round(effective_px + tgt_pts, 2)

                            active_position = Position(
                                id=f"POS_{len(trades)+1:03d}",
                                underlying=symbol,
                                contract_symbol=contract.symbol,
                                action=OrderAction.BUY_CE if opt_type == "CE" else OrderAction.BUY_PE,
                                option_type=opt_type,
                                strike=contract.strike,
                                lots=lots,
                                units=units,
                                entry_time=bar_ts,
                                entry_premium=effective_px,
                                entry_spot=spot_close,
                                initial_sl_premium=initial_sl,
                                current_sl_premium=initial_sl,
                                target_premium=target_px,
                                peak_premium=effective_px,
                                trailing_stage=0,
                                strategy_name=strategy_name,
                                slices=slices,
                                minutes_held=0,
                                metadata={
                                    "probability": "90%",
                                    "channel": opt_type,
                                    "entry_reason": sig_obj.extra.get("reason") if sig_obj else "Option Chart Trend Breakout"
                                }
                            )
                            active_contract = contract
                            day_trades_count += 1
                            prev_option_close = effective_px

                            # Hook S/R Zone Manager on Trade Entry
                            active_sr = ce_sr_manager if opt_type == "CE" else pe_sr_manager
                            active_sr.on_trade_entry(
                                entry_price=effective_px,
                                timestamp=str(bar_ts),
                                history=ce_candles_history if opt_type == "CE" else pe_candles_history
                            )

            # End-of-day square-off: never carry intraday options overnight
            if active_position is not None and active_contract is not None:
                last_row = day_df.iloc[-1]
                last_ts = last_row["ts"]
                exit_close = prev_option_close if (prev_option_close is not None and prev_option_close > 0.0) else active_position.entry_premium
                forced_close = self.lifecycle.close_position(
                    position=active_position,
                    exit_price=exit_close,
                    exit_reason="AUTO_SQUARE_OFF_15_15",
                    timestamp=last_ts
                )
                trades.append(forced_close)
                current_capital += forced_close.net_pnl
                equity_curve.append({
                    "time": str(last_ts)[:16],
                    "equity": round(current_capital, 2)
                })

        return trades, equity_curve
