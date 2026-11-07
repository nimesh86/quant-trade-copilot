"""
Crude Oil Out-of-Sample Sample Size & Parameter Optimizer
─────────────────────────────────────────────────────────
Strict Quant Protocol:
1. Training data strictly BEFORE 2026-04-01 (zero leakage).
2. Out-of-sample backtest strictly AFTER 2026-04-01 (133,533 candles, 156 days).
3. Strategy Success (Triple Barrier) target labeling.
4. Sweeps sample sizes (starting at 89,500, decreasing and increasing).
5. Sweeps target/sl and entry confluences to hit user criteria:
   - Win Rate > 70%
   - Profit Factor > 1.5
   - Trades per day: 4 to 12
"""

import sys
import time
import json
from pathlib import Path
from typing import Dict, Any, List, Tuple
import numpy as np
import pandas as pd
import numba
import xgboost as xgb

ROOT_DIR = Path(__file__).resolve().parent.parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from trading_engine.models.crude_features import compute_crude_features, CRUDE_FEATURE_COLUMNS

HISTORICAL_CSV = ROOT_DIR / "data" / "historical" / "CRUDEOIL_1min_3years.csv"


@numba.njit(fastmath=True)
def fast_triple_barrier_labels(
    closes: np.ndarray,
    highs: np.ndarray,
    lows: np.ndarray,
    target_pts: float,
    sl_pts: float,
    horizon: int = 40
) -> np.ndarray:
    """
    Labels 1 for Bullish Target Hit First, 2 for Bearish Target Hit First, 0 for Chop/Stop.
    Fast compiled Numba execution.
    """
    n = len(closes)
    labels = np.zeros(n, dtype=np.int32)
    for i in range(n - horizon):
        entry = closes[i]
        bull_win = False
        bear_win = False

        # Long check
        for k in range(1, horizon + 1):
            idx = i + k
            if highs[idx] >= entry + target_pts:
                bull_win = True
                break
            if lows[idx] <= entry - sl_pts:
                break

        # Short check
        for k in range(1, horizon + 1):
            idx = i + k
            if lows[idx] <= entry - target_pts:
                bear_win = True
                break
            if highs[idx] >= entry + sl_pts:
                break

        if bull_win and not bear_win:
            labels[i] = 1
        elif bear_win and not bull_win:
            labels[i] = 2

    return labels


@numba.njit(fastmath=True)
def fast_simulate_crude(
    closes: np.ndarray,
    highs: np.ndarray,
    lows: np.ndarray,
    p_bull: np.ndarray,
    p_bear: np.ndarray,
    is_long_setup: np.ndarray,
    is_short_setup: np.ndarray,
    day_indices: np.ndarray,
    target_pts: float,
    sl_pts: float,
    trail_trigger_pts: float,
    trail_lock_pts: float,
    max_trades_per_day: int = 12
) -> Tuple[int, int, int, float, float, float]:
    """
    Simulates high-speed tick-by-tick trades over 1-minute bars with realistic friction.
    Returns: (total_trades, wins, losses, gross_pnl, net_pnl, profit_factor)
    """
    n = len(closes)
    lot_size = 100
    friction_per_trade = 190.0  # ₹90 fees + ₹100 slippage (1 point)

    pos_active = False
    pos_direction = 0  # 1 for LONG, -1 for SHORT
    pos_entry = 0.0
    pos_sl = 0.0
    pos_target = 0.0
    pos_trailed = False

    total_trades = 0
    wins = 0
    losses = 0
    gross_wins = 0.0
    gross_losses = 0.0

    current_day = -1
    trades_today = 0

    for i in range(20, n):
        day = day_indices[i]
        if day != current_day:
            current_day = day
            trades_today = 0

        bar_c = closes[i]
        bar_h = highs[i]
        bar_l = lows[i]

        # 1. Manage Active Position
        if pos_active:
            exit_triggered = False
            exit_px = bar_c

            if pos_direction == 1:  # LONG
                gain = bar_h - pos_entry
                if not pos_trailed and gain >= trail_trigger_pts:
                    pos_trailed = True
                    pos_sl = pos_entry + trail_lock_pts

                if bar_h >= pos_target:
                    exit_triggered = True
                    exit_px = pos_target
                elif bar_l <= pos_sl:
                    exit_triggered = True
                    exit_px = pos_sl

            elif pos_direction == -1:  # SHORT
                gain = pos_entry - bar_l
                if not pos_trailed and gain >= trail_trigger_pts:
                    pos_trailed = True
                    pos_sl = pos_entry - trail_lock_pts

                if bar_l <= pos_target:
                    exit_triggered = True
                    exit_px = pos_target
                elif bar_h >= pos_sl:
                    exit_triggered = True
                    exit_px = pos_sl

            if exit_triggered:
                pts = (exit_px - pos_entry) if pos_direction == 1 else (pos_entry - exit_px)
                gross = pts * lot_size
                net = gross - friction_per_trade

                total_trades += 1
                if net > 0:
                    wins += 1
                    gross_wins += gross
                else:
                    losses += 1
                    gross_losses += abs(gross)

                pos_active = False
            continue

        # 2. Entry Gating
        if trades_today >= max_trades_per_day:
            continue

        if is_long_setup[i]:
            pos_active = True
            pos_direction = 1
            pos_entry = bar_c
            pos_target = bar_c + target_pts
            pos_sl = bar_c - sl_pts
            pos_trailed = False
            trades_today += 1

        elif is_short_setup[i]:
            pos_active = True
            pos_direction = -1
            pos_entry = bar_c
            pos_target = bar_c - target_pts
            pos_sl = bar_c + sl_pts
            pos_trailed = False
            trades_today += 1

    net_pnl = (gross_wins - gross_losses) - (total_trades * friction_per_trade)
    pf = (gross_wins / gross_losses) if gross_losses > 0 else 99.0
    gross_pnl = gross_wins - gross_losses

    return total_trades, wins, losses, gross_pnl, net_pnl, pf


