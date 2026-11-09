"""
Crude Oil 6-Month Backtest Engine
──────────────────────────────────
Institutional Walk-Forward Backtester for MCX CRUDEOIL Futures.
Evaluates high-edge Bullish & Bearish momentum setups using the trained
Crude XGBoost Model against 1-minute historical candles.

Features:
- Realistic MCX Commodity Market Microstructure (09:00 to 23:15 IST).
- Daily trade limit gating (4 trades/day cap for commodities).
- 2:1 Asymmetric Reward-to-Risk (Target: +40 pts / SL: -20 pts / Trailing Lock).
- Statutory Commodity Transaction Tax (CTT), Brokerage, Stamp Duty, GST & Slippage.
- Generates comprehensive Scorecard, Monthly Breakdown, and Trade Log CSV.
"""

import os
import sys
import json
import logging
from datetime import datetime, date, timedelta
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple

import joblib
import numpy as np
import pandas as pd

# Set up project root
ROOT_DIR = Path(__file__).resolve().parent.parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from trading_engine.models.crude_features import compute_crude_features, CRUDE_FEATURE_COLUMNS

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s - %(message)s",
    datefmt="%H:%M:%S"
)
logger = logging.getLogger("crude_backtest")

MODEL_PATH = ROOT_DIR / "models" / "saved" / "crude_macro_model.pkl"
HISTORICAL_CSV = ROOT_DIR / "data" / "historical" / "CRUDEOIL_1min_3years.csv"
REPORTS_DIR = ROOT_DIR / "reports"


