"""
Paper Trade Daemon & Real-Time Autonomous Signal Engine
────────────────────────────────────────────────────────
Continuously runs in the background on the live WebSocket stream (0 REST calls).
Features:
  - Dynamic ATM Option Contract Resolution (CE & PE) via OptionResolver.
  - Zero-lag sub-second Stop Loss, Target, and Trailing execution against live ticks.
  - Evaluates Dual XGBoost + Macro (AI-Trader 78L) & Quant Core indicator strategies.
  - Institutional 7% capital compounding risk sizing.
  - Real-time Buyer/Seller strength calculation from order depth.
  - Instant Force Market Exit endpoint.
  - 3-Way Subtab Performance Breakdown (Total Details, Monthly Details, Today's Details).
  - Multi-tier persistence: local JSON (data/paper_trade_state.json) + TimescaleDB (trade_log).
"""

import os
import sys
import json
import math
import uuid
import logging
from datetime import datetime, date, time as dt_time, timezone, timedelta
from pathlib import Path
from typing import Dict, Any, Optional, List, Tuple
import pandas as pd
import numpy as np

# Add project root to path
ROOT_DIR = Path(__file__).resolve().parent.parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from trading_engine.quant_core.option_resolver import OptionResolver
from trading_engine.quant_core.contracts import Bar, Signal, Position, TradeRecord
from trading_engine.quant_core.costs import CostCalculator
from trading_engine.quant_core.strategies import UnifiedAITraderStrategy, UnifiedSignal

logger = logging.getLogger("paper_trade_daemon")
logger.setLevel(logging.INFO)
if not logger.handlers:
    _sh = logging.StreamHandler(sys.stdout)
    _sh.setLevel(logging.INFO)
    _sh.setFormatter(logging.Formatter("[%(levelname)s] PaperDaemon: %(message)s"))
    logger.addHandler(_sh)

DATA_DIR = ROOT_DIR / "data"
STATE_FILE = DATA_DIR / "paper_trade_state.json"


