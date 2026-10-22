"""
Root Cause Ablation Analysis.
Systematically adds and removes each change implemented in Self Training Engine
to isolate the exact financial and mathematical impact of every single component.
"""
import sys
import copy
from pathlib import Path
import numpy as np
import pandas as pd

# Add trading_engine to path
sys.path.insert(0, str(Path(__file__).resolve().parent / "trading_engine"))

from config import EngineConfig
from feeders.historical_db import HistoricalDBFeeder
from adapters.backtest_adapter import BacktestAdapter
from strategy.core_85_strategy import Core85Strategy

print("================================================================================")
print("  STARTING STEP-BY-STEP ROOT CAUSE ABLATION ANALYSIS")
print("================================================================================", flush=True)

# 1. Load Data Feeder once
base_cfg = EngineConfig()
feeder = HistoricalDBFeeder(base_cfg)
feeder.initialize()
spot_tr_raw, feat_tr_raw, tr_idx_raw = feeder.get_training_data()

def run_simulation(cfg: EngineConfig, name: str, session_prime: bool = True, window_end: str = "15:15"):
    # Create Strategy
    strat = Core85Strategy(cfg)
    
    # Configure lookback regime filter
    t_tr = spot_tr_raw.index.time
    if session_prime:
        is_prime = (t_tr >= pd.to_datetime("10:05").time()) & (t_tr <= pd.to_datetime("15:00").time())
    else:
        is_prime = np.ones(len(t_tr), dtype=bool) # raw full lookback
    
    clean_mask = feat_tr_raw[strat.feature_cols if strat.feature_cols else [c for c in feat_tr_raw.columns if c != "atr"]].notna().all(axis=1).values & is_prime
    
    # Custom training to control session_prime and multipliers
    strat.feature_cols = [c for c in feat_tr_raw.columns if c != "atr"]
    c_arr = spot_tr_raw["close"].values
    h_arr = spot_tr_raw["high"].values
    l_arr = spot_tr_raw["low"].values
    atr_arr = feat_tr_raw["atr"].values
    days = spot_tr_raw.index.date
    pos = pd.Series(np.arange(len(spot_tr_raw)))
    day_ends = pos.groupby(days).transform("max").values

    call_win = np.zeros(len(spot_tr_raw), dtype=int)
    put_win = np.zeros(len(spot_tr_raw), dtype=int)
    for i in range(len(spot_tr_raw)):
        end_b = day_ends[i]
        if i >= end_b or np.isnan(atr_arr[i]) or atr_arr[i] <= 0:
            continue
        entry = c_arr[i]
        c_tgt = entry + cfg.target_atr_mult * atr_arr[i]
        c_sl  = entry - cfg.stop_atr_mult * atr_arr[i]
        p_tgt = entry - cfg.target_atr_mult * atr_arr[i]
        p_sl  = entry + cfg.stop_atr_mult * atr_arr[i]
        c_done, p_done = False, False
        for j in range(i + 1, min(end_b + 1, i + 90)):
            if not c_done:
                if h_arr[j] >= c_tgt and l_arr[j] > c_sl:
                    call_win[i] = 1
                    c_done = True
                elif l_arr[j] <= c_sl:
                    c_done = True
            if not p_done:
                if l_arr[j] <= p_tgt and h_arr[j] < p_sl:
                    put_win[i] = 1
                    p_done = True
                elif h_arr[j] >= p_sl:
                    p_done = True
            if c_done and p_done:
                break

    X = feat_tr_raw.iloc[clean_mask][strat.feature_cols].values
    y_c = call_win[clean_mask]
    y_p = put_win[clean_mask]

    import lightgbm as lgb
    strat.model_call = lgb.LGBMClassifier(n_estimators=250, learning_rate=0.03, num_leaves=31, random_state=42, verbose=-1, n_jobs=-1)
    strat.model_put = lgb.LGBMClassifier(n_estimators=250, learning_rate=0.03, num_leaves=31, random_state=42, verbose=-1, n_jobs=-1)
    strat.model_call.fit(X, y_c)
    strat.model_put.fit(X, y_p)
    strat.booster_c = strat.model_call.booster_
    strat.booster_p = strat.model_put.booster_
    strat.is_trained = True

    strat.precompute_batch(feeder.feature_df, feeder.test_indices)

    # Initialize Adapter
    adapter = BacktestAdapter(cfg)
    
    end_t = pd.to_datetime(window_end).time()
    start_t = pd.to_datetime(cfg.trading_start_time).time()

    for bar in feeder.stream_bars():
        exit_rec = adapter.update_position(bar)
        if exit_rec is not None:
            strat.notify_exit(exit_rec)

        if adapter.active_position is None:
            t = bar["timestamp"].time()
            if not (start_t <= t <= end_t):
                continue
            sig = strat.evaluate_bar(bar)
            if sig is not None:
                side, atr, is_sar = sig
                adapter.open_position(
                    symbol=cfg.symbol,
                    side=side,
                    price=bar["close"],
                    atr=atr,
                    timestamp=bar["timestamp"],
                    is_sar=is_sar
                )

    summ = adapter.get_portfolio_summary()
    df = summ.get("trades_df", pd.DataFrame())
    
    tot_trades = len(df)
    wins = (df["is_win"] == True).sum() if tot_trades > 0 else 0
    losses = tot_trades - wins
    wr = (wins / tot_trades * 100.0) if tot_trades > 0 else 0.0
    net_pnl = summ["net_profit"]
    end_cap = summ["final_capital"]
    ret_pct = summ["return_pct"]
    friction = df["total_friction"].sum() if "total_friction" in df.columns else 0.0
    spot_pts = df["gross_pts"].sum() if "gross_pts" in df.columns else 0.0

    print(f"\n--- {name} ---")
    print(f"  Trades: {tot_trades:3d} | Wins: {wins:3d} | Losses: {losses:3d} | WinRate: {wr:.2f}%")
    print(f"  Spot Pts: {spot_pts:+8.2f} pts | Friction: ₹{friction:,.2f}")
    print(f"  Final Capital: ₹{end_cap:,.2f} | Net PnL: ₹{net_pnl:+,.2f} ({ret_pct:+.2f}%)")

    # Monthly summary
    m_pnl = {}
    if tot_trades > 0:
        for m, grp in df.groupby("month"):
            m_pnl[m] = grp["net_pnl_inr"].sum()
        m_str = " | ".join([f"{k}: ₹{v:+,.0f}" for k, v in sorted(m_pnl.items())])
        print(f"  Monthly PnL: {m_str}")

    return {
        "name": name,
        "trades": tot_trades,
        "wins": wins,
        "losses": losses,
        "win_rate": wr,
        "spot_pts": spot_pts,
        "friction": friction,
        "final_capital": end_cap,
        "net_pnl": net_pnl,
        "return_pct": ret_pct,
        "monthly": m_pnl
    }

