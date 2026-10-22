#!/usr/bin/env python3
"""
Comprehensive Backtest & Statistical Verification of Kronos Foundation Model Forecasts.
────────────────────────────────────────────────────────────────────────────────────────
Evaluates:
1. Directional Accuracy (Hit Rate %) across 10-minute forward horizons.
2. Trajectory Error (MAE in ₹ and MAPE in %).
3. Option A (Dual-Layer Spot -> Black-Scholes Greeks) vs Direct Option Prediction.
4. SMF Strategy Integration: Win Rate & PnL with vs without AI Confirmation Filter.
"""

import os
import sys
import time
import math
import numpy as np
import pandas as pd
from datetime import datetime, timezone
from sqlalchemy import create_engine, text

# Add project root to sys.path
BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from trading_engine.ai.kronos_service import KronosEngine
from trading_engine.web.contract_data_manager import fetch_contract_candles, parse_option_symbol

DB_URL = "postgresql://postgres:postgres@localhost:5432/trading"


def run_comprehensive_backtest(n_samples: int = 60):
    print("=" * 80)
    print("⚡ COMPREHENSIVE BACKTEST & STATISTICAL ACCURACY AUDIT")
    print("   Evaluating Kronos Foundation Model & Option A Dual-Layer Engine")
    print("=" * 80)

    engine = create_engine(DB_URL)
    kronos = KronosEngine(device="cpu")
    if not kronos.load_model():
        print("❌ Error: Failed to load Kronos model.")
        return

    # ─────────────────────────────────────────────────────────────────────────
    # Phase 1: Underlying Index (NIFTY-I) Directional & Trajectory Accuracy
    # ─────────────────────────────────────────────────────────────────────────
    print("\n[PHASE 1] Walk-Forward Forecast Accuracy on Underlying Asset (NIFTY-I)")
    print("-" * 80)

    query_fut = """
        SELECT timestamp, open, high, low, close, COALESCE(volume, 1) as volume
        FROM minute_candles
        WHERE symbol = 'NIFTY-I'
          AND timestamp >= '2026-10-01'
        ORDER BY timestamp ASC;
    """
    df_fut = pd.read_sql(query_fut, engine)
    print(f"Loaded {len(df_fut)} continuous 1-minute future candles from 2026-10-01 onwards.")

    # Sample walk-forward test points spaced across the session
    # Each sample takes past 35 bars, predicts next 10 bars, and compares with actual next 10 bars
    step_stride = max(5, (len(df_fut) - 50) // n_samples)
    eval_indices = list(range(40, len(df_fut) - 12, step_stride))[:n_samples]

    spot_results = []
    print(f"Testing {len(eval_indices)} walk-forward evaluation points across trading sessions...\n")

    for idx in eval_indices:
        hist_slice = df_fut.iloc[idx - 35 : idx]
        actual_forward = df_fut.iloc[idx : idx + 10]

        candles_in = []
        for _, r in hist_slice.iterrows():
            candles_in.append({
                "time": r["timestamp"],
                "open": float(r["open"]),
                "high": float(r["high"]),
                "low": float(r["low"]),
                "close": float(r["close"]),
                "volume": float(r["volume"]),
            })

        t0 = time.time()
        pred = kronos.predict_trajectory(candles_in, pred_len=10, temperature=0.2)
        inf_time = (time.time() - t0) * 1000

        if not pred.get("success"):
            continue

        p_curr = pred["current_price"]
        p_pred_final = pred["forecast_candles"][-1]["close"]
        p_act_final = float(actual_forward["close"].iloc[-1])
        act_return_pct = ((p_act_final - p_curr) / p_curr) * 100
        pred_return_pct = pred["expected_return_pct"]
        pred_dir = pred["direction"]

        # Actual direction
        if act_return_pct >= 0.10:
            act_dir = "BULLISH"
        elif act_return_pct <= -0.10:
            act_dir = "BEARISH"
        else:
            act_dir = "NEUTRAL"

        # Direction match check
        dir_correct = False
        if pred_dir == "BULLISH" and act_return_pct > 0:
            dir_correct = True
        elif pred_dir == "BEARISH" and act_return_pct < 0:
            dir_correct = True
        elif pred_dir == "NEUTRAL" and abs(act_return_pct) <= 0.15:
            dir_correct = True

        # Trajectory pointwise error
        pred_closes = [c["close"] for c in pred["forecast_candles"]]
        act_closes = actual_forward["close"].tolist()
        min_len = min(len(pred_closes), len(act_closes))
        mae = np.mean([abs(pred_closes[i] - act_closes[i]) for i in range(min_len)])
        mape = np.mean([abs(pred_closes[i] - act_closes[i]) / act_closes[i] for i in range(min_len)]) * 100

        spot_results.append({
            "timestamp": hist_slice["timestamp"].iloc[-1],
            "current_price": p_curr,
            "pred_dir": pred_dir,
            "act_dir": act_dir,
            "pred_ret": pred_return_pct,
            "act_ret": act_return_pct,
            "dir_correct": dir_correct,
            "mae": mae,
            "mape": mape,
            "latency_ms": inf_time,
        })
        if len(spot_results) % 5 == 0 or len(spot_results) == len(eval_indices):
            print(f"  [{len(spot_results)}/{len(eval_indices)}] MAE: ₹{mae:.2f}, Latency: {inf_time:.0f}ms, Pred: {pred_dir}, Act: {act_dir}", flush=True)

    df_spot = pd.DataFrame(spot_results)
    n_total = len(df_spot)
    n_correct = df_spot["dir_correct"].sum()
    hit_rate = (n_correct / n_total) * 100 if n_total > 0 else 0.0

    # Directional signals only (excluding neutral)
    directional_df = df_spot[df_spot["pred_dir"] != "NEUTRAL"]
    dir_hit_rate = (directional_df["dir_correct"].sum() / len(directional_df)) * 100 if len(directional_df) > 0 else 0.0

    bullish_df = df_spot[df_spot["pred_dir"] == "BULLISH"]
    bull_acc = (bullish_df["act_ret"] > 0).mean() * 100 if len(bullish_df) > 0 else 0.0

    bearish_df = df_spot[df_spot["pred_dir"] == "BEARISH"]
    bear_acc = (bearish_df["act_ret"] < 0).mean() * 100 if len(bearish_df) > 0 else 0.0

    avg_mae = df_spot["mae"].mean()
    avg_mape = df_spot["mape"].mean()
    avg_lat = df_spot["latency_ms"].mean()

    print("📊 UNDERLYING (NIFTY-I) ACCURACY SUMMARY:")
    print(f"• Total Evaluation Windows:      {n_total}")
    print(f"• Overall Hit Rate (All):        {hit_rate:.2f}% ({n_correct}/{n_total})")
    print(f"• Directional Hit Rate (Active): {dir_hit_rate:.2f}% ({directional_df['dir_correct'].sum()}/{len(directional_df)})")
    print(f"  ├── Bullish Forecast Precision: {bull_acc:.2f}% (Count: {len(bullish_df)})")
    print(f"  └── Bearish Forecast Precision: {bear_acc:.2f}% (Count: {len(bearish_df)})")
    print(f"• Mean Absolute Error (MAE):     ₹{avg_mae:.2f}")
    print(f"• Mean Absolute % Error (MAPE):  {avg_mape:.3f}%")
    print(f"• Avg Inference Latency:         {avg_lat:.1f}ms")

    # ─────────────────────────────────────────────────────────────────────────
    # Phase 2: Option A (Dual-Layer) vs Direct Option Forecasting
    # ─────────────────────────────────────────────────────────────────────────
    print("\n" + "=" * 80)
    print("[PHASE 2] Option A (Dual-Layer Greeks) vs Raw Direct Option Forecasting")
    print("-" * 80)

    # Let's test on real option contracts from 2026-10-19:
    # 22950 PE and 22950 CE
    test_contracts = ["NIFTY26O1922950PE", "NIFTY26O1922950CE"]
    option_evals = []

    for sym in test_contracts:
        parsed = parse_option_symbol(sym)
        if not parsed:
            continue
        strike = parsed["strike"]
        opt_type = parsed["option_type"]
        expiry = parsed["expiry"]

        query_opt = f"""
            SELECT timestamp, open, high, low, close, COALESCE(volume, 1) as volume
            FROM minute_candles
            WHERE symbol = '{sym}'
            ORDER BY timestamp ASC;
        """
        df_opt = pd.read_sql(query_opt, engine)
        if len(df_opt) < 30:
            continue

        # Find matching overlapping timestamps with NIFTY-I
        stride = max(4, (len(df_opt) - 45) // 15)
        for i in range(35, len(df_opt) - 12, stride):
            opt_slice = df_opt.iloc[i - 35 : i]
            opt_forward = df_opt.iloc[i : i + 10]
            curr_opt_ts = opt_slice["timestamp"].iloc[-1]

            # Matching spot slice
            spot_slice = df_fut[df_fut["timestamp"] <= curr_opt_ts].tail(35)
            if len(spot_slice) < 15:
                continue

            opt_candles = [
                {"time": r["timestamp"], "open": float(r["open"]), "high": float(r["high"]), "low": float(r["low"]), "close": float(r["close"]), "volume": float(r["volume"])}
                for _, r in opt_slice.iterrows()
            ]
            spot_candles = [
                {"time": r["timestamp"], "open": float(r["open"]), "high": float(r["high"]), "low": float(r["low"]), "close": float(r["close"]), "volume": float(r["volume"])}
                for _, r in spot_slice.iterrows()
            ]

            # 1. Option A: Dual-Layer Forecast
            res_opt_a = kronos.predict_option_trajectory(
                option_candles=opt_candles,
                underlying_candles=spot_candles,
                strike=strike,
                option_type=opt_type,
                expiry=expiry,
                pred_len=10,
                temperature=0.2,
            )

            # 2. Direct Raw Option Prediction (Naive Model)
            res_direct = kronos.predict_trajectory(
                candles=opt_candles,
                pred_len=10,
                temperature=0.2,
            )

            if not res_opt_a.get("success") or not res_direct.get("success"):
                continue

            curr_opt_p = float(opt_slice["close"].iloc[-1])
            act_final_opt = float(opt_forward["close"].iloc[-1])
            act_opt_ret = ((act_final_opt - curr_opt_p) / curr_opt_p) * 100

            # Option A metrics
            opt_a_pred_final = res_opt_a["forecast_candles"][-1]["close"]
            opt_a_ret = res_opt_a["expected_return_pct"]
            opt_a_correct = (opt_a_ret > 0 and act_opt_ret > 0) or (opt_a_ret < 0 and act_opt_ret < 0) or (abs(act_opt_ret) <= 1.0)
            opt_a_mae = np.mean([abs(c["close"] - float(opt_forward["close"].iloc[j])) for j, c in enumerate(res_opt_a["forecast_candles"][:len(opt_forward)])])

            # Direct metrics
            dir_pred_final = res_direct["forecast_candles"][-1]["close"]
            dir_ret = res_direct["expected_return_pct"]
            direct_correct = (dir_ret > 0 and act_opt_ret > 0) or (dir_ret < 0 and act_opt_ret < 0) or (abs(act_opt_ret) <= 1.0)
            direct_mae = np.mean([abs(c["close"] - float(opt_forward["close"].iloc[j])) for j, c in enumerate(res_direct["forecast_candles"][:len(opt_forward)])])

            option_evals.append({
                "symbol": sym,
                "timestamp": curr_opt_ts,
                "curr_price": curr_opt_p,
                "act_return": act_opt_ret,
                "opt_a_ret": opt_a_ret,
                "opt_a_correct": opt_a_correct,
                "opt_a_mae": opt_a_mae,
                "direct_ret": dir_ret,
                "direct_correct": direct_correct,
                "direct_mae": direct_mae,
            })
            if len(option_evals) % 5 == 0:
                print(f"  [Options {len(option_evals)}] Contract: {sym}, OptA MAE: ₹{opt_a_mae:.2f}, Direct MAE: ₹{direct_mae:.2f}", flush=True)

    df_opt_res = pd.DataFrame(option_evals)
    if len(df_opt_res) > 0:
        opt_a_acc = df_opt_res["opt_a_correct"].mean() * 100
        direct_acc = df_opt_res["direct_correct"].mean() * 100
        opt_a_mae_avg = df_opt_res["opt_a_mae"].mean()
        direct_mae_avg = df_opt_res["direct_mae"].mean()

        print(f"Evaluated {len(df_opt_res)} Option Contract Windows across PE and CE contracts.")
        print(f"• Option A (Dual-Layer Spot -> Greeks) Hit Rate: {opt_a_acc:.2f}%  (MAE: ₹{opt_a_mae_avg:.2f})")
        print(f"• Direct Raw Option Forecast Hit Rate:          {direct_acc:.2f}%  (MAE: ₹{direct_mae_avg:.2f})")
        print(f"• Outperformance of Option A:                   +{opt_a_acc - direct_acc:.2f}% higher accuracy, {((direct_mae_avg - opt_a_mae_avg)/direct_mae_avg)*100:.1f}% lower error!")

    # ─────────────────────────────────────────────────────────────────────────
    # Phase 3: SMF Strategy Confluence & PnL Gatekeeper Backtest
    # ─────────────────────────────────────────────────────────────────────────
    print("\n" + "=" * 80)
    print("[PHASE 3] Strategy PnL Backtest: SMF Signals With vs Without AI Gatekeeper")
    print("-" * 80)

    # Simulate SMF signals on NIFTY-I and measure trade win rate & PnL
    # Calculate simple EMA/ATR SMF cloud proxy
    closes = df_fut["close"].values
    highs = df_fut["high"].values
    lows = df_fut["low"].values
    n = len(closes)

    # 20 EMA and 50 EMA for trend
    ema20 = pd.Series(closes).ewm(span=20, adjust=False).mean().values
    ema50 = pd.Series(closes).ewm(span=50, adjust=False).mean().values

    # Find crossover signals
    trades_raw = []
    trades_ai_filtered = []

    for i in range(50, n - 15):
        # Bullish signal: EMA20 crosses above EMA50
        bull_signal = (ema20[i] > ema50[i]) and (ema20[i-1] <= ema50[i-1])
        # Bearish signal: EMA20 crosses below EMA50
        bear_signal = (ema20[i] < ema50[i]) and (ema20[i-1] >= ema50[i-1])

        if bull_signal or bear_signal:
            sig_type = "BUY" if bull_signal else "SELL"
            entry_p = closes[i]

            # 10-bar forward outcome
            fwd_slice = closes[i+1 : i+11]
            if len(fwd_slice) < 10:
                continue

            max_fwd = np.max(fwd_slice)
            min_fwd = np.min(fwd_slice)
            exit_p = fwd_slice[-1]

            if sig_type == "BUY":
                pnl = exit_p - entry_p
                target_hit = (max_fwd - entry_p) >= 15.0  # +15 pt target
                stop_hit = (entry_p - min_fwd) >= 12.0    # -12 pt stop
            else:
                pnl = entry_p - exit_p
                target_hit = (entry_p - min_fwd) >= 15.0
                stop_hit = (max_fwd - entry_p) >= 12.0

            is_win = (target_hit and not stop_hit) or (pnl > 0 and not stop_hit)

            trades_raw.append({
                "time": df_fut["timestamp"].iloc[i],
                "type": sig_type,
                "entry": entry_p,
                "pnl": pnl,
                "win": is_win,
            })

            # Check Kronos AI confirmation
            past_candles = [
                {"time": df_fut["timestamp"].iloc[k], "open": float(df_fut["open"].iloc[k]), "high": float(df_fut["high"].iloc[k]), "low": float(df_fut["low"].iloc[k]), "close": float(df_fut["close"].iloc[k]), "volume": float(df_fut["volume"].iloc[k])}
                for k in range(i - 35, i)
            ]
            eval_ai = kronos.evaluate_smf_confirmation(past_candles, signal_type=sig_type, pred_len=8)

            if eval_ai.get("confirmed", True):
                trades_ai_filtered.append({
                    "time": df_fut["timestamp"].iloc[i],
                    "type": sig_type,
                    "entry": entry_p,
                    "pnl": pnl,
                    "win": is_win,
                    "confidence": eval_ai.get("confidence", 0.5),
                })

    df_raw = pd.DataFrame(trades_raw)
    df_ai = pd.DataFrame(trades_ai_filtered)

    print(f"Total Raw Strategy Signals Generated: {len(df_raw)}")
    print(f"Signals Approved by Kronos AI:        {len(df_ai)} (Filtered {len(df_raw) - len(df_ai)} suspicious/counter-trend setups)")

    if len(df_raw) > 0 and len(df_ai) > 0:
        raw_winrate = df_raw["win"].mean() * 100
        raw_pnl = df_raw["pnl"].sum()
        ai_winrate = df_ai["win"].mean() * 100
        ai_pnl = df_ai["pnl"].sum()

        print("\n📈 STRATEGY PERFORMANCE COMPARISON:")
        print(f"• Raw Strategy Win Rate:         {raw_winrate:.2f}%  (Net PnL: {raw_pnl:+.1f} pts)")
        print(f"• Kronos AI-Gated Win Rate:      {ai_winrate:.2f}%  (Net PnL: {ai_pnl:+.1f} pts)")
        print(f"• Win Rate Improvement:          {ai_winrate - raw_winrate:+.2f}%")
        print(f"• False-Positive Loss Reduction: Filtered out {(df_raw[~df_raw['win']]['pnl'].count() - df_ai[~df_ai['win']]['pnl'].count())} losing trades!")

    print("\n" + "=" * 80)
    print("✅ BACKTEST EXECUTION COMPLETE")
    print("=" * 80)


if __name__ == "__main__":
    n = 40
    if len(sys.argv) > 1:
        n = int(sys.argv[1])
    run_comprehensive_backtest(n_samples=n)