class PaperTradeDaemon:
    """
    Autonomous Paper Trading Engine.
    Operates 24/7 in the background with zero REST API overhead.
    """

    _instance = None

    @classmethod
    def get_instance(cls):
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def __init__(self):
        DATA_DIR.mkdir(parents=True, exist_ok=True)

        # Configuration (synchronized with Config Cogwheel modal)
        self.strategy = "dual_xgboost_macro"

        # Dual-Fund Segregated Capital Architecture (ICICI Direct style separate accounts)
        self.equity_initial_capital = 20000.0
        self.equity_capital = 20000.0
        self.equity_blocked_capital = 0.0
        self.equity_available_capital = 20000.0

        self.commodity_initial_capital = 50000.0
        self.commodity_capital = 50000.0
        self.commodity_blocked_capital = 0.0
        self.commodity_available_capital = 50000.0

        # Cooldown trackers (15-min cooldown between exits and entries)
        self.last_crude_exit_time: Optional[datetime] = None
        self.last_equity_exit_time: Optional[datetime] = None

        self.symbol = "NIFTY"
        self.max_trades_per_day = 8
        self.max_trades_index = 4
        self.max_trades_commodity = 4
        # Dynamic Commodity Risk & Trade Sizing Rules (Profitable = ∞ | 2-Loss = 2.5h Pause | 3-Loss = Stop)
        self.commodity_dynamic_trade_limit: bool = True
        self.commodity_pause_hours: float = 2.5
        self.commodity_loss_streak_pause: int = 2
        self.commodity_loss_streak_stop: int = 3
        self.blocked_time_slots = [["09:15", "10:05"]]
        self.risk_pct = 0.07  # 7% compounding risk per trade
        self.sl_pct = 0.30    # 30% Stop Loss
        self.tgt_pct = 0.50   # 50% Profit Target

        # Real-time state
        self.is_running = True
        self._active_trade: Optional[Dict[str, Any]] = None
        self.active_trades: Dict[str, Dict[str, Any]] = {}
        self.trade_history: List[Dict[str, Any]] = []

        # Real-time market metrics
        self.current_spot: float = 24347.35
        self.current_future: float = 24376.90
        self.prev_tick_price: float = 24347.35
        self.forming_bars: Dict[str, Dict[str, Any]] = {}
        self.bar_history: Dict[str, List[Dict[str, Any]]] = {}

        # Real-time Indices Statistics (NIFTY 50, NIFTY-I, BANKNIFTY, SENSEX)
        self.indices: Dict[str, Dict[str, Any]] = {
            "NIFTY": {
                "name": "NIFTY 50",
                "symbol": "NIFTY",
                "segment": "NSE Cash",
                "badge": "SPOT",
                "ltp": 24347.35,
                "atm_strike": 24350,
                "strike_step": 50,
                "change_pts": 386.75,
                "change_pct": 1.61,
                "trend": "UP",
                "high": 24391.95,
                "low": 24296.30,
                "open": 24378.05,
                "prev_close": 23960.60,
                "updated_at": datetime.now().isoformat()
            },
            "NIFTY-I": {
                "name": "NIFTY-I (Near Fut)",
                "symbol": "NIFTY-I",
                "segment": "NSE Futures",
                "badge": "FUTURES",
                "ltp": 24376.90,
                "atm_strike": 24350,
                "strike_step": 50,
                "change_pts": 406.20,
                "change_pct": 1.69,
                "trend": "UP",
                "high": 24388.00,
                "low": 24311.00,
                "open": 24320.00,
                "prev_close": 23970.70,
                "oi": 13602355,
                "volume": 1195870,
                "updated_at": datetime.now().isoformat()
            },
            "BANKNIFTY": {
                "name": "BANK NIFTY",
                "symbol": "BANKNIFTY",
                "segment": "NSE Cash",
                "badge": "SPOT",
                "ltp": 52100.00,
                "atm_strike": 52100,
                "strike_step": 100,
                "change_pts": 450.00,
                "change_pct": 0.87,
                "trend": "UP",
                "high": 52250.00,
                "low": 51900.00,
                "open": 52000.00,
                "prev_close": 51650.00,
                "updated_at": datetime.now().isoformat()
            },
            "SENSEX": {
                "name": "BSE SENSEX",
                "symbol": "SENSEX",
                "segment": "BSE Cash",
                "badge": "SPOT",
                "ltp": 78688.70,
                "atm_strike": 78700,
                "strike_step": 100,
                "change_pts": 1387.30,
                "change_pct": 1.79,
                "trend": "UP",
                "high": 78922.44,
                "low": 78520.56,
                "open": 78910.94,
                "prev_close": 77301.40,
                "updated_at": datetime.now().isoformat()
            },
            "CRUDEOIL": {
                "name": "Crude Oil (Near Fut)",
                "symbol": "CRUDEOIL",
                "segment": "MCX Commodity",
                "badge": "COMMODITY",
                "ltp": 8551.00,
                "atm_strike": 8550,
                "strike_step": 50,
                "change_pts": 187.00,
                "change_pct": 2.24,
                "trend": "UP",
                "high": 8594.00,
                "low": 8191.00,
                "open": 8219.00,
                "prev_close": 8364.00,
                "updated_at": datetime.now().isoformat()
            },
            "NATURALGAS": {
                "name": "Natural Gas (Near Fut)",
                "symbol": "NATURALGAS",
                "segment": "MCX Commodity",
                "badge": "COMMODITY",
                "ltp": 282.40,
                "atm_strike": 280,
                "strike_step": 5,
                "change_pts": 2.35,
                "change_pct": 0.84,
                "trend": "UP",
                "high": 289.10,
                "low": 277.50,
                "open": 280.05,
                "prev_close": 280.05,
                "updated_at": datetime.now().isoformat()
            }
        }

        # Buyer/Seller Strength Meter
        self.buyer_strength: float = 52.0
        self.seller_strength: float = 48.0

        # Unified AI-Trader Strategy Evaluator (Shared across Backtest, Paper, Live)
        self.strategy_evaluator = UnifiedAITraderStrategy.get_instance()
        self.crude_model = self.strategy_evaluator.crude_model
        self.crude_feature_cols = self.strategy_evaluator.crude_feature_cols

        # Load saved state from disk & bootstrap today's candle history
        self.load_state()
        self.bootstrap_bars_from_db()

    @property
    def capital(self) -> float:
        return round(self.equity_capital + self.commodity_capital, 2)

    @capital.setter
    def capital(self, val: float):
        pass

    @property
    def initial_capital(self) -> float:
        return round(self.equity_initial_capital + self.commodity_initial_capital, 2)

    @initial_capital.setter
    def initial_capital(self, val: float):
        pass

    @property
    def blocked_capital(self) -> float:
        return round(self.equity_blocked_capital + self.commodity_blocked_capital, 2)

    @blocked_capital.setter
    def blocked_capital(self, val: float):
        pass

    @property
    def available_capital(self) -> float:
        return round(self.equity_available_capital + self.commodity_available_capital, 2)

    @available_capital.setter
    def available_capital(self, val: float):
        pass

    @property
    def active_trade(self) -> Optional[Dict[str, Any]]:
        """Returns the primary active trade (equity preferred if both active, else commodity)."""
        if not hasattr(self, "active_trades") or not self.active_trades:
            return getattr(self, "_active_trade", None)
        if "equity" in self.active_trades:
            return self.active_trades["equity"]
        if "commodity" in self.active_trades:
            return self.active_trades["commodity"]
        return list(self.active_trades.values())[0]

    @active_trade.setter
    def active_trade(self, val: Optional[Dict[str, Any]]):
        if not hasattr(self, "active_trades"):
            self.active_trades = {}
        self._active_trade = val
        if val is None:
            self.active_trades.clear()
        else:
            seg = "commodity" if self._is_commodity_trade(val) else "equity"
            self.active_trades[seg] = val

    def _is_commodity_trade(self, t: Optional[Dict[str, Any]]) -> bool:
        """Determines if a trade belongs to the MCX Commodity fund ledger."""
        if not t:
            return False
        comm_symbols = ("CRUDEOIL", "NATURALGAS", "GOLD", "GOLDM", "SILVER", "SILVERM")
        return (
            t.get("segment") == "MCX Commodity"
            or t.get("symbol") in comm_symbols
            or any(s in str(t.get("contract", "")).upper() for s in ("CRUDE", "NATURALGAS", "GOLD", "SILVER"))
        )

    def _load_crude_model(self):
        """Loads the specialized Crude Oil XGBoost macro model if present."""
        try:
            import joblib
            model_p = ROOT_DIR / "models" / "saved" / "crude_macro_model.pkl"
            if model_p.exists():
                pkg = joblib.load(model_p)
                self.crude_model = pkg.get("model")
                self.crude_feature_cols = pkg.get("feature_cols", [])
                logger.info("PaperTradeDaemon: Loaded specialized Crude Oil XGBoost Macro Model.")
        except Exception as e:
            logger.debug(f"PaperTradeDaemon: Crude model load skipped: {e}")

    def get_today_trade_counts(self, today_str: Optional[str] = None) -> Tuple[int, int, int]:
        """
        Returns (index_trades_count, commodity_trades_count, total_trades_count) for today.
        - Index Trades: NIFTY, BANKNIFTY, SENSEX Options and Equities.
        - Commodity Trades: CRUDEOIL, NATURALGAS, GOLD, SILVER Futures and Options.
        """
        if not today_str:
            today_str = datetime.now().strftime("%Y-%m-%d")
        today_trades = [t for t in self.trade_history if t.get("date") == today_str]
        comm_count = sum(1 for t in today_trades if self._is_commodity_trade(t))
        index_count = len(today_trades) - comm_count
        return index_count, comm_count, len(today_trades)

    def check_commodity_dynamic_trade_allowed(
        self,
        now: Optional[datetime] = None,
        today_str: Optional[str] = None
    ) -> Tuple[bool, str, Dict[str, Any]]:
        """
        Evaluates dynamic commodity trade rules:
        1. If the day is profitable (today net P&L > 0) -> Unlimited trades allowed.
        2. If 2 trades consecutively result in loss -> Take 2-3 hours pause (default 2.5h), then try again.
        3. If 3rd trade also results in loss -> Stop commodity trading for the day.
        """
        if now is None:
            now = datetime.now()
        if today_str is None:
            today_str = now.strftime("%Y-%m-%d")

        # Static fallback if dynamic is disabled
        if not self.commodity_dynamic_trade_limit:
            idx_cnt, comm_cnt, tot_cnt = self.get_today_trade_counts(today_str)
            if self.max_trades_commodity > 0 and comm_cnt >= self.max_trades_commodity:
                return False, f"Static commodity trade limit reached ({comm_cnt}/{self.max_trades_commodity})", {
                    "dynamic_enabled": False,
                    "allowed": False,
                    "day_profitable": False,
                    "today_comm_net": 0.0,
                    "today_comm_count": comm_cnt,
                    "consecutive_losses": 0,
                    "is_paused": False,
                    "pause_remaining_minutes": 0.0,
                    "is_stopped_for_day": False,
                    "status_label": f"STATIC CAP ({comm_cnt}/{self.max_trades_commodity})",
                    "status_message": f"Static trade cap of {self.max_trades_commodity} reached"
                }
            if self.max_trades_per_day > 0 and tot_cnt >= self.max_trades_per_day:
                return False, f"Static total trade limit reached ({tot_cnt}/{self.max_trades_per_day})", {
                    "dynamic_enabled": False,
                    "allowed": False,
                    "day_profitable": False,
                    "today_comm_net": 0.0,
                    "today_comm_count": comm_cnt,
                    "consecutive_losses": 0,
                    "is_paused": False,
                    "pause_remaining_minutes": 0.0,
                    "is_stopped_for_day": False,
                    "status_label": f"TOTAL CAP ({tot_cnt}/{self.max_trades_per_day})",
                    "status_message": f"Total daily trade cap of {self.max_trades_per_day} reached"
                }
            return True, "Static commodity trade allowed", {
                "dynamic_enabled": False,
                "allowed": True,
                "day_profitable": False,
                "today_comm_net": 0.0,
                "today_comm_count": comm_cnt,
                "consecutive_losses": 0,
                "is_paused": False,
                "pause_remaining_minutes": 0.0,
                "is_stopped_for_day": False,
                "status_label": f"STATIC ({comm_cnt}/{self.max_trades_commodity})",
                "status_message": "Within static trade limit"
            }

        today_trades = [t for t in self.trade_history if t.get("date") == today_str]
        today_comm_trades = [t for t in today_trades if self._is_commodity_trade(t)]
        comm_cnt = len(today_comm_trades)

        if comm_cnt == 0:
            return True, "Allowed: First commodity trade of the day", {
                "dynamic_enabled": True,
                "allowed": True,
                "day_profitable": False,
                "today_comm_net": 0.0,
                "today_comm_count": 0,
                "consecutive_losses": 0,
                "is_paused": False,
                "pause_remaining_minutes": 0.0,
                "is_stopped_for_day": False,
                "status_label": "DYNAMIC ACTIVE (0 trades)",
                "status_message": "Ready for commodity entry"
            }

        today_comm_net = round(sum(float(t.get("net", t.get("net_pnl", 0.0))) for t in today_comm_trades), 2)
        day_profitable = (today_comm_net > 0)

        # Count consecutive losses at the end of today's commodity trades
        consecutive_losses = 0
        last_loss_exit_dt: Optional[datetime] = None
        for t in reversed(today_comm_trades):
            net_val = float(t.get("net", t.get("net_pnl", 0.0)))
            if net_val <= 0:
                consecutive_losses += 1
                if last_loss_exit_dt is None:
                    ex_val = t.get("exit_time")
                    if isinstance(ex_val, datetime):
                        last_loss_exit_dt = ex_val
                    elif isinstance(ex_val, str) and ":" in ex_val:
                        try:
                            parts = [int(p) for p in ex_val.split(":")]
                            last_loss_exit_dt = now.replace(
                                hour=parts[0],
                                minute=parts[1],
                                second=parts[2] if len(parts) > 2 else 0,
                                microsecond=0
                            )
                        except Exception:
                            pass
            else:
                break

        # Fallback to self.last_crude_exit_time if available and recent
        if consecutive_losses > 0 and self.last_crude_exit_time and isinstance(self.last_crude_exit_time, datetime):
            if last_loss_exit_dt is None or self.last_crude_exit_time > last_loss_exit_dt:
                last_loss_exit_dt = self.last_crude_exit_time

        # Rule 3: If 3rd trade is also giving loss then stop for the day
        if consecutive_losses >= self.commodity_loss_streak_stop:
            return False, f"🛑 Commodity trading stopped for today: {consecutive_losses} consecutive losses reached", {
                "dynamic_enabled": True,
                "allowed": False,
                "day_profitable": day_profitable,
                "today_comm_net": today_comm_net,
                "today_comm_count": comm_cnt,
                "consecutive_losses": consecutive_losses,
                "is_paused": False,
                "pause_remaining_minutes": 0.0,
                "is_stopped_for_day": True,
                "status_label": f"STOPPED TODAY ({consecutive_losses} Losses)",
                "status_message": f"Capital protection: stopped for the day after {consecutive_losses} consecutive losses"
            }

        # Rule 2: If 2 trades consecutively getting loss, pause 2-3 hours (default 2.5h), then try again
        if consecutive_losses == self.commodity_loss_streak_pause:
            pause_mins = self.commodity_pause_hours * 60.0
            elapsed_mins = 0.0
            if last_loss_exit_dt:
                elapsed_mins = max(0.0, (now - last_loss_exit_dt).total_seconds() / 60.0)

            if elapsed_mins < pause_mins:
                rem_mins = round(pause_mins - elapsed_mins, 1)
                pause_until_dt = (last_loss_exit_dt or now) + timedelta(minutes=pause_mins)
                pause_until_str = pause_until_dt.strftime("%H:%M")
                return False, f"⏸️ Commodity trading paused: 2 consecutive losses. Paused until {pause_until_str} ({rem_mins}m left)", {
                    "dynamic_enabled": True,
                    "allowed": False,
                    "day_profitable": day_profitable,
                    "today_comm_net": today_comm_net,
                    "today_comm_count": comm_cnt,
                    "consecutive_losses": consecutive_losses,
                    "is_paused": True,
                    "pause_remaining_minutes": rem_mins,
                    "pause_until": pause_until_str,
                    "is_stopped_for_day": False,
                    "status_label": f"PAUSED ({int(rem_mins)}m left | {pause_until_str})",
                    "status_message": f"In {self.commodity_pause_hours}h cooling pause after 2 consecutive losses. Resumes at {pause_until_str} IST"
                }
            else:
                # Pause has expired -> Try again!
                return True, f"Allowed: Cooling pause of {self.commodity_pause_hours}h completed. Trying trade #3", {
                    "dynamic_enabled": True,
                    "allowed": True,
                    "day_profitable": day_profitable,
                    "today_comm_net": today_comm_net,
                    "today_comm_count": comm_cnt,
                    "consecutive_losses": consecutive_losses,
                    "is_paused": False,
                    "pause_remaining_minutes": 0.0,
                    "is_stopped_for_day": False,
                    "status_label": "RESUMED (Trying Trade #3)",
                    "status_message": "Cooling pause completed after 2 losses; permitted to try trade #3"
                }

        # Rule 1: If the day is profitable, no limit!
        if day_profitable:
            return True, f"Allowed: Day is profitable (+₹{today_comm_net:,.2f}) - Unlimited commodity trades", {
                "dynamic_enabled": True,
                "allowed": True,
                "day_profitable": True,
                "today_comm_net": today_comm_net,
                "today_comm_count": comm_cnt,
                "consecutive_losses": consecutive_losses,
                "is_paused": False,
                "pause_remaining_minutes": 0.0,
                "is_stopped_for_day": False,
                "status_label": f"UNLIMITED (+₹{today_comm_net:,.0f} 🟢)",
                "status_message": "Day is profitable. Unlimited commodity trades permitted."
            }

        # Day is not yet profitable, but streak < 2 (0 or 1 loss) -> Allowed
        return True, f"Allowed: Dynamic commodity active ({consecutive_losses} loss streak, P&L: ₹{today_comm_net:,.2f})", {
            "dynamic_enabled": True,
            "allowed": True,
            "day_profitable": False,
            "today_comm_net": today_comm_net,
            "today_comm_count": comm_cnt,
            "consecutive_losses": consecutive_losses,
            "is_paused": False,
            "pause_remaining_minutes": 0.0,
            "is_stopped_for_day": False,
            "status_label": f"DYNAMIC ({comm_cnt} trades | ₹{today_comm_net:,.0f})",
            "status_message": "Active dynamic commodity trading"
        }

    def bootstrap_bars_from_db(self):
        """Loads today's completed 1-minute candles from TimescaleDB on engine startup."""
        try:
            from trading_engine.core.keyvault import get_secret
            import psycopg2
            conn = psycopg2.connect(
                dbname=get_secret("TIMESCALE_DB") or "trading",
                user=get_secret("TIMESCALE_USER") or "postgres",
                password=get_secret("TIMESCALE_PASSWORD") or "postgres",
                host=get_secret("TIMESCALE_HOST") or "localhost",
                port=5432,
                connect_timeout=3
            )
            cur = conn.cursor()
            today_str = datetime.now().strftime("%Y-%m-%d")
            cur.execute("""
                SELECT time_bucket('1 minute', timestamp) AS bucket,
                       first(price, timestamp) as o,
                       max(price) as h,
                       min(price) as l,
                       last(price, timestamp) as c,
                       max(volume) as v
                FROM tick_data
                WHERE timestamp >= %s AND symbol IN ('NIFTY', 'NIFTY 50')
                GROUP BY bucket ORDER BY bucket ASC
            """, (f"{today_str} 09:15:00+05:30",))
            rows = cur.fetchall()
            conn.close()

            if rows:
                if "NIFTY" not in self.bar_history:
                    self.bar_history["NIFTY"] = []
                for r in rows:
                    b_ts = r[0].strftime("%Y-%m-%d %H:%M:00") if hasattr(r[0], "strftime") else str(r[0])
                    self.bar_history["NIFTY"].append({
                        "timestamp": b_ts,
                        "open": float(r[1]),
                        "high": float(r[2]),
                        "low": float(r[3]),
                        "close": float(r[4]),
                        "volume": int(r[5] or 0),
                        "symbol": "NIFTY"
                    })
                logger.info(f"PaperTradeDaemon: Bootstrapped {len(self.bar_history['NIFTY'])} 1-min NIFTY bars from TimescaleDB.")
        except Exception as e:
            logger.debug(f"PaperTradeDaemon: DB bar bootstrap skipped: {e}")

        # Bootstrap recent CRUDEOIL 1-minute bars from historical archive for immediate 1H-trend & 30-bar indicators
        try:
            crude_csv = DATA_DIR / "historical" / "CRUDEOIL_1min_3years.csv"
            if crude_csv.exists() and "CRUDEOIL" not in self.bar_history:
                df_c = pd.read_csv(crude_csv).tail(500)
                self.bar_history["CRUDEOIL"] = []
                for _, row in df_c.iterrows():
                    self.bar_history["CRUDEOIL"].append({
                        "timestamp": str(row["datetime"]),
                        "open": float(row["open"]),
                        "high": float(row["high"]),
                        "low": float(row["low"]),
                        "close": float(row["close"]),
                        "volume": int(row.get("volume", 0) or 0),
                        "symbol": "CRUDEOIL"
                    })
                logger.info(f"PaperTradeDaemon: Bootstrapped {len(self.bar_history['CRUDEOIL'])} 1-min CRUDEOIL bars from archive.")
        except Exception as e:
            logger.debug(f"PaperTradeDaemon: CRUDE bar bootstrap skipped: {e}")

    def load_state(self):
        """Restores persistent paper trading portfolio from disk with dual-account segregation."""
        if STATE_FILE.exists():
            try:
                with open(STATE_FILE, "r") as f:
                    data = json.load(f)
                    self.strategy = data.get("strategy", self.strategy)
                    self.max_trades_index = int(data.get("max_trades_index", 4))
                    self.max_trades_commodity = int(data.get("max_trades_commodity", 4))
                    self.commodity_dynamic_trade_limit = bool(data.get("commodity_dynamic_trade_limit", True))
                    self.commodity_pause_hours = float(data.get("commodity_pause_hours", 2.5))
                    saved_total = int(data.get("max_trades_per_day", 8))
                    if saved_total <= 4:
                        saved_total = self.max_trades_index + self.max_trades_commodity
                    self.max_trades_per_day = saved_total
                    self.blocked_time_slots = data.get("blocked_time_slots", self.blocked_time_slots)
                    self.active_trades = {}
                    if "active_trades" in data and isinstance(data["active_trades"], list):
                        for at in data["active_trades"]:
                            if isinstance(at, dict):
                                seg = "commodity" if self._is_commodity_trade(at) else "equity"
                                self.active_trades[seg] = at
                    elif "active_trade" in data and isinstance(data["active_trade"], dict):
                        at = data["active_trade"]
                        seg = "commodity" if self._is_commodity_trade(at) else "equity"
                        self.active_trades[seg] = at
                    self.trade_history = data.get("trade_history", [])

                    # Dual-Account Segregation (ICICI Direct style separate ledgers)
                    if "equity_capital" in data and "commodity_capital" in data:
                        self.equity_initial_capital = float(data.get("equity_initial_capital", 20000.0))
                        self.equity_capital = float(data.get("equity_capital", 20000.0))
                        self.equity_blocked_capital = float(data.get("equity_blocked_capital", 0.0))
                        self.equity_available_capital = float(data.get("equity_available_capital", self.equity_capital - self.equity_blocked_capital))

                        self.commodity_initial_capital = float(data.get("commodity_initial_capital", 50000.0))
                        self.commodity_capital = float(data.get("commodity_capital", 50000.0))
                        self.commodity_blocked_capital = float(data.get("commodity_blocked_capital", 0.0))
                        self.commodity_available_capital = float(data.get("commodity_available_capital", self.commodity_capital - self.commodity_blocked_capital))
                    else:
                        # Legacy state file migration: separate into 20k Stock fund and 50k Crude fund
                        self.equity_initial_capital = 20000.0
                        self.commodity_initial_capital = 50000.0
                        eq_pnl = sum(t.get("net", 0.0) for t in self.trade_history if not self._is_commodity_trade(t))
                        comm_pnl = sum(t.get("net", 0.0) for t in self.trade_history if self._is_commodity_trade(t))
                        self.equity_capital = round(self.equity_initial_capital + eq_pnl, 2)
                        self.equity_blocked_capital = 0.0
                        self.equity_available_capital = self.equity_capital
                        self.commodity_capital = round(self.commodity_initial_capital + comm_pnl, 2)
                        self.commodity_blocked_capital = 0.0
                        self.commodity_available_capital = self.commodity_capital

                    # Auto-normalize legacy trade records for backwards compatibility
                    for t in self.trade_history:
                        if "entry_price" not in t and "entry_rate" in t:
                            t["entry_price"] = t["entry_rate"]
                        if "entry_rate" not in t and "entry_price" in t:
                            t["entry_rate"] = t["entry_price"]
                        if "exit_price" not in t and "exit_rate" in t:
                            t["exit_price"] = t["exit_rate"]
                        if "exit_rate" not in t and "exit_price" in t:
                            t["exit_rate"] = t["exit_price"]
                        if "quantity" not in t and "units" in t:
                            t["quantity"] = t["units"]
                        if "units" not in t and "quantity" in t:
                            t["units"] = t["quantity"]
                        if "strategy" not in t and "indicator" in t:
                            t["strategy"] = t["indicator"]
                    if self.active_trade:
                        if "entry_price" not in self.active_trade and "entry_rate" in self.active_trade:
                            self.active_trade["entry_price"] = self.active_trade["entry_rate"]
                        if "entry_rate" not in self.active_trade and "entry_price" in self.active_trade:
                            self.active_trade["entry_rate"] = self.active_trade["entry_price"]
                        if "quantity" not in self.active_trade and "units" in self.active_trade:
                            self.active_trade["quantity"] = self.active_trade["units"]
                        if "units" not in self.active_trade and "quantity" in self.active_trade:
                            self.active_trade["units"] = self.active_trade["quantity"]

                    logger.info(
                        f"PaperTradeDaemon: Restored {len(self.trade_history)} trades. "
                        f"Stock Bal: ₹{self.equity_capital:,.2f} | Crude Bal: ₹{self.commodity_capital:,.2f} | "
                        f"Total Capital: ₹{self.capital:,.2f}"
                    )
            except Exception as e:
                logger.error(f"PaperTradeDaemon: Error loading state: {e}")

    def reset_state(self, equity_capital: float = 20000.0, commodity_capital: float = 50000.0):
        """Clears paper trading ledger and resets segregated ICICI account balances."""
        self.equity_initial_capital = float(equity_capital)
        self.equity_capital = float(equity_capital)
        self.equity_blocked_capital = 0.0
        self.equity_available_capital = float(equity_capital)

        self.commodity_initial_capital = float(commodity_capital)
        self.commodity_capital = float(commodity_capital)
        self.commodity_blocked_capital = 0.0
        self.commodity_available_capital = float(commodity_capital)

        self.active_trades.clear()
        self._active_trade = None
        self.trade_history = []
        self.last_crude_exit_time = None
        self.last_equity_exit_time = None
        self.save_state()
        logger.info(
            f"🔄 [PAPER TRADE RESET] Ledger cleared. Capital reset to Stock: ₹{equity_capital:,.2f} | "
            f"Crude: ₹{commodity_capital:,.2f} (Total: ₹{self.capital:,.2f})"
        )

    def save_state(self):
        """Persists paper trading state to disk with segregated fund accounting."""
        try:
            payload = {
                "strategy": self.strategy,
                # Aggregated Balances
                "capital": round(self.capital, 2),
                "total_capital": round(self.capital, 2),
                "blocked_capital": round(self.blocked_capital, 2),
                "available_capital": round(self.available_capital, 2),
                "initial_capital": round(self.initial_capital, 2),
                # Segregated ICICI Accounts
                "equity_capital": round(self.equity_capital, 2),
                "equity_initial_capital": round(self.equity_initial_capital, 2),
                "equity_blocked_capital": round(self.equity_blocked_capital, 2),
                "equity_available_capital": round(self.equity_available_capital, 2),
                "commodity_capital": round(self.commodity_capital, 2),
                "commodity_initial_capital": round(self.commodity_initial_capital, 2),
                "commodity_blocked_capital": round(self.commodity_blocked_capital, 2),
                "commodity_available_capital": round(self.commodity_available_capital, 2),
                # Limits & Controls
                "max_trades_per_day": self.max_trades_per_day,
                "max_trades_total": self.max_trades_per_day,
                "max_trades_index": self.max_trades_index,
                "max_trades_commodity": self.max_trades_commodity,
                "commodity_dynamic_trade_limit": self.commodity_dynamic_trade_limit,
                "commodity_pause_hours": self.commodity_pause_hours,
                "blocked_time_slots": self.blocked_time_slots,
                "active_trade": self.active_trade,
                "active_trades": list(self.active_trades.values()) if hasattr(self, "active_trades") else ([] if not self.active_trade else [self.active_trade]),
                "trade_history": self.trade_history,
                "updated_at": datetime.now().isoformat()
            }
            with open(STATE_FILE, "w") as f:
                json.dump(payload, f, indent=2, default=str)
        except Exception as e:
            logger.error(f"PaperTradeDaemon: Error saving state: {e}")

    def update_config(self, params: Dict[str, Any]):
        """Updates trading parameters from config modal."""
        if "strategy" in params and params["strategy"]:
            self.strategy = str(params["strategy"])
        if "equity_capital" in params and float(params["equity_capital"]) > 0:
            new_eq = float(params["equity_capital"])
            if not self.trade_history and not self.active_trade:
                self.equity_capital = new_eq
                self.equity_initial_capital = new_eq
        if "commodity_capital" in params and float(params["commodity_capital"]) > 0:
            new_comm = float(params["commodity_capital"])
            if not self.trade_history and not self.active_trade:
                self.commodity_capital = new_comm
                self.commodity_initial_capital = new_comm
        if "max_trades_index" in params:
            self.max_trades_index = int(params["max_trades_index"])
        if "max_trades_commodity" in params:
            comm_val = params["max_trades_commodity"]
            if str(comm_val).lower() == "dynamic":
                self.commodity_dynamic_trade_limit = True
            else:
                try:
                    self.max_trades_commodity = int(comm_val)
                except Exception:
                    pass
        if "commodity_dynamic_trade_limit" in params:
            self.commodity_dynamic_trade_limit = bool(params["commodity_dynamic_trade_limit"])
        if "commodity_pause_hours" in params and float(params["commodity_pause_hours"]) > 0:
            self.commodity_pause_hours = float(params["commodity_pause_hours"])
        if "max_trades_per_day" in params:
            self.max_trades_per_day = int(params["max_trades_per_day"])
        elif "maxTradesPerDay" in params:
            self.max_trades_per_day = int(params["maxTradesPerDay"])
        elif "max_trades_index" in params or "max_trades_commodity" in params:
            self.max_trades_per_day = self.max_trades_index + self.max_trades_commodity
        if "blocked_time_slots" in params:
            self.blocked_time_slots = params["blocked_time_slots"]
        elif "blockedTimeSlots" in params:
            self.blocked_time_slots = params["blockedTimeSlots"]
        self.save_state()
        return self.get_config()

    def get_config(self) -> Dict[str, Any]:
        return {
            "strategy": self.strategy,
            "capital": self.capital,
            "total_capital": self.capital,
            "blocked_capital": self.blocked_capital,
            "available_capital": self.available_capital,
            "initial_capital": self.initial_capital,
            "equity_capital": round(self.equity_capital, 2),
            "equity_available_capital": round(self.equity_available_capital, 2),
            "equity_blocked_capital": round(self.equity_blocked_capital, 2),
            "equity_initial_capital": round(self.equity_initial_capital, 2),
            "commodity_capital": round(self.commodity_capital, 2),
            "commodity_available_capital": round(self.commodity_available_capital, 2),
            "commodity_blocked_capital": round(self.commodity_blocked_capital, 2),
            "commodity_initial_capital": round(self.commodity_initial_capital, 2),
            "max_trades_per_day": self.max_trades_per_day,
            "max_trades_total": self.max_trades_per_day,
            "max_trades_index": self.max_trades_index,
            "max_trades_commodity": self.max_trades_commodity,
            "commodity_dynamic_trade_limit": self.commodity_dynamic_trade_limit,
            "commodity_pause_hours": self.commodity_pause_hours,
            "blocked_time_slots": self.blocked_time_slots,
            "symbol": self.symbol,
            "segment": "NIFTY & CRUDEOIL (Separate ICICI Ledgers)",
            "contract": "Dynamic ATM (CE & PE)"
        }

    # ── Sub-Second Live Tick Processing ──────────────────────────────────────

    def on_tick(self, tick: Dict[str, Any]):
        """
        Invoked on every incoming tick from broker WebSocket.
        1. Updates spot & futures benchmarks.
        2. Calculates live Buyer/Seller strength.
        3. Monitors active position SL / Target / Trailing lock.
        4. Accumulates 1-minute candle bars.
        """
        symbol = tick.get("symbol", "")
        ltp = float(tick.get("price") or tick.get("last") or 0.0)
        if ltp <= 0:
            return

        now = datetime.now()

        # 1. Update benchmark spot / futures & Index Statistics
        canon = symbol
        if symbol in ("NIFTY 50", "NSE NIFTY"):
            canon = "NIFTY"
        elif symbol in ("NIFTY-I", "NIFTY I", "NIFTY_I", "NIFTY FUT", "NIFTY26NOVFUT"):
            canon = "NIFTY-I"
        elif symbol in ("NIFTY BANK", "CNXBAN"):
            canon = "BANKNIFTY"
        elif symbol in ("BSESEN", "BSE:SENSEX", "BSE SENSEX"):
            canon = "SENSEX"
        elif symbol in ("CRUDEOIL", "CRUDE", "MCX:CRUDEOIL") or "573422" in symbol:
            canon = "CRUDEOIL"
        elif symbol in ("NATURALGAS", "NATGAS", "MCX:NATURALGAS") or "574319" in symbol:
            canon = "NATURALGAS"

        if canon == "NIFTY":
            self.current_spot = ltp
        elif canon == "NIFTY-I":
            self.current_future = ltp

        if hasattr(self, "indices") and canon in self.indices:
            idx = self.indices[canon]
            idx["ltp"] = round(ltp, 2)
            step = idx.get("strike_step", 50)
            idx["atm_strike"] = int(round(ltp / step) * step)

            if "high" in tick and float(tick["high"]) > 0:
                idx["high"] = round(float(tick["high"]), 2)
            else:
                idx["high"] = max(idx.get("high", ltp), ltp)

            if "low" in tick and float(tick["low"]) > 0:
                idx["low"] = round(float(tick["low"]), 2)
            else:
                idx["low"] = min(idx.get("low", ltp), ltp)

            if "open" in tick and float(tick["open"]) > 0:
                idx["open"] = round(float(tick["open"]), 2)

            close_val = float(tick.get("close") or idx.get("prev_close") or 0.0)
            if close_val > 0:
                idx["prev_close"] = round(close_val, 2)
                change_pts = round(ltp - close_val, 2)
                change_pct = round((change_pts / close_val) * 100.0, 2)
            else:
                change_pts = round(float(tick.get("change") or 0.0), 2)
                change_pct = 0.0

            idx["change_pts"] = change_pts
            idx["change_pct"] = change_pct
            idx["trend"] = "UP" if change_pts >= 0 else "DOWN"

            if "volume" in tick:
                idx["volume"] = int(tick["volume"])
            if "oi" in tick:
                idx["oi"] = int(tick["oi"])
            idx["updated_at"] = now.isoformat()

        # 2. Update Buyer / Seller Strength from depth or tick momentum
        self._update_strength_meter(tick, ltp)

        # 3. Active Position Sub-Second Evaluation
        if self.active_trade:
            self._evaluate_active_position_tick(symbol, ltp, now)

        # 4. 1-Minute Candle Accumulation
        if canon in ("NIFTY", "NIFTY-I", "CRUDEOIL", "NATURALGAS"):
            self._accumulate_minute_bar(canon, ltp, int(tick.get("volume", 0) or 0), now)

    def _update_strength_meter(self, tick: Dict[str, Any], ltp: float):
        """Calculates buyer vs seller strength from order book depth or tick pressure."""
        buy_qty = float(tick.get("buy_qty") or tick.get("bid_qty") or 0)
        sell_qty = float(tick.get("sell_qty") or tick.get("ask_qty") or 0)

        # Check nested depth if present
        depth = tick.get("depth")
        if isinstance(depth, dict):
            bids = depth.get("buy") or depth.get("bids") or []
            asks = depth.get("sell") or depth.get("asks") or []
            if bids and asks:
                buy_qty = sum(float(b.get("quantity", 0)) for b in bids)
                sell_qty = sum(float(a.get("quantity", 0)) for a in asks)

        if buy_qty > 0 and sell_qty > 0:
            total = buy_qty + sell_qty
            ratio = (buy_qty / total) * 100.0
            # Exponential smooth
            self.buyer_strength = round(0.85 * self.buyer_strength + 0.15 * ratio, 1)
            self.seller_strength = round(100.0 - self.buyer_strength, 1)
        else:
            # Momentum fallback
            delta = ltp - self.prev_tick_price
            shift = 1.5 if delta > 0 else (-1.5 if delta < 0 else 0)
            self.buyer_strength = round(max(15.0, min(85.0, self.buyer_strength + shift)), 1)
            self.seller_strength = round(100.0 - self.buyer_strength, 1)

        self.prev_tick_price = ltp

    def _evaluate_active_position_tick(self, symbol: str, ltp: float, now: datetime):
        if not hasattr(self, "active_trades") or not self.active_trades:
            return

        for seg_key in list(self.active_trades.keys()):
            pos = self.active_trades.get(seg_key)
            if not pos:
                continue
            contract_sym = pos.get("contract", "")
            pos_sym = pos.get("symbol", "")

            # MCX Commodity Position Evaluation
            if pos.get("segment") == "MCX Commodity" or pos_sym in ("CRUDEOIL", "NATURALGAS"):
                if symbol == contract_sym or symbol == pos_sym:
                    underlying_px = ltp
                    pos["current_underlying"] = underlying_px
                    trend = pos.get("trend", "BULLISH")
                    u_entry = pos.get("entry_underlying", pos["entry_price"])

                    if trend in ("BULLISH", "LONG", "CALL"):
                        gain_u = round(underlying_px - u_entry, 2)
                        pts_opt = round(gain_u * 0.55, 2)

                        # Trailing Trigger (+22 pts underlying move locks +3 pts option profit)
                        if not pos.get("trail_active") and gain_u >= pos.get("trail_trig_u", 22.0):
                            pos["trail_active"] = True
                            pos["sl_underlying"] = round(u_entry + pos.get("trail_lock_u", 5.5), 2)
                            pos["sl"] = round(pos["entry_price"] + pos.get("opt_trail_pts", 3.0), 2)
                            logger.info(f"🛡️ [CRUDE TRAILING SL ACTIVATED] Underlying gain +{gain_u:.1f} pts >= {pos.get('trail_trig_u', 22.0)} pts. SL secured at ₹{pos['sl_underlying']:.2f} (+3 pts opt)")

                        # Target Hit (+45 pts underlying move -> +25 pts option gain)
                        if underlying_px >= pos.get("target_underlying", u_entry + 45.0):
                            self._close_active_trade(
                                reason="Target Hit (+25 pts Option)",
                                exit_price=pos["entry_price"] + pos.get("opt_tgt_pts", 25.0),
                                exit_time=now,
                                opt_pts=pos.get("opt_tgt_pts", 25.0),
                                trade_to_close=pos
                            )
                            continue
                        # Stop Loss / Trailed SL Hit
                        elif underlying_px <= pos.get("sl_underlying", u_entry - 22.0):
                            opt_exit_pts = pos.get("opt_trail_pts", 3.0) if pos.get("trail_active") else -pos.get("opt_sl_pts", 12.0)
                            reason = "Trailing SL Hit (+3 pts Option)" if pos.get("trail_active") else "Stop Loss Hit (-12 pts Option)"
                            self._close_active_trade(
                                reason=reason,
                                exit_price=pos["entry_price"] + opt_exit_pts,
                                exit_time=now,
                                opt_pts=opt_exit_pts,
                                trade_to_close=pos
                            )
                            continue
                    else:  # Short (BEARISH / PUT)
                        gain_u = round(u_entry - underlying_px, 2)
                        pts_opt = round(gain_u * 0.55, 2)

                        if not pos.get("trail_active") and gain_u >= pos.get("trail_trig_u", 22.0):
                            pos["trail_active"] = True
                            pos["sl_underlying"] = round(u_entry - pos.get("trail_lock_u", 5.5), 2)
                            pos["sl"] = round(pos["entry_price"] + pos.get("opt_trail_pts", 3.0), 2)
                            logger.info(f"🛡️ [CRUDE TRAILING SL ACTIVATED] Underlying gain +{gain_u:.1f} pts >= {pos.get('trail_trig_u', 22.0)} pts. SL secured at ₹{pos['sl_underlying']:.2f} (+3 pts opt)")

                        if underlying_px <= pos.get("target_underlying", u_entry - 45.0):
                            self._close_active_trade(
                                reason="Target Hit (+25 pts Option)",
                                exit_price=pos["entry_price"] + pos.get("opt_tgt_pts", 25.0),
                                exit_time=now,
                                opt_pts=pos.get("opt_tgt_pts", 25.0),
                                trade_to_close=pos
                            )
                            continue
                        elif underlying_px >= pos.get("sl_underlying", u_entry + 22.0):
                            opt_exit_pts = pos.get("opt_trail_pts", 3.0) if pos.get("trail_active") else -pos.get("opt_sl_pts", 12.0)
                            reason = "Trailing SL Hit (+3 pts Option)" if pos.get("trail_active") else "Stop Loss Hit (-12 pts Option)"
                            self._close_active_trade(
                                reason=reason,
                                exit_price=pos["entry_price"] + opt_exit_pts,
                                exit_time=now,
                                opt_pts=opt_exit_pts,
                                trade_to_close=pos
                            )
                            continue

                    # Commodity Auto Square-Off at 23:25 IST
                    if now.hour == 23 and now.minute >= 25:
                        self._close_active_trade(
                            reason="23:25 EOD Square-Off",
                            exit_price=pos["entry_price"] + pts_opt,
                            exit_time=now,
                            opt_pts=pts_opt,
                            trade_to_close=pos
                        )
                        continue

                    pos["current_price"] = round(pos["entry_price"] + pts_opt, 2)
                    pos["points_gained"] = pts_opt
                    pos["points"] = pts_opt
                    pos["unrealized_pnl"] = round(pts_opt * pos["units"], 2)
                    pos["buyer_strength"] = self.buyer_strength
                    pos["seller_strength"] = self.seller_strength
            else:
                # Option LTP update for Equity / NIFTY
                opt_ltp = pos.get("current_price", pos["entry_price"])
                if symbol == contract_sym:
                    opt_ltp = ltp
                elif symbol in ("NIFTY", "NIFTY 50"):
                    spot_diff = ltp - pos.get("spot_at_entry", ltp)
                    delta = 0.55 if pos.get("trend") == "BULLISH" else -0.55
                    opt_ltp = max(5.0, round(pos["entry_price"] + spot_diff * delta, 2))
                else:
                    continue

                pos["current_price"] = round(opt_ltp, 2)
                pos["unrealized_pnl"] = round((opt_ltp - pos["entry_price"]) * pos["units"], 2)
                pos["points_gained"] = round(opt_ltp - pos["entry_price"], 2)
                pos["points"] = pos["points_gained"]
                pos["buyer_strength"] = self.buyer_strength
                pos["seller_strength"] = self.seller_strength

                # Check Stop Loss Hit
                if opt_ltp <= pos["sl"]:
                    self._close_active_trade(reason="Stop Loss Hit", exit_price=pos["sl"], exit_time=now, trade_to_close=pos)
                    continue

                # Check Target Hit
                if opt_ltp >= pos["target"]:
                    self._close_active_trade(reason="Target Hit", exit_price=pos["target"], exit_time=now, trade_to_close=pos)
                    continue

                # Auto Square-Off at 15:15 IST
                if now.hour == 15 and now.minute >= 15:
                    self._close_active_trade(reason="15:15 Auto Square-Off", exit_price=opt_ltp, exit_time=now, trade_to_close=pos)
                    continue

    def _accumulate_minute_bar(self, symbol: str, ltp: float, volume: int, now: datetime):
        """Rolls incoming ticks into 1-minute OHLCV candles."""
        minute_key = now.strftime("%Y-%m-%d %H:%M:00")
        current_bar = self.forming_bars.get(symbol)

        if not current_bar:
            self.forming_bars[symbol] = {
                "timestamp": minute_key,
                "open": ltp, "high": ltp, "low": ltp, "close": ltp,
                "volume": volume, "symbol": symbol
            }
        elif current_bar["timestamp"] != minute_key:
            # Finalize previous 1-minute candle
            closed_bar = current_bar.copy()
            self._on_minute_candle_close(symbol, closed_bar, now)

            # Fill any missed minutes with flat candles to maintain clean time series on tick dropouts
            try:
                prev_dt = datetime.strptime(closed_bar["timestamp"], "%Y-%m-%d %H:%M:00")
                curr_dt = datetime.strptime(minute_key, "%Y-%m-%d %H:%M:00")
                gap_mins = int((curr_dt - prev_dt).total_seconds() // 60)
                if 1 < gap_mins <= 5:
                    for m in range(1, gap_mins):
                        g_ts = (prev_dt + timedelta(minutes=m)).strftime("%Y-%m-%d %H:%M:00")
                        flat_b = {
                            "timestamp": g_ts,
                            "open": closed_bar["close"],
                            "high": closed_bar["close"],
                            "low": closed_bar["close"],
                            "close": closed_bar["close"],
                            "volume": 0,
                            "symbol": symbol
                        }
                        if symbol in self.bar_history:
                            self.bar_history[symbol].append(flat_b)
                            if len(self.bar_history[symbol]) > 1000:
                                self.bar_history[symbol].pop(0)
            except Exception:
                pass

            # Start new candle
            self.forming_bars[symbol] = {
                "timestamp": minute_key,
                "open": ltp, "high": ltp, "low": ltp, "close": ltp,
                "volume": volume, "symbol": symbol
            }
        else:
            current_bar["high"] = max(current_bar["high"], ltp)
            current_bar["low"] = min(current_bar["low"], ltp)
            current_bar["close"] = ltp
            current_bar["volume"] = max(current_bar["volume"], volume)

    def _on_minute_candle_close(self, symbol: str, bar: Dict[str, Any], now: datetime):
        """Invoked when a 1-minute bar closes. Evaluates strategy entry signals."""
        if symbol not in self.bar_history:
            self.bar_history[symbol] = []
        self.bar_history[symbol].append(bar)
        if len(self.bar_history[symbol]) > 1000:
            self.bar_history[symbol].pop(0)

        # Log candle close for operational visibility
        if symbol in ("NIFTY", "NIFTY 50", "CRUDEOIL", "NATURALGAS"):
            logger.info(f"📊 [1-MIN BAR CLOSED] {symbol} @ {now.strftime('%H:%M:%S')} | O:{bar['open']} H:{bar['high']} L:{bar['low']} C:{bar['close']} | History: {len(self.bar_history[symbol])} bars")

        # Handle Commodity Entry Evaluation
        if symbol in ("CRUDEOIL", "NATURALGAS"):
            # Commodity Market Hours Gating: MCX market must be open (09:00 - 23:30 IST)
            if not self.is_market_open("MCX Commodity"):
                return
            # Cutoff: no new commodity entries after 23:15 IST
            if now.hour > 23 or (now.hour == 23 and now.minute >= 15):
                return

            # Segment-specific active position check: do not enter if commodity trade is already active
            if hasattr(self, "active_trades") and "commodity" in self.active_trades:
                return

            # Cooldown Gating: at least 15 minutes since last crude exit
            if self.last_crude_exit_time:
                mins_since = (now - self.last_crude_exit_time).total_seconds() / 60.0
                if mins_since < 15.0:
                    return

            # Dynamic Commodity Trade Gating (Profit: unlimited | 2-Loss: 2.5h pause | 3-Loss: stop)
            today_str = now.strftime("%Y-%m-%d")
            allowed, reason, comm_status = self.check_commodity_dynamic_trade_allowed(now, today_str)
            if not allowed:
                logger.debug(f"PaperDaemon: Commodity trade gated: {reason}")
                return

            self._evaluate_commodity_strategy_signal(symbol, now)
            return

        # Only evaluate strategy entries on primary benchmark candle close (avoids duplicate signals on futures candle close)
        if symbol not in ("NIFTY", "NIFTY 50"):
            return

        # Pre-Market & Regular Hours Gating: NSE market must be open (09:15 - 15:30 IST)
        if not self.is_market_open("NSE Cash"):
            return

        # Segment-specific active position check: do not enter if equity trade is already active
        if hasattr(self, "active_trades") and "equity" in self.active_trades:
            return

        # Gating 1: Blocked Time Slots (e.g. 09:15 to 10:05 opening volatility noise filter)
        hm = now.strftime("%H:%M")
        for slot in self.blocked_time_slots:
            if isinstance(slot, (list, tuple)) and len(slot) >= 2:
                if slot[0] <= hm < slot[1]:
                    return
            elif isinstance(slot, dict) and "start" in slot and "end" in slot:
                if slot["start"] <= hm < slot["end"]:
                    return

        # Gating 2: Market Close Gating (no new trades after 15:10 IST)
        if now.hour > 15 or (now.hour == 15 and now.minute >= 10):
            return

        # Gating 3: Cooldown Gating (at least 15 minutes since last equity exit)
        if self.last_equity_exit_time:
            mins_since = (now - self.last_equity_exit_time).total_seconds() / 60.0
            if mins_since < 15.0:
                return

        # Gating 4: Max Trades Per Day Gating (Index-specific limit + total cap)
        today_str = now.strftime("%Y-%m-%d")
        idx_cnt, comm_cnt, tot_cnt = self.get_today_trade_counts(today_str)
        if self.max_trades_index > 0 and idx_cnt >= self.max_trades_index:
            return
        if self.max_trades_per_day > 0 and tot_cnt >= self.max_trades_per_day:
            return

        # Evaluate Strategy Setup
        self._evaluate_strategy_signal(now)

    def _evaluate_strategy_signal(self, now: datetime):
        """Evaluates Dual XGBoost + Macro high-edge setups via Unified Strategy Evaluator."""
        nifty_bars = self.bar_history.get("NIFTY") or self.bar_history.get("NIFTY 50") or []
        fut_bars = self.bar_history.get("NIFTY-I") or []
        sig = self.strategy_evaluator.evaluate_nifty(nifty_bars, fut_bars=fut_bars)
        if sig:
            logger.info(f"🎯 [STRATEGY SIGNAL DETECTED] Setup: {sig.strategy_name} | Direction: {sig.direction} | Spot: {sig.spot_price:.2f} | Prob: {sig.probability_pct}")
            self._open_paper_trade(
                direction=sig.direction,
                strategy_name=sig.strategy_name,
                probability=sig.probability_pct,
                spot_price=sig.spot_price,
                now=now
            )

    def _evaluate_commodity_strategy_signal(self, symbol: str, now: datetime):
        """Evaluates 1-Hour Trend & 30-Bar Sweep setups for MCX Commodities via Unified Strategy Evaluator."""
        bars = self.bar_history.get(symbol) or []
        if symbol == "CRUDEOIL":
            sig = self.strategy_evaluator.evaluate_crude(bars)
            if sig:
                logger.info(f"🎯 [COMMODITY SIGNAL DETECTED] Setup: {sig.strategy_name} | Direction: {sig.direction} | Spot: {sig.spot_price:.2f} | Prob: {sig.probability_pct}")
                self._execute_commodity_paper_entry(
                    symbol=symbol,
                    direction=sig.direction,
                    strategy_name=sig.strategy_name,
                    probability=sig.probability_pct,
                    current_price=sig.spot_price,
                    now=now
                )

    def _execute_commodity_paper_entry(
        self,
        symbol: str,
        direction: str,
        strategy_name: str,
        probability: str,
        current_price: float,
        now: datetime
    ):
        """Constructs Dynamic CRUDE Option contract with 7% compounding and margin reservation."""
        lot_size = 100 if symbol == "CRUDEOIL" else (1250 if symbol == "NATURALGAS" else 100)

        if symbol == "CRUDEOIL":
            # Target / SL based on backtested 1-Hour Trend model
            # Underlying Target: +45 pts (Option Target: +25 pts = +₹2,500/lot)
            # Underlying SL: -22 pts (Option SL: -12 pts = -₹1,200/lot)
            tgt_u = 45.0
            sl_u = 22.0
            opt_tgt_pts = 25.0
            opt_sl_pts = 12.0
            opt_trail_pts = 3.0

            from trading_engine.quant_core.option_pricing import (
                get_canonical_contract_info,
                estimate_option_premium,
                calculate_realistic_lots_and_margin
            )

            contract_info = get_canonical_contract_info("CRUDEOIL", current_price, direction, now)
            atm_strike = contract_info["strike"]
            opt_type = contract_info["option_type"]
            contract_sym = contract_info["display_symbol"]
            lot_size = contract_info["lot_size"]

            # Authoritative pricing: Live broker quote or Black-76 commodity model
            est_prem, pricing_source = estimate_option_premium(
                underlying="CRUDEOIL",
                underlying_price=current_price,
                strike=atm_strike,
                option_type=opt_type,
                current_dt=now,
                broker_adapter=getattr(self, "broker_adapter", None)
            )

            lots, units, required_margin, allowed, reason = calculate_realistic_lots_and_margin(
                capital=self.commodity_capital,
                available_capital=self.commodity_available_capital,
                option_premium=est_prem,
                lot_size=lot_size,
                risk_pct=self.risk_pct,
                opt_sl_pts=opt_sl_pts,
                brokerage_per_lot=90.0,
                max_lots=50
            )

            if not allowed:
                logger.warning(
                    f"❌ [COMMODITY PAPER TRADE ABORTED] {reason} "
                    f"Required Margin: ₹{required_margin:,.2f}, Available: ₹{self.commodity_available_capital:,.2f}"
                )
                return

            self.commodity_blocked_capital = required_margin
            self.commodity_available_capital = round(self.commodity_capital - self.commodity_blocked_capital, 2)

            if direction == "CALL":
                target_px = round(current_price + tgt_u, 2)
                sl_px = round(current_price - sl_u, 2)
                trend_str = "BULLISH"
            else:
                target_px = round(current_price - tgt_u, 2)
                sl_px = round(current_price + sl_u, 2)
                trend_str = "BEARISH"

            trade_id = f"PTR_MCX_{now.strftime('%Y%m%d_%H%M%S')}"
            self.active_trade = {
                "id": trade_id,
                "trade_id": trade_id,
                "entry_time": now.strftime("%H:%M:%S"),
                "entry_datetime": now.isoformat(),
                "date": now.strftime("%Y-%m-%d"),
                "day": now.strftime("%A"),
                "symbol": symbol,
                "contract": contract_sym,
                "canonical_symbol": contract_info.get("exchange_symbol", contract_sym),
                "expiry_date": contract_info.get("expiry_date"),
                "pricing_source": pricing_source,
                "strike": atm_strike,
                "option_type": opt_type,
                "trend": trend_str,
                "entry_price": est_prem,
                "entry_underlying": current_price,
                "entry_rate": est_prem,
                "current_price": est_prem,
                "current_underlying": current_price,
                "spot_at_entry": current_price,
                "benchmark_symbol": symbol,
                "lots": lots,
                "units": units,
                "quantity": units,
                "blocked_margin": required_margin,
                "target": round(est_prem + opt_tgt_pts, 2),
                "sl": round(est_prem - opt_sl_pts, 2),
                "target_underlying": target_px,
                "sl_underlying": sl_px,
                "tgt_u": tgt_u,
                "sl_u": sl_u,
                "trail_trig_u": 22.0,
                "trail_lock_u": 5.5,
                "trail_active": False,
                "opt_tgt_pts": opt_tgt_pts,
                "opt_sl_pts": opt_sl_pts,
                "opt_trail_pts": opt_trail_pts,
                "strategy": strategy_name,
                "indicator": strategy_name,
                "probability": probability,
                "unrealized_pnl": 0.0,
                "points_gained": 0.0,
                "points": 0.0,
                "buyer_strength": self.buyer_strength,
                "seller_strength": self.seller_strength,
                "segment": "MCX Commodity"
            }

            logger.info(
                f"⚡ [COMMODITY PAPER TRADE OPENED] {contract_sym} ({trend_str}) @ ₹{est_prem:.2f} [{pricing_source}] (Underlying ₹{current_price:.2f}) | "
                f"Lots: {lots} ({units} units) | Blocked Margin: ₹{required_margin:,.2f} | "
                f"Crude Free Capital: ₹{self.commodity_available_capital:,.2f} | TGT: ₹{target_px} | SL: ₹{sl_px}"
            )
            self.save_state()
            return

        # Fallback for other commodities (e.g. NATURALGAS)
        lots = 1
        units = lots * lot_size
        required_margin = 10000.0
        if required_margin > self.commodity_available_capital:
            logger.warning(
                f"❌ [COMMODITY PAPER TRADE ABORTED] Insufficient capital. "
                f"Required: ₹{required_margin:,.2f}, Available: ₹{self.commodity_available_capital:,.2f}"
            )
            return

        self.commodity_blocked_capital = required_margin
        self.commodity_available_capital = round(self.commodity_capital - self.commodity_blocked_capital, 2)

        sl_pts = 2.50
        tgt_pts = 5.00
        target_px = round(current_price + tgt_pts, 2) if direction == "CALL" else round(current_price - tgt_pts, 2)
        sl_px = round(current_price - sl_pts, 2) if direction == "CALL" else round(current_price + sl_pts, 2)
        trend_str = "BULLISH" if direction == "CALL" else "BEARISH"
        contract_sym = f"{symbol} Near Fut"
        trade_id = f"PTR_MCX_{now.strftime('%Y%m%d_%H%M%S')}"

        self.active_trade = {
            "id": trade_id,
            "trade_id": trade_id,
            "entry_time": now.strftime("%H:%M:%S"),
            "entry_datetime": now.isoformat(),
            "date": now.strftime("%Y-%m-%d"),
            "day": now.strftime("%A"),
            "symbol": symbol,
            "contract": contract_sym,
            "strike": int(round(current_price)),
            "option_type": "FUT",
            "trend": trend_str,
            "entry_price": current_price,
            "entry_rate": current_price,
            "current_price": current_price,
            "spot_at_entry": current_price,
            "benchmark_symbol": symbol,
            "lots": lots,
            "units": units,
            "quantity": units,
            "blocked_margin": required_margin,
            "target": target_px,
            "sl": sl_px,
            "stop_loss": sl_px,
            "strategy": strategy_name,
            "indicator": strategy_name,
            "probability": probability,
            "unrealized_pnl": 0.0,
            "points_gained": 0.0,
            "points": 0.0,
            "buyer_strength": self.buyer_strength,
            "seller_strength": self.seller_strength,
            "segment": "MCX Commodity"
        }

        logger.info(
            f"⚡ [COMMODITY PAPER TRADE OPENED] {contract_sym} ({trend_str}) @ ₹{current_price:.2f} | "
            f"Lots: {lots} ({units} units) | Blocked Margin: ₹{required_margin:,.2f} | "
            f"Free Capital: ₹{self.commodity_available_capital:,.2f} | TGT: ₹{target_px} | SL: ₹{sl_px}"
        )
        self.save_state()

    def _open_paper_trade(
        self,
        direction: str,
        strategy_name: str,
        probability: str,
        spot_price: float,
        now: datetime
    ):
        """Constructs Dynamic ATM Option contract and opens a paper trade from Equity Account."""
        opt_type = "CE" if direction == "CALL" else "PE"
        contract = OptionResolver.resolve_contract(
            spot_price=spot_price,
            option_type=opt_type,
            current_dt=now,
            underlying="NIFTY",
            avoid_otm=True
        )

        from trading_engine.quant_core.option_pricing import (
            get_canonical_contract_info,
            estimate_option_premium,
            calculate_realistic_lots_and_margin
        )

        opt_dir = "CALL" if direction == "CE" else "PUT"
        contract_info = get_canonical_contract_info("NIFTY", spot_price, opt_dir, now)

        # Realistic Option Premium: Broker live quote or Black-Scholes model
        est_prem, pricing_source = estimate_option_premium(
            underlying="NIFTY",
            underlying_price=spot_price,
            strike=contract.strike,
            option_type=opt_type,
            current_dt=now,
            broker_adapter=getattr(self, "broker_adapter", None)
        )

        risk_pt = max(15.0, round(est_prem * self.sl_pct, 2))

        lots, units, required_capital, allowed, reason = calculate_realistic_lots_and_margin(
            capital=self.equity_capital,
            available_capital=self.equity_available_capital,
            option_premium=est_prem,
            lot_size=contract.lot_size,
            risk_pct=self.risk_pct,
            opt_sl_pts=risk_pt,
            brokerage_per_lot=40.0,
            max_lots=100
        )

        if not allowed:
            logger.warning(
                f"❌ [PAPER TRADE ABORTED] {reason} "
                f"Required Capital: ₹{required_capital:,.2f}, Available: ₹{self.equity_available_capital:,.2f}"
            )
            return

        # Deduct / block margin immediately from Equity Account
        self.equity_blocked_capital = required_capital
        self.equity_available_capital = round(self.equity_capital - self.equity_blocked_capital, 2)

        target_px = round(est_prem * (1.0 + self.tgt_pct), 2)
        sl_px = round(est_prem * (1.0 - self.sl_pct), 2)

        trade_id = f"PTR_{now.strftime('%Y%m%d_%H%M%S')}"
        self.active_trade = {
            "id": trade_id,
            "trade_id": trade_id,
            "entry_time": now.strftime("%H:%M:%S"),
            "entry_datetime": now.isoformat(),
            "date": now.strftime("%Y-%m-%d"),
            "day": now.strftime("%A"),
            "symbol": "NIFTY",
            "contract": contract.symbol,
            "strike": contract.strike,
            "option_type": opt_type,
            "trend": "BULLISH" if direction == "CALL" else "BEARISH",
            "entry_price": est_prem,
            "entry_rate": est_prem,
            "current_price": est_prem,
            "spot_at_entry": spot_price,
            "benchmark_symbol": "NIFTY",
            "lots": lots,
            "units": units,
            "quantity": units,
            "blocked_margin": required_capital,
            "target": target_px,
            "sl": sl_px,
            "stop_loss": sl_px,
            "strategy": strategy_name,
            "indicator": strategy_name,
            "probability": probability,
            "unrealized_pnl": 0.0,
            "points_gained": 0.0,
            "points": 0.0,
            "buyer_strength": self.buyer_strength,
            "seller_strength": self.seller_strength,
            "segment": "NIFTY"
        }

        logger.info(
            f"⚡ [PAPER TRADE OPENED] {contract.symbol} ({self.active_trade['trend']}) @ ₹{est_prem:.2f} | "
            f"Lots: {lots} ({units} units) | Blocked Margin: ₹{required_capital:,.2f} | "
            f"Stock Free Capital: ₹{self.equity_available_capital:,.2f} | TGT: ₹{target_px} | SL: ₹{sl_px}"
        )
        self.save_state()

    def _close_active_trade(
        self,
        reason: str,
        exit_price: float,
        exit_time: datetime,
        opt_pts: Optional[float] = None,
        trade_to_close: Optional[Dict[str, Any]] = None
    ):
        """Finalizes active trade and adds to persistent ledger with separated fund settlement."""
        if trade_to_close is None:
            trade_to_close = self.active_trade
        if not trade_to_close:
            return

        pos = trade_to_close
        is_comm = self._is_commodity_trade(pos)
        seg_key = "commodity" if is_comm else "equity"

        if opt_pts is not None:
            pts = round(opt_pts, 2)
        elif pos.get("trend") in ("BEARISH", "SHORT", "PUT") and pos.get("segment") == "MCX Commodity":
            pts = round(pos["entry_price"] - exit_price, 2)
        else:
            pts = round(exit_price - pos["entry_price"], 2)

        gross = round(pts * pos["units"], 2)

        # Statutory charges + slippage friction via Unified Strategy Engine
        charges = self.strategy_evaluator.calculate_charges(pos["lots"], pos["units"], is_commodity=is_comm)

        net = round(gross - charges, 2)
        invested = pos["entry_price"] * pos["units"]
        roi = round((net / invested) * 100.0, 2) if invested > 0 else 0.0

        closed_rec = {
            "id": pos.get("id", f"PTR_{exit_time.strftime('%Y%m%d_%H%M%S')}"),
            "trade_id": pos.get("trade_id", pos.get("id", "")),
            "date": pos.get("date", exit_time.strftime("%Y-%m-%d")),
            "day": pos.get("day", exit_time.strftime("%A")),
            "symbol": pos.get("symbol", "NIFTY"),
            "contract": pos.get("contract", "NIFTY ATM"),
            "strike": pos.get("strike", 0.0),
            "option_type": pos.get("option_type", "CE"),
            "trend": pos.get("trend", "BULLISH"),
            "action": f"BUY_{pos.get('option_type', 'CE')}",
            "indicator": pos.get("strategy", pos.get("indicator", "Unified AI Trader")),
            "strategy": pos.get("strategy", pos.get("indicator", "Unified AI Trader")),
            "probability": pos.get("probability", "70%"),
            "entry_time": pos.get("entry_time", "--"),
            "entry_price": pos.get("entry_price", pos.get("entry_rate", 0.0)),
            "entry_rate": pos.get("entry_rate", pos.get("entry_price", 0.0)),
            "exit_time": exit_time.strftime("%H:%M:%S"),
            "exit_price": round(exit_price, 2),
            "exit_rate": round(exit_price, 2),
            "reason": reason,
            "exit_reason": reason,
            "points": pts,
            "gross": gross,
            "charges": charges,
            "net": net,
            "roi": roi,
            "lots": pos.get("lots", 1),
            "units": pos.get("units", pos.get("quantity", 50)),
            "quantity": pos.get("units", pos.get("quantity", 50)),
            "segment": pos.get("segment", "NSE Cash" if not is_comm else "MCX Commodity")
        }

        self.trade_history.append(closed_rec)

        # Capital Settlement: Release blocked margin and credit/debit to separate ICICI account
        if is_comm:
            self.commodity_blocked_capital = 0.0
            self.commodity_capital = round(self.commodity_capital + net, 2)
            self.commodity_available_capital = self.commodity_capital
            self.last_crude_exit_time = exit_time
            acc_name = "Crude Fund"
            acc_cap = self.commodity_capital
        else:
            self.equity_blocked_capital = 0.0
            self.equity_capital = round(self.equity_capital + net, 2)
            self.equity_available_capital = self.equity_capital
            self.last_equity_exit_time = exit_time
            acc_name = "Stock Fund"
            acc_cap = self.equity_capital

        if hasattr(self, "active_trades") and seg_key in self.active_trades:
            del self.active_trades[seg_key]
        if hasattr(self, "active_trades") and not self.active_trades:
            self._active_trade = None
        self.save_state()
        self._record_to_db(closed_rec)
        logger.info(
            f"✅ [PAPER TRADE CLOSED] {closed_rec['contract']} | Net PnL: {net:+,.2f} ({pts:+.2f} pts) | "
            f"Reason: {reason} | {acc_name}: ₹{acc_cap:,.2f} | Total Capital: ₹{self.capital:,.2f}"
        )

    def _record_to_db(self, rec: Dict[str, Any]):
        """Persists closed trade into TimescaleDB trade_log table."""
        try:
            from trading_engine.core.keyvault import get_secret
            import psycopg2
            db_url = os.getenv("DATABASE_URL") or get_secret("DATABASE_URL") or "postgresql://postgres:postgres@localhost:5432/trading"
            conn = psycopg2.connect(db_url)
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO trade_log (
                        symbol, option_symbol, side, entry_price, exit_price,
                        stop_loss, target, quantity, pnl, net_pnl, result,
                        strategy, exit_reason
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """, (
                    rec["symbol"], rec["contract"], rec["option_type"],
                    rec["entry_rate"], rec["exit_rate"], 0.0, 0.0,
                    rec["units"], rec["gross"], rec["net"],
                    "WIN" if rec["net"] > 0 else "LOSS",
                    rec["indicator"], rec["reason"]
                ))
                conn.commit()
            conn.close()
        except Exception as e:
            logger.debug(f"PaperTradeDaemon: DB record skipped: {e}")

    def force_exit(self, trade_id: Optional[str] = None) -> Dict[str, Any]:
        """Manually forces immediate market exit of the targeted active trade."""
        if not hasattr(self, "active_trades") or not self.active_trades:
            if not self.active_trade:
                return {"status": "error", "message": "No active trade to exit."}
            target_trade = self.active_trade
        else:
            target_trade = None
            if trade_id:
                for t in self.active_trades.values():
                    if t.get("id") == trade_id or t.get("trade_id") == trade_id or t.get("symbol") == trade_id or t.get("segment") == trade_id:
                        target_trade = t
                        break
            if not target_trade:
                target_trade = self.active_trade

        if not target_trade:
            return {"status": "error", "message": "No matching active trade to exit."}

        exit_px = target_trade.get("current_price", target_trade.get("entry_price", 0.0))
        self._close_active_trade(reason="Manual Force Exit", exit_price=exit_px, exit_time=datetime.now(), trade_to_close=target_trade)
        return {"status": "success", "message": f"Active position ({target_trade.get('contract', '')}) successfully exited."}

    # ── Summary & Subtab Generation ──────────────────────────────────────────

    def get_summary(self) -> Dict[str, Any]:
        """Computes comprehensive quantitative performance across all 3 subtabs."""
        trades = self.trade_history
        total_trades = len(trades)
        wins = [t for t in trades if t["net"] > 0]
        losses = [t for t in trades if t["net"] <= 0]
        n_wins = len(wins)
        n_losses = len(losses)
        win_rate = round((n_wins / total_trades * 100.0), 1) if total_trades > 0 else 0.0

        gross_pnl = sum(t["gross"] for t in trades)
        total_charges = sum(t["charges"] for t in trades)
        net_pnl = sum(t["net"] for t in trades)

        # Segregated Fund P&L Breakdown (ICICI Direct style)
        equity_trades = [t for t in trades if not self._is_commodity_trade(t)]
        commodity_trades = [t for t in trades if self._is_commodity_trade(t)]
        equity_net_pnl = sum(t["net"] for t in equity_trades)
        commodity_net_pnl = sum(t["net"] for t in commodity_trades)

        gross_wins = sum(t["gross"] for t in wins)
        gross_losses = abs(sum(t["gross"] for t in losses))
        profit_factor = round(gross_wins / gross_losses, 2) if gross_losses > 0 else (99.0 if gross_wins > 0 else 0.0)

        # Drawdown calculation
        peak = self.initial_capital
        running_bal = self.initial_capital
        max_dd = 0.0
        for t in trades:
            running_bal += t["net"]
            if running_bal > peak:
                peak = running_bal
            dd = peak - running_bal
            if dd > max_dd:
                max_dd = dd

        avg_win = round(sum(t["net"] for t in wins) / n_wins, 2) if n_wins > 0 else 0.0
        avg_loss = round(abs(sum(t["net"] for t in losses)) / n_losses, 2) if n_losses > 0 else 0.0

        # Subtab B: Monthly Trade Details
        monthly_map: Dict[str, Dict[str, Any]] = {}
        for t in trades:
            d_str = t.get("date", "")
            if len(d_str) >= 7:
                m_key = d_str[:7]  # YYYY-MM
                if m_key not in monthly_map:
                    monthly_map[m_key] = {
                        "month": m_key,
                        "trades_count": 0,
                        "wins": 0,
                        "losses": 0,
                        "gross": 0.0,
                        "charges": 0.0,
                        "net": 0.0
                    }
                monthly_map[m_key]["trades_count"] += 1
                if t["net"] > 0:
                    monthly_map[m_key]["wins"] += 1
                else:
                    monthly_map[m_key]["losses"] += 1
                monthly_map[m_key]["gross"] += t["gross"]
                monthly_map[m_key]["charges"] += t["charges"]
                monthly_map[m_key]["net"] += t["net"]

        monthly_list = []
        for m_key in sorted(monthly_map.keys(), reverse=True):
            m = monthly_map[m_key]
            wr = round((m["wins"] / m["trades_count"]) * 100.0, 1) if m["trades_count"] > 0 else 0.0
            monthly_list.append({
                "month": m["month"],
                "trades": m["trades_count"],
                "wins": m["wins"],
                "losses": m["losses"],
                "winRate": f"{wr:.1f}%",
                "gross": round(m["gross"], 2),
                "charges": round(m["charges"], 2),
                "net": round(m["net"], 2)
            })

        # Subtab C: Today's Trade Details
        today_str = date.today().isoformat()
        today_trades = [t for t in trades if t.get("date") == today_str]
        today_net = sum(t["net"] for t in today_trades)
        idx_cnt, comm_cnt, tot_cnt = self.get_today_trade_counts(today_str)
        comm_allowed, comm_reason, comm_dyn_status = self.check_commodity_dynamic_trade_allowed(datetime.now(), today_str)

        return {
            "total_trades": total_trades,
            "wins": n_wins,
            "losses": n_losses,
            "win_rate": win_rate,
            "gross_pnl": round(gross_pnl, 2),
            "charges": round(total_charges, 2),
            "net_pnl": round(net_pnl, 2),
            "profit_factor": profit_factor,
            "max_drawdown": round(max_dd, 2),
            "avg_win": avg_win,
            "avg_loss": avg_loss,
            # Total Capital Aggregation
            "capital": round(self.capital, 2),
            "total_capital": round(self.capital, 2),
            "blocked_capital": round(self.blocked_capital, 2),
            "available_capital": round(self.available_capital, 2),
            "initial_capital": round(self.initial_capital, 2),
            # Segregated ICICI Accounts
            "equity_capital": round(self.equity_capital, 2),
            "equity_initial_capital": round(self.equity_initial_capital, 2),
            "equity_blocked_capital": round(self.equity_blocked_capital, 2),
            "equity_available_capital": round(self.equity_available_capital, 2),
            "equity_net_pnl": round(equity_net_pnl, 2),
            "commodity_capital": round(self.commodity_capital, 2),
            "commodity_initial_capital": round(self.commodity_initial_capital, 2),
            "commodity_blocked_capital": round(self.commodity_blocked_capital, 2),
            "commodity_available_capital": round(self.commodity_available_capital, 2),
            "commodity_net_pnl": round(commodity_net_pnl, 2),
            "commodity_today_net": round(comm_dyn_status.get("today_comm_net", 0.0), 2),
            "commodity_dynamic_status": comm_dyn_status,
            "today_net": round(today_net, 2),
            "today_trades_count": tot_cnt,
            "today_trades_index": idx_cnt,
            "today_trades_commodity": comm_cnt,
            "max_trades_total": self.max_trades_per_day,
            "max_trades_index": self.max_trades_index,
            "max_trades_commodity": self.max_trades_commodity,
            "monthly_breakdown": monthly_list,
            "today_trades": today_trades,
            "all_trades": list(reversed(trades))
        }

    @staticmethod
    def is_market_open(segment: str = "NSE Cash") -> bool:
        """
        Determines whether the market is RUNNING or CLOSED for the specified segment in IST.
        - MCX Commodity (CRUDEOIL, NATURALGAS): Mon-Fri 09:00 - 23:30 IST.
        - NSE Cash / NSE Futures / BSE Cash: Mon-Fri 09:15 - 15:30 IST.
        - Weekends: Closed (Always gray).
        """
        try:
            from zoneinfo import ZoneInfo
            now = datetime.now(ZoneInfo("Asia/Kolkata"))
        except Exception:
            tz = timezone(timedelta(hours=5, minutes=30))
            now = datetime.now(tz)

        if now.weekday() >= 5:
            return False

        t = now.time()
        s = (segment or "").lower()
        if "mcx" in s or "commodity" in s or "crude" in s or "natural" in s:
            return dt_time(9, 0) <= t <= dt_time(23, 30)
        return dt_time(9, 15) <= t <= dt_time(15, 30)

    def get_indices_list(self) -> List[Dict[str, Any]]:
        """Returns list of indices enriched with real-time market segment running/closed status."""
        if not hasattr(self, "indices"):
            return []
        enriched = []
        for key, idx in self.indices.items():
            item = dict(idx)
            seg = item.get("segment", "NSE Cash")
            is_open = self.is_market_open(seg)
            item["market_status"] = "RUNNING" if is_open else "CLOSED"
            item["is_open"] = is_open
            item["status_color"] = "green" if is_open else "gray"
            enriched.append(item)
        return enriched

    def get_full_state(self) -> Dict[str, Any]:
        """Complete bundle returned to the Paper Trading tab UI."""
        return {
            "status": "active" if self.is_running else "paused",
            "active_trade": self.active_trade,
            "summary": self.get_summary(),
            "config": self.get_config(),
            "buyer_strength": self.buyer_strength,
            "seller_strength": self.seller_strength,
            "current_spot": self.current_spot,
            "current_future": self.current_future,
            "indices": self.get_indices_list(),
            "market_segments": {
                "NSE Cash": "RUNNING" if self.is_market_open("NSE Cash") else "CLOSED",
                "NSE Futures": "RUNNING" if self.is_market_open("NSE Futures") else "CLOSED",
                "BSE Cash": "RUNNING" if self.is_market_open("BSE Cash") else "CLOSED",
                "MCX Commodity": "RUNNING" if self.is_market_open("MCX Commodity") else "CLOSED",
                "stock": "RUNNING" if self.is_market_open("NSE Cash") else "CLOSED",
                "crude": "RUNNING" if self.is_market_open("MCX Commodity") else "CLOSED",
            },
            "timestamp": datetime.now().isoformat()
        }


# Global singleton instance
paper_daemon = PaperTradeDaemon.get_instance()