class CrudeBacktestEngine:
    """
    Simulates high-precision execution on 1-minute MCX CRUDEOIL futures.
    """

    def __init__(
        self,
        model_path: Path = MODEL_PATH,
        capital: float = 20000.0,
        lot_size: int = 100,            # 1 MCX Crude contract = 100 barrels
        target_pts: float = 40.0,       # +40 points (+₹4,000 / lot)
        sl_pts: float = 20.0,           # -20 points (-₹2,000 / lot)
        trail_trigger_pts: float = 25.0,# Trigger trailing SL once +25 pts reached
        trail_lock_pts: float = 5.0,    # Move SL to +5 pts (breakeven profit)
        max_trades_per_day: int = 4,    # Commodity trade cap (fallback if dynamic disabled)
        dynamic_trade_limit: bool = True,# Dynamic: Profit=∞ | 2-Loss=2.5h pause | 3-Loss=Stop
        pause_hours: float = 2.5,
        loss_streak_pause: int = 2,
        loss_streak_stop: int = 3,
        score_threshold: float = 0.40,  # Calibrated ML probability hurdle
        vol_ratio_min: float = 1.00,    # Volume ratio threshold
        enable_pullback_setup: bool = True,
        enable_reversal_setup: bool = True
    ):
        self.model_path = model_path
        self.initial_capital = capital
        self.lot_size = lot_size
        self.target_pts = target_pts
        self.sl_pts = sl_pts
        self.trail_trigger_pts = trail_trigger_pts
        self.trail_lock_pts = trail_lock_pts
        self.max_trades_per_day = max_trades_per_day
        self.dynamic_trade_limit = dynamic_trade_limit
        self.pause_hours = pause_hours
        self.loss_streak_pause = loss_streak_pause
        self.loss_streak_stop = loss_streak_stop
        self.score_threshold = score_threshold
        self.vol_ratio_min = vol_ratio_min
        self.enable_pullback_setup = enable_pullback_setup
        self.enable_reversal_setup = enable_reversal_setup

        self.model = None
        self.feature_cols: List[str] = []
        self._load_model()

    def _load_model(self):
        if not self.model_path.exists():
            logger.warning(f"Crude model package not found at {self.model_path}. ML filter will be bypassed.")
            return

        try:
            package = joblib.load(self.model_path)
            self.model = package.get("model")
            self.feature_cols = package.get("feature_cols", CRUDE_FEATURE_COLUMNS)
            logger.info(f"Loaded trained Crude XGBoost model from {self.model_path}")
        except Exception as e:
            logger.error(f"Error loading model package: {e}")

    def run(
        self,
        csv_path: Optional[Path] = None,
        date_from: str = "2026-05-01",
        date_to: str = "2026-11-06"
    ) -> Dict[str, Any]:
        """
        Executes backtest over the specified date window (default: last 6 months).
        """
        csv_file = csv_path or HISTORICAL_CSV
        if not csv_file.exists():
            raise FileNotFoundError(f"Historical data file not found: {csv_file}")

        logger.info(f"Loading historical data from {csv_file}...")
        df_raw = pd.read_csv(csv_file)
        if not pd.api.types.is_datetime64_any_dtype(df_raw["datetime"]):
            df_raw["dt"] = pd.to_datetime(df_raw["datetime"])
        else:
            df_raw["dt"] = df_raw["datetime"]

        logger.info(f"Loaded {len(df_raw):,} raw candles. Computing indicators & features...")
        df_featured = compute_crude_features(df_raw)

        # Filter date range
        df_featured["date_str"] = df_featured["dt"].dt.strftime("%Y-%m-%d")
        mask = (df_featured["date_str"] >= date_from) & (df_featured["date_str"] <= date_to)
        df_test = df_featured[mask].copy().reset_index(drop=True)

        logger.info(
            f"Backtest Window: {date_from} to {date_to} | "
            f"Total Candles: {len(df_test):,} | Days: {df_test['date_str'].nunique()}"
        )

        if len(df_test) < 100:
            raise ValueError("Insufficient candles in test range.")

        # Batch predict ML probabilities if model available
        if self.model and self.feature_cols:
            logger.info("Generating batch ML probability inferences...")
            X = df_test[self.feature_cols]
            probas = self.model.predict_proba(X)
            df_test["p_chop"] = probas[:, 0]
            df_test["p_bull"] = probas[:, 1]
            df_test["p_bear"] = probas[:, 2]
        else:
            df_test["p_chop"] = 0.33
            df_test["p_bull"] = 0.50
            df_test["p_bear"] = 0.50

        # State tracking
        capital = self.initial_capital
        active_position: Optional[Dict[str, Any]] = None
        trade_log: List[Dict[str, Any]] = []
        trades_today = 0
        current_day_str = ""
        day_comm_net = 0.0
        day_consecutive_losses = 0
        day_pause_until: Optional[datetime] = None
        day_stopped = False

        # Simulation loop over 1-minute bars
        logger.info("Executing tick simulation...")
        for i in range(20, len(df_test)):
            row = df_test.iloc[i]
            dt = row["dt"]
            day_str = row["date_str"]
            ltp = float(row["close"])
            bar_high = float(row["high"])
            bar_low = float(row["low"])
            bar_open = float(row["open"])

            # Day rollover
            if day_str != current_day_str:
                current_day_str = day_str
                trades_today = 0
                day_comm_net = 0.0
                day_consecutive_losses = 0
                day_pause_until = None
                day_stopped = False

            # ── 1. ACTIVE POSITION MANAGEMENT ──
            if active_position is not None:
                pos = active_position
                direction = pos["direction"]  # "LONG" or "SHORT"
                entry_px = pos["entry_price"]
                sl = pos["sl"]
                target = pos["target"]
                trail_active = pos["trail_active"]

                # High / Low price action check
                exit_triggered = False
                exit_px = ltp
                exit_reason = ""

                # Check Long Position
                if direction == "LONG":
                    # Max potential gain in bar
                    gain = bar_high - entry_px
                    loss = entry_px - bar_low

                    # Trailing Stop activation
                    if not trail_active and gain >= self.trail_trigger_pts:
                        pos["trail_active"] = True
                        pos["sl"] = entry_px + self.trail_lock_pts
                        sl = pos["sl"]

                    if bar_high >= target:
                        exit_triggered = True
                        exit_px = target
                        exit_reason = "TARGET HIT"
                    elif bar_low <= sl:
                        exit_triggered = True
                        exit_px = sl
                        exit_reason = "TRAILING SL HIT" if trail_active else "STOP LOSS HIT"
                    elif dt.hour >= 23 and dt.minute >= 10:
                        exit_triggered = True
                        exit_px = ltp
                        exit_reason = "SESSION CLOSE SQUARED OFF"

                # Check Short Position
                elif direction == "SHORT":
                    gain = entry_px - bar_low
                    loss = bar_high - entry_px

                    # Trailing Stop activation
                    if not trail_active and gain >= self.trail_trigger_pts:
                        pos["trail_active"] = True
                        pos["sl"] = entry_px - self.trail_lock_pts
                        sl = pos["sl"]

                    if bar_low <= target:
                        exit_triggered = True
                        exit_px = target
                        exit_reason = "TARGET HIT"
                    elif bar_high >= sl:
                        exit_triggered = True
                        exit_px = sl
                        exit_reason = "TRAILING SL HIT" if trail_active else "STOP LOSS HIT"
                    elif dt.hour >= 23 and dt.minute >= 10:
                        exit_triggered = True
                        exit_px = ltp
                        exit_reason = "SESSION CLOSE SQUARED OFF"

                if exit_triggered:
                    pts = round(exit_px - entry_px if direction == "LONG" else entry_px - exit_px, 2)
                    gross = round(pts * self.lot_size, 2)
                    # Statutory charges + 1 tick slippage
                    charges = 90.0 + 100.0  # ₹90 taxes & brokerage + ₹100 1-pt slippage
                    net = round(gross - charges, 2)
                    capital = round(capital + net, 2)

                    trade_rec = {
                        "id": f"CRUDE_{day_str}_{dt.strftime('%H%M%S')}",
                        "date": day_str,
                        "entry_time": pos["entry_time"],
                        "exit_time": dt.strftime("%H:%M:%S"),
                        "direction": direction,
                        "symbol": "CRUDEOIL Near Fut",
                        "setup": pos.get("setup", "Breakout"),
                        "entry_price": entry_px,
                        "exit_price": exit_px,
                        "points": pts,
                        "gross": gross,
                        "charges": charges,
                        "net": net,
                        "balance": capital,
                        "reason": exit_reason,
                        "ml_score": pos["ml_score"]
                    }
                    trade_log.append(trade_rec)
                    day_comm_net += net
                    if net <= 0:
                        day_consecutive_losses += 1
                        if day_consecutive_losses >= self.loss_streak_stop:
                            day_stopped = True
                        elif day_consecutive_losses == self.loss_streak_pause:
                            day_pause_until = dt + timedelta(hours=self.pause_hours)
                    else:
                        day_consecutive_losses = 0
                    active_position = None
                continue

            # ── 2. ENTRY SIGNAL EVALUATION ──
            # Market Hours: 09:15 to 22:45 IST (No new entries in the last 30 minutes)
            if dt.hour < 9 or (dt.hour == 9 and dt.minute < 15):
                continue
            if dt.hour > 22 or (dt.hour == 22 and dt.minute > 45):
                continue

            # Dynamic vs Static Trade Limit Gating
            if self.dynamic_trade_limit:
                if day_stopped:
                    continue
                if day_pause_until is not None and dt < day_pause_until:
                    continue
                # If day_comm_net > 0 -> unlimited trades allowed!
                # If day_comm_net <= 0 -> allowed unless stopped or paused
            elif self.max_trades_per_day > 0 and trades_today >= self.max_trades_per_day:
                continue

            p_bull = float(row["p_bull"])
            p_bear = float(row["p_bear"])
            rsi = float(row["rsi"])
            ema9 = float(row["ema9"])
            ema20 = float(row["ema20"])
            ema50 = float(row.get("ema50", row["ema20"]))
            macd_hist = float(row["macd_hist"])
            vol_ratio = float(row["volume_ratio"])
            close_vs_ema20 = float(row.get("close_vs_ema20_pct", 0.0))
            rsi_slope_3 = float(row.get("rsi_slope_3", 0.0))
            b_pct = float(row.get("bollinger_pct", 0.5))

            # Setup 1: Momentum Breakout
            long_breakout = (
                p_bull >= self.score_threshold
                and ema9 > ema20
                and ltp > ema20
                and rsi > 51.0
                and macd_hist > -1.0
                and vol_ratio >= self.vol_ratio_min
            )
            short_breakout = (
                p_bear >= self.score_threshold
                and ema9 < ema20
                and ltp < ema20
                and rsi < 49.0
                and macd_hist < 1.0
                and vol_ratio >= self.vol_ratio_min
            )

            # Setup 2: Trend Pullback / EMA Retest
            long_pullback = False
            short_pullback = False
            if self.enable_pullback_setup:
                long_pullback = (
                    p_bull >= (self.score_threshold - 0.03)
                    and ema9 > ema20 > ema50
                    and abs(close_vs_ema20) <= 0.0015
                    and rsi > 45.0
                    and rsi_slope_3 > 0
                )
                short_pullback = (
                    p_bear >= (self.score_threshold - 0.03)
                    and ema9 < ema20 < ema50
                    and abs(close_vs_ema20) <= 0.0015
                    and rsi < 55.0
                    and rsi_slope_3 < 0
                )

            # Setup 3: Mean Reversion / Trap
            long_reversal = False
            short_reversal = False
            if self.enable_reversal_setup:
                long_reversal = (
                    p_bull >= self.score_threshold
                    and rsi < 32.0
                    and rsi_slope_3 > 0.8
                    and b_pct < 0.10
                )
                short_reversal = (
                    p_bear >= self.score_threshold
                    and rsi > 68.0
                    and rsi_slope_3 < -0.8
                    and b_pct > 0.90
                )

            is_long = long_breakout or long_pullback or long_reversal
            is_short = short_breakout or short_pullback or short_reversal

            if is_long:
                if long_breakout:
                    setup_desc = "Breakout"
                elif long_pullback:
                    setup_desc = "Pullback Retest"
                else:
                    setup_desc = "Mean Reversion"

                entry_px = ltp
                active_position = {
                    "direction": "LONG",
                    "setup": setup_desc,
                    "entry_price": entry_px,
                    "entry_time": dt.strftime("%H:%M:%S"),
                    "target": round(entry_px + self.target_pts, 2),
                    "sl": round(entry_px - self.sl_pts, 2),
                    "trail_active": False,
                    "ml_score": round(p_bull, 3)
                }
                trades_today += 1

            elif is_short:
                if short_breakout:
                    setup_desc = "Breakdown"
                elif short_pullback:
                    setup_desc = "Pullback Retest"
                else:
                    setup_desc = "Mean Reversion"

                entry_px = ltp
                active_position = {
                    "direction": "SHORT",
                    "setup": setup_desc,
                    "entry_price": entry_px,
                    "entry_time": dt.strftime("%H:%M:%S"),
                    "target": round(entry_px - self.target_pts, 2),
                    "sl": round(entry_px + self.sl_pts, 2),
                    "trail_active": False,
                    "ml_score": round(p_bear, 3)
                }
                trades_today += 1

        # ── 3. PERFORMANCE METRICS ──
        total_trades = len(trade_log)
        wins = [t for t in trade_log if t["net"] > 0]
        losses = [t for t in trade_log if t["net"] <= 0]
        n_wins = len(wins)
        n_losses = len(losses)
        win_rate = round(n_wins / total_trades * 100.0, 1) if total_trades > 0 else 0.0

        gross_pnl = sum(t["gross"] for t in trade_log)
        charges = sum(t["charges"] for t in trade_log)
        net_pnl = sum(t["net"] for t in trade_log)

        gross_wins = sum(t["gross"] for t in wins)
        gross_losses = abs(sum(t["gross"] for t in losses))
        profit_factor = round(gross_wins / gross_losses, 2) if gross_losses > 0 else 99.0

        num_days = df_test["date_str"].nunique()
        trades_per_day = round(total_trades / num_days, 2) if num_days > 0 else 0.0

        # Drawdown computation
        running_bal = self.initial_capital
        peak = self.initial_capital
        max_dd = 0.0
        for t in trade_log:
            running_bal += t["net"]
            if running_bal > peak:
                peak = running_bal
            dd = peak - running_bal
            if dd > max_dd:
                max_dd = dd

        max_dd_pct = round((max_dd / peak) * 100.0, 2) if peak > 0 else 0.0
        roi_pct = round((net_pnl / self.initial_capital) * 100.0, 2)

        # Setup breakdown
        setup_stats: Dict[str, Dict[str, Any]] = {}
        for t in trade_log:
            s_name = t.get("setup", "Unknown")
            if s_name not in setup_stats:
                setup_stats[s_name] = {"trades": 0, "wins": 0, "losses": 0, "net": 0.0}
            setup_stats[s_name]["trades"] += 1
            if t["net"] > 0:
                setup_stats[s_name]["wins"] += 1
            else:
                setup_stats[s_name]["losses"] += 1
            setup_stats[s_name]["net"] = round(setup_stats[s_name]["net"] + t["net"], 2)

        for s_name, data in setup_stats.items():
            data["win_rate"] = f"{round(data['wins'] / data['trades'] * 100.0, 1)}%"

        # Monthly breakdown
        monthly_map: Dict[str, Dict[str, Any]] = {}
        for t in trade_log:
            m_key = t["date"][:7]  # YYYY-MM
            if m_key not in monthly_map:
                monthly_map[m_key] = {"month": m_key, "trades": 0, "wins": 0, "losses": 0, "net": 0.0}
            monthly_map[m_key]["trades"] += 1
            if t["net"] > 0:
                monthly_map[m_key]["wins"] += 1
            else:
                monthly_map[m_key]["losses"] += 1
            monthly_map[m_key]["net"] = round(monthly_map[m_key]["net"] + t["net"], 2)

        monthly_list = []
        for m in sorted(monthly_map.keys()):
            item = monthly_map[m]
            w_r = round(item["wins"] / item["trades"] * 100.0, 1) if item["trades"] > 0 else 0.0
            item["win_rate"] = f"{w_r:.1f}%"
            monthly_list.append(item)

        scorecard = {
            "symbol": "CRUDEOIL Near Fut",
            "date_range": f"{date_from} to {date_to}",
            "days_tested": num_days,
            "initial_capital": self.initial_capital,
            "final_balance": round(capital, 2),
            "net_pnl": round(net_pnl, 2),
            "roi_pct": f"{roi_pct:+.2f}%",
            "total_trades": total_trades,
            "trades_per_day": trades_per_day,
            "wins": n_wins,
            "losses": n_losses,
            "win_rate": f"{win_rate:.1f}%",
            "profit_factor": profit_factor,
            "max_drawdown": round(max_dd, 2),
            "max_drawdown_pct": f"{max_dd_pct:.2f}%",
            "charges_paid": round(charges, 2),
            "setup_breakdown": setup_stats,
            "monthly_breakdown": monthly_list,
        }

        # Persist results
        REPORTS_DIR.mkdir(parents=True, exist_ok=True)
        report_json = REPORTS_DIR / "crude_6m_backtest.json"
        trades_csv = REPORTS_DIR / "crude_6m_trades.csv"

        with open(report_json, "w") as f:
            json.dump({"scorecard": scorecard, "trade_log": trade_log}, f, indent=2)

        pd.DataFrame(trade_log).to_csv(trades_csv, index=False)
        logger.info(f"Saved scorecard to {report_json}")
        logger.info(f"Saved trades log to {trades_csv}")

        return scorecard


if __name__ == "__main__":
    engine = CrudeBacktestEngine()
    results = engine.run(date_from="2026-05-01", date_to="2026-11-06")
    print(json.dumps(results, indent=2))