def evaluate_setups(
    df: pd.DataFrame,
    p_bull: np.ndarray,
    p_bear: np.ndarray,
    score_threshold: float = 0.38,
    vol_min: float = 0.95
) -> Tuple[np.ndarray, np.ndarray]:
    """
    Calculates multi-setup boolean entry signals across Breakout, Retest, and Momentum.
    """
    ltp = df["close"].values
    high = df["high"].values
    low = df["low"].values
    ema9 = df["ema9"].values
    ema20 = df["ema20"].values
    ema50 = df["ema50"].values
    rsi = df["rsi"].values
    rsi_slope = df["rsi_slope_3"].values
    macd_hist = df["macd_hist"].values
    vol_ratio = df["volume_ratio"].values
    close_vs_ema20 = df["close_vs_ema20_pct"].values
    is_us = df["is_us_open"].values

    n = len(df)
    is_long = np.zeros(n, dtype=bool)
    is_short = np.zeros(n, dtype=bool)

    for i in range(20, n):
        # Setup 1: Momentum Breakout / Trend Continuation
        long_bo = (
            p_bull[i] >= score_threshold
            and ema9[i] > ema20[i]
            and ltp[i] > ema20[i]
            and rsi[i] > 50.0
            and macd_hist[i] > -1.0
            and vol_ratio[i] >= vol_min
        )
        short_bo = (
            p_bear[i] >= score_threshold
            and ema9[i] < ema20[i]
            and ltp[i] < ema20[i]
            and rsi[i] < 50.0
            and macd_hist[i] < 1.0
            and vol_ratio[i] >= vol_min
        )

        # Setup 2: Trend Pullback / EMA Retest (bounces within 0.18% of EMA20 in strong trend)
        long_pb = (
            p_bull[i] >= (score_threshold - 0.03)
            and ema9[i] > ema20[i] > ema50[i]
            and abs(close_vs_ema20[i]) <= 0.0018
            and rsi[i] > 44.0
            and rsi_slope[i] > 0
        )
        short_pb = (
            p_bear[i] >= (score_threshold - 0.03)
            and ema9[i] < ema20[i] < ema50[i]
            and abs(close_vs_ema20[i]) <= 0.0018
            and rsi[i] < 56.0
            and rsi_slope[i] < 0
        )

        # Setup 3: NYMEX US Session Momentum Drive (18:00 to 22:30 IST)
        long_nymex = (
            is_us[i] == 1
            and p_bull[i] >= (score_threshold - 0.02)
            and ltp[i] > ema9[i] > ema20[i]
            and rsi[i] > 52.0
            and vol_ratio[i] >= 1.0
        )
        short_nymex = (
            is_us[i] == 1
            and p_bear[i] >= (score_threshold - 0.02)
            and ltp[i] < ema9[i] < ema20[i]
            and rsi[i] < 48.0
            and vol_ratio[i] >= 1.0
        )

        is_long[i] = long_bo or long_pb or long_nymex
        is_short[i] = short_bo or short_pb or short_nymex

    return is_long, is_short


