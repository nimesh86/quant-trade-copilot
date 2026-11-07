"""
Crude Oil Trade Frequency Optimization Matrix
─────────────────────────────────────────────
Explores tuning parameters and multi-setup expansions to increase trade count
from 1.47 trades/day toward the 3.0–3.8 trades/day target (closer to the 4 trades/day cap),
while measuring the impact on Win Rate, Net Profit, and Profit Factor.
"""

import sys
import json
from pathlib import Path
from typing import Dict, Any, List
import pandas as pd
import numpy as np

ROOT_DIR = Path(__file__).resolve().parent.parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from trading_engine.models.crude_features import compute_crude_features, CRUDE_FEATURE_COLUMNS
from trading_engine.backtest_engine.crude_backtest_engine import CrudeBacktestEngine

HISTORICAL_CSV = ROOT_DIR / "data" / "historical" / "CRUDEOIL_1min_3years.csv"


def run_experiment(
    df_test: pd.DataFrame,
    score_threshold: float = 0.40,
    target_pts: float = 40.0,
    sl_pts: float = 20.0,
    trail_trigger_pts: float = 25.0,
    trail_lock_pts: float = 5.0,
    vol_ratio_min: float = 1.0,
    enable_pullback_setup: bool = True,
    enable_reversal_setup: bool = True,
    max_trades_per_day: int = 4
) -> Dict[str, Any]:
    lot_size = 100
    capital = 20000.0
    active_position = None
    trade_log = []
    trades_today = 0
    current_day_str = ""

    for i in range(20, len(df_test)):
        row = df_test.iloc[i]
        dt = row["dt"]
        day_str = row["date_str"]
        ltp = float(row["close"])
        bar_high = float(row["high"])
        bar_low = float(row["low"])

        if day_str != current_day_str:
            current_day_str = day_str
            trades_today = 0

        # Position monitoring
        if active_position is not None:
            pos = active_position
            direction = pos["direction"]
            entry_px = pos["entry_price"]
            sl = pos["sl"]
            target = pos["target"]
            trail_active = pos["trail_active"]

            exit_triggered = False
            exit_px = ltp
            exit_reason = ""

            if direction == "LONG":
                gain = bar_high - entry_px
                if not trail_active and gain >= trail_trigger_pts:
                    pos["trail_active"] = True
                    pos["sl"] = entry_px + trail_lock_pts
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
                    exit_reason = "SESSION CLOSE"

            elif direction == "SHORT":
                gain = entry_px - bar_low
                if not trail_active and gain >= trail_trigger_pts:
                    pos["trail_active"] = True
                    pos["sl"] = entry_px - trail_lock_pts
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
                    exit_reason = "SESSION CLOSE"

            if exit_triggered:
                pts = round(exit_px - entry_px if direction == "LONG" else entry_px - exit_px, 2)
                gross = round(pts * lot_size, 2)
                charges = 90.0 + 100.0  # ₹90 fees + ₹100 slippage
                net = round(gross - charges, 2)
                capital = round(capital + net, 2)
                trade_log.append({
                    "date": day_str,
                    "direction": direction,
                    "points": pts,
                    "gross": gross,
                    "net": net,
                    "reason": exit_reason
                })
                active_position = None
            continue

        # Market Hours
        if dt.hour < 9 or (dt.hour == 9 and dt.minute < 15):
            continue
        if dt.hour > 22 or (dt.hour == 22 and dt.minute > 45):
            continue

        if max_trades_per_day > 0 and trades_today >= max_trades_per_day:
            continue

        p_bull = float(row["p_bull"])
        p_bear = float(row["p_bear"])
        rsi = float(row["rsi"])
        ema9 = float(row["ema9"])
        ema20 = float(row["ema20"])
        ema50 = float(row["ema50"])
        macd_hist = float(row["macd_hist"])
        vol_ratio = float(row["volume_ratio"])
        close_vs_ema20 = float(row["close_vs_ema20_pct"])

        # Setup 1: Momentum Breakout
        long_breakout = (
            p_bull >= score_threshold
            and ema9 > ema20
            and ltp > ema20
            and rsi > 51.0
            and macd_hist > -1.0
            and vol_ratio >= vol_ratio_min
        )

        short_breakout = (
            p_bear >= score_threshold
            and ema9 < ema20
            and ltp < ema20
            and rsi < 49.0
            and macd_hist < 1.0
            and vol_ratio >= vol_ratio_min
        )

        # Setup 2: Trend Pullback / EMA Retest
        long_pullback = False
        short_pullback = False
        if enable_pullback_setup:
            long_pullback = (
                p_bull >= (score_threshold - 0.03)
                and ema9 > ema20 > ema50
                and abs(close_vs_ema20) <= 0.0015  # within 0.15% of EMA20
                and rsi > 45.0
                and row["rsi_slope_3"] > 0
            )
            short_pullback = (
                p_bear >= (score_threshold - 0.03)
                and ema9 < ema20 < ema50
                and abs(close_vs_ema20) <= 0.0015
                and rsi < 55.0
                and row["rsi_slope_3"] < 0
            )

        # Setup 3: Mean Reversion / Trap
        long_reversal = False
        short_reversal = False
        if enable_reversal_setup:
            long_reversal = (
                p_bull >= score_threshold
                and rsi < 32.0
                and row["rsi_slope_3"] > 0.8
                and float(row["bollinger_pct"]) < 0.10
            )
            short_reversal = (
                p_bear >= score_threshold
                and rsi > 68.0
                and row["rsi_slope_3"] < -0.8
                and float(row["bollinger_pct"]) > 0.90
            )

        is_long = long_breakout or long_pullback or long_reversal
        is_short = short_breakout or short_pullback or short_reversal

        if is_long:
            active_position = {
                "direction": "LONG",
                "entry_price": ltp,
                "target": round(ltp + target_pts, 2),
                "sl": round(ltp - sl_pts, 2),
                "trail_active": False,
            }
            trades_today += 1
        elif is_short:
            active_position = {
                "direction": "SHORT",
                "entry_price": ltp,
                "target": round(ltp - target_pts, 2),
                "sl": round(ltp + sl_pts, 2),
                "trail_active": False,
            }
            trades_today += 1

    total_trades = len(trade_log)
    if total_trades == 0:
        return {"trades": 0, "win_rate": 0, "net_pnl": 0, "profit_factor": 0, "trades_per_day": 0}

    wins = [t for t in trade_log if t["net"] > 0]
    losses = [t for t in trade_log if t["net"] <= 0]
    wr = len(wins) / total_trades * 100.0
    net = sum(t["net"] for t in trade_log)
    gross_w = sum(t["gross"] for t in wins)
    gross_l = abs(sum(t["gross"] for t in losses))
    pf = round(gross_w / gross_l, 2) if gross_l > 0 else 99.0
    tpd = round(total_trades / df_test["date_str"].nunique(), 2)

    return {
        "trades": total_trades,
        "trades_per_day": tpd,
        "win_rate": round(wr, 1),
        "net_pnl": round(net, 2),
        "profit_factor": pf,
        "final_capital": round(capital, 2)
    }