results = []

# Baseline Configuration A: Original State as first created in trading_engine
# (0.75x Tgt, 1.75x SL, Multi-step Ladder ON, Window 10:05-13:30, No Cooldown, Unfiltered Lookback)
cfg_orig = EngineConfig(
    target_atr_mult=0.75,
    stop_atr_mult=1.75,
    use_trailing_ladder=True,
    be_trigger_mult=0.35,
    be_lock_amt_mult=0.05,
    profit_lock_trigger=0.55,
    profit_lock_amt=0.30,
    loss_cooldown_mins=0,
    consecutive_losses_for_cooldown=999
)
r1 = run_simulation(cfg_orig, "1. Original State (0.75x Tgt, 1.75x SL, Trailing Ladder, Cutoff 13:30, No Cooldown)", session_prime=False, window_end="13:30")
results.append(r1)

# Step B: Remove Trailing Ladder (Pure 0.75x Tgt / 1.75x SL exit)
cfg_no_ladder = copy.deepcopy(cfg_orig)
cfg_no_ladder.use_trailing_ladder = False
r2 = run_simulation(cfg_no_ladder, "2. Remove Trailing Ladder (Pure 0.75x Tgt vs 1.75x SL, Cutoff 13:30)", session_prime=False, window_end="13:30")
results.append(r2)

# Step C: Change Payoff to Equal 1.50x Target vs 1.50x Stop
cfg_symm = copy.deepcopy(cfg_no_ladder)
cfg_symm.target_atr_mult = 1.50
cfg_symm.stop_atr_mult = 1.50
r3 = run_simulation(cfg_symm, "3. Symmetric Payoff (1.50x Tgt vs 1.50x SL, Cutoff 13:30)", session_prime=False, window_end="13:30")
results.append(r3)

# Step D: Expand Time Window to 15:15 IST (Remove 13:30 cutoff)
cfg_expand_time = copy.deepcopy(cfg_symm)
r4 = run_simulation(cfg_expand_time, "4. Expand Window to 15:15 IST (Allow afternoon momentum)", session_prime=False, window_end="15:15")
results.append(r4)

# Step E: Apply session_prime Lookback Filter (Strip 09:15-10:05 noise)
cfg_session_prime = copy.deepcopy(cfg_expand_time)
r5 = run_simulation(cfg_session_prime, "5. Apply session_prime Filter (Clean Lookback 10:05-15:00)", session_prime=True, window_end="15:15")
results.append(r5)

# Step F: Add 15-Minute Cooldown Timer for 2 Consecutive Losses
cfg_cooldown = copy.deepcopy(cfg_session_prime)
cfg_cooldown.loss_cooldown_mins = 15
cfg_cooldown.consecutive_losses_for_cooldown = 2
r6 = run_simulation(cfg_cooldown, "6. Add 15-Min Loss Cooldown (Curb chop clusters)", session_prime=True, window_end="15:15")
results.append(r6)

# Step G: Test High Target / Favorable R:R Architecture (2.0x Target vs 1.0x Stop Loss)
cfg_trend = copy.deepcopy(cfg_cooldown)
cfg_trend.target_atr_mult = 2.00
cfg_trend.stop_atr_mult = 1.00
r7 = run_simulation(cfg_trend, "7. Favorable 2:1 R:R (2.00x Tgt vs 1.00x SL + 15m Cooldown)", session_prime=True, window_end="15:15")
results.append(r7)

# Step H: Compare Statutory 300 Lots vs Free Compounding (Uncapped Lots)
cfg_uncapped = copy.deepcopy(cfg_cooldown)
cfg_uncapped.max_lots = 5000 # Uncapped leverage
cfg_uncapped.depth_slip_per_slice = 0.0 # Zero iceberg penalty
r8 = run_simulation(cfg_uncapped, "8. Uncapped Compounding (No statutory 300-lot ceiling)", session_prime=True, window_end="15:15")
results.append(r8)

# Summary Comparison Table
res_df = pd.DataFrame(results)[["name", "trades", "win_rate", "spot_pts", "friction", "final_capital", "return_pct"]]
print("\n" + "="*95)
print("  COMPLETE ABLATION ROOT CAUSE MATRIX")
print("="*95)
print(res_df.to_string(index=False))
res_df.to_csv("ablation_matrix_results.csv", index=False)
print("\nSaved ablation_matrix_results.csv")