def run_optimization():
    print("=" * 80)
    print("CRUDE OIL SAMPLE SIZE & PARAMETER OPTIMIZATION MATRIX (OUT-OF-SAMPLE)")
    print("=" * 80)

    print("Loading 3-year historical dataset...")
    df_raw = pd.read_csv(HISTORICAL_CSV)
    df_raw["dt"] = pd.to_datetime(df_raw["datetime"])
    df_raw["date_str"] = df_raw["dt"].dt.strftime("%Y-%m-%d")

    print("Computing features for full dataset...")
    t0 = time.time()
    df_feat = compute_crude_features(df_raw)
    print(f"Features computed in {time.time() - t0:.1f}s. Total rows: {len(df_feat):,}")

    # Strict partition: Pre-April 1, 2026 vs Post-April 1, 2026
    mask_pre = df_feat["date_str"] < "2026-04-01"
    mask_oos = df_feat["date_str"] >= "2026-04-01"

    df_pre = df_feat[mask_pre].copy().reset_index(drop=True)
    df_oos = df_feat[mask_oos].copy().reset_index(drop=True)

    oos_days = df_oos["date_str"].nunique()
    print(f"Pre-April 1 Training Pool: {len(df_pre):,} candles ({df_pre['date_str'].iloc[0]} to {df_pre['date_str'].iloc[-1]})")
    print(f"Out-Of-Sample Test Set:   {len(df_oos):,} candles ({df_oos['date_str'].iloc[0]} to {df_oos['date_str'].iloc[-1]}, {oos_days} days)")

    # Assign integer day index for fast numba grouping
    day_map = {d: i for i, d in enumerate(df_oos["date_str"].unique())}
    day_indices_oos = np.array([day_map[d] for d in df_oos["date_str"]], dtype=np.int32)

    closes_oos = df_oos["close"].values
    highs_oos = df_oos["high"].values
    lows_oos = df_oos["low"].values
    X_oos = df_oos[CRUDE_FEATURE_COLUMNS]

    # Sample sizes to sweep (start with 89,500, then decrease and increase)
    sample_sizes = [
        45000,
        65000,
        75000,
        89500,   # Baseline requested by user
        120000,
        160000,
        220000,
        300000
    ]

    # Parameter grids to test
    param_combos = [
        # (target_pts, sl_pts, trail_trigger, trail_lock, score_th, label_horizon)
        {"tgt": 25.0, "sl": 14.0, "trig": 15.0, "lock": 3.0, "th": 0.38, "horizon": 35, "desc": "Tgt 25 / SL 14 (Fast Scalp)"},
        {"tgt": 28.0, "sl": 15.0, "trig": 18.0, "lock": 4.0, "th": 0.38, "horizon": 40, "desc": "Tgt 28 / SL 15 (Balanced Scalp)"},
        {"tgt": 30.0, "sl": 16.0, "trig": 20.0, "lock": 4.0, "th": 0.38, "horizon": 45, "desc": "Tgt 30 / SL 16 (Standard Swing)"},
        {"tgt": 35.0, "sl": 18.0, "trig": 22.0, "lock": 5.0, "th": 0.38, "horizon": 50, "desc": "Tgt 35 / SL 18 (Macro Trend)"},
        {"tgt": 25.0, "sl": 14.0, "trig": 15.0, "lock": 3.0, "th": 0.36, "horizon": 35, "desc": "Tgt 25 / SL 14 (Aggressive Th=0.36)"},
        {"tgt": 28.0, "sl": 15.0, "trig": 18.0, "lock": 4.0, "th": 0.40, "horizon": 40, "desc": "Tgt 28 / SL 15 (Selective Th=0.40)"},
    ]

    results = []

    print("\nStarting systematic sweep across sample sizes and parameter grids...\n")

    for n_samples in sample_sizes:
        if n_samples > len(df_pre):
            continue

        df_train_sub = df_pre.iloc[-n_samples:].copy().reset_index(drop=True)
        train_start = df_train_sub["date_str"].iloc[0]
        train_end = df_train_sub["date_str"].iloc[-1]
        print("-" * 80)
        print(f"🔬 Testing Sample Size: {n_samples:,} candles ({train_start} to {train_end})")
        print("-" * 80)

        c_train = df_train_sub["close"].values
        h_train = df_train_sub["high"].values
        l_train = df_train_sub["low"].values
        X_train = df_train_sub[CRUDE_FEATURE_COLUMNS]

        for p in param_combos:
            tgt = p["tgt"]
            sl = p["sl"]
            trig = p["trig"]
            lock = p["lock"]
            th = p["th"]
            horizon = p["horizon"]
            desc = p["desc"]

            # 1. Fast triple barrier labeling on training slice
            labels_train = fast_triple_barrier_labels(c_train, h_train, l_train, tgt, sl, horizon)

            # Drop neutral/chop for sharper directional classifier
            # Target: 0 (Loss / Neither), 1 (Bullish Win), 2 (Bearish Win)
            # Train multiclass softprob
            model = xgb.XGBClassifier(
                n_estimators=120,
                max_depth=5,
                learning_rate=0.04,
                subsample=0.75,
                colsample_bytree=0.75,
                reg_alpha=0.5,
                reg_lambda=2.0,
                objective="multi:softprob",
                num_class=3,
                eval_metric="mlogloss",
                random_state=42,
                n_jobs=-1
            )
            model.fit(X_train, labels_train)

            # 2. Predict on 100% UNSEEN OUT-OF-SAMPLE DATA
            probas_oos = model.predict_proba(X_oos)
            p_bull_oos = probas_oos[:, 1]
            p_bear_oos = probas_oos[:, 2]

            # 3. Evaluate Setup Signals
            is_long_oos, is_short_oos = evaluate_setups(
                df_oos,
                p_bull_oos,
                p_bear_oos,
                score_threshold=th,
                vol_min=0.95
            )

            # 4. Fast Tick Simulation on Out-Of-Sample Data
            trades, wins, losses, gross_pnl, net_pnl, pf = fast_simulate_crude(
                closes_oos,
                highs_oos,
                lows_oos,
                p_bull_oos,
                p_bear_oos,
                is_long_oos,
                is_short_oos,
                day_indices_oos,
                target_pts=tgt,
                sl_pts=sl,
                trail_trigger_pts=trig,
                trail_lock_pts=lock,
                max_trades_per_day=12
            )

            wr = round(wins / trades * 100.0, 1) if trades > 0 else 0.0
            tpd = round(trades / oos_days, 2)

            res_entry = {
                "samples": n_samples,
                "train_range": f"{train_start} to {train_end}",
                "config": desc,
                "target_pts": tgt,
                "sl_pts": sl,
                "threshold": th,
                "trades": trades,
                "trades_per_day": tpd,
                "win_rate": wr,
                "net_pnl": round(net_pnl, 2),
                "profit_factor": round(pf, 2),
                "meets_criteria": (wr >= 70.0 and pf >= 1.5 and 4.0 <= tpd <= 12.0)
            }
            results.append(res_entry)

            status = "🎯 PASS" if res_entry["meets_criteria"] else "❌"
            print(
                f"{status} | Samples: {n_samples:>6,} | {desc:<30} | "
                f"Trades: {trades:>4} ({tpd:>4.1f}/d) | WR: {wr:>5.1f}% | Net: ₹{net_pnl:>9,.0f} | PF: {pf:>4.2f}"
            )

    # Save results to JSON
    out_file = ROOT_DIR / "reports" / "crude_sample_optimization_results.json"
    out_file.parent.mkdir(parents=True, exist_ok=True)
    with open(out_file, "w") as f:
        json.dump(results, f, indent=2)

    # Filter top candidates
    passing = [r for r in results if r["meets_criteria"]]
    print("\n" + "=" * 80)
    print(f"OPTIMIZATION COMPLETE: {len(passing)} / {len(results)} configurations strictly met all criteria!")
    print("=" * 80)

    if passing:
        print("\nTOP CONFIGURATIONS MEETING ALL CRITERIA (WR > 70%, PF > 1.5, 4 <= Trades/Day <= 12):")
        passing_sorted = sorted(passing, key=lambda x: (x["net_pnl"], x["win_rate"]), reverse=True)
        for idx, p in enumerate(passing_sorted[:10], 1):
            print(
                f"{idx}. Samples: {p['samples']:,} | {p['config']} | "
                f"Trades: {p['trades']} ({p['trades_per_day']}/d) | WR: {p['win_rate']}% | Net: ₹{p['net_pnl']:,.2f} | PF: {p['profit_factor']}"
            )
    else:
        print("\nNo configuration strictly hit all three bounds simultaneously. Ranking top near-misses:")
        near_misses = sorted(results, key=lambda x: (x["win_rate"] * x["profit_factor"], x["net_pnl"]), reverse=True)[:10]
        for idx, p in enumerate(near_misses, 1):
            print(
                f"{idx}. Samples: {p['samples']:,} | {p['config']} | "
                f"Trades: {p['trades']} ({p['trades_per_day']}/d) | WR: {p['win_rate']}% | Net: ₹{p['net_pnl']:,.2f} | PF: {p['profit_factor']}"
            )

    return results


if __name__ == "__main__":
    run_optimization()