def main():
    print("Loading test data...")
    df_raw = pd.read_csv(HISTORICAL_CSV)
    df_raw["dt"] = pd.to_datetime(df_raw["datetime"])
    df_feat = compute_crude_features(df_raw)
    df_feat["date_str"] = df_feat["dt"].dt.strftime("%Y-%m-%d")

    # 6-month window: May 1 to Nov 6, 2026
    mask = (df_feat["date_str"] >= "2026-05-01") & (df_feat["date_str"] <= "2026-11-06")
    df_test = df_feat[mask].copy().reset_index(drop=True)

    # Load trained model
    import joblib
    model_p = ROOT_DIR / "models" / "saved" / "crude_macro_model.pkl"
    pkg = joblib.load(model_p)
    model = pkg["model"]
    feature_cols = pkg["feature_cols"]

    print("Pre-computing model probabilities...")
    X = df_test[feature_cols]
    probas = model.predict_proba(X)
    df_test["p_chop"] = probas[:, 0]
    df_test["p_bull"] = probas[:, 1]
    df_test["p_bear"] = probas[:, 2]

    print("\nRunning Sensitivity Matrix across configurations...\n")
    results = []

    configs = [
        # Baseline
        {"name": "1. Baseline (Current)", "score_th": 0.45, "vol_min": 1.05, "pullback": False, "rev": False, "tgt": 40, "sl": 20},
        # Score threshold sweeps
        {"name": "2. Score Thresh 0.42", "score_th": 0.42, "vol_min": 1.00, "pullback": False, "rev": False, "tgt": 40, "sl": 20},
        {"name": "3. Score Thresh 0.40", "score_th": 0.40, "vol_min": 1.00, "pullback": False, "rev": False, "tgt": 40, "sl": 20},
        {"name": "4. Score Thresh 0.38", "score_th": 0.38, "vol_min": 0.95, "pullback": False, "rev": False, "tgt": 40, "sl": 20},
        # Multi-setup combinations
        {"name": "5. Breakout + Pullback (0.40)", "score_th": 0.40, "vol_min": 1.00, "pullback": True, "rev": False, "tgt": 40, "sl": 20},
        {"name": "6. Triple Setup (BO+PB+REV) 0.40", "score_th": 0.40, "vol_min": 1.00, "pullback": True, "rev": True, "tgt": 40, "sl": 20},
        {"name": "7. Triple Setup (BO+PB+REV) 0.38", "score_th": 0.38, "vol_min": 0.95, "pullback": True, "rev": True, "tgt": 40, "sl": 20},
        {"name": "8. Triple Setup (Tgt:35/SL:18)", "score_th": 0.38, "vol_min": 0.95, "pullback": True, "rev": True, "tgt": 35, "sl": 18, "trail_trig": 20, "trail_lock": 4},
    ]

    for cfg in configs:
        res = run_experiment(
            df_test,
            score_threshold=cfg["score_th"],
            vol_ratio_min=cfg["vol_min"],
            enable_pullback_setup=cfg["pullback"],
            enable_reversal_setup=cfg["rev"],
            target_pts=cfg.get("tgt", 40),
            sl_pts=cfg.get("sl", 20),
            trail_trigger_pts=cfg.get("trail_trig", 25),
            trail_lock_pts=cfg.get("trail_lock", 5)
        )
        res["config"] = cfg["name"]
        results.append(res)
        print(f"{cfg['name']:<35} | Trades: {res['trades']:>4} ({res['trades_per_day']} /day) | WR: {res['win_rate']:>5.1f}% | Net PnL: ₹{res['net_pnl']:>10,.2f} | PF: {res['profit_factor']:>4.2f}")

    return results

if __name__ == "__main__":
    main()

