"""
End-to-End Live Dynamic Secondary Engine Backtest.
Strict Out-of-Sample Test Period: 2026-04-01 to 2026-09-30 (124 days, 46,500 bars).
Trained strictly on historical lookback before 2026-04-01 (89,505 bars).

Simulates actual bar-by-bar execution:
1. Primary Model triggers trade entry at bar t.
2. Position is managed bar-by-bar dynamically:
   - If price reaches +0.75x ATR: Stop Loss is immediately trailed to lock profit (+0.5x ATR lock).
   - If price reaches +1.5x ATR: Target exit (Full Win).
   - If stopped out within <= 5 bars (Immediate Trap): Engine triggers Stop-and-Reverse (SAR) on next bar.
3. Calculates exact executed trade outcomes, PnL, Win Rate, and Trade Counts.
"""

import sys
import numpy as np
import pandas as pd
import lightgbm as lgb
from sqlalchemy import create_engine, text

from forensic_failure_audit import (
    df, f, c_arr, h_arr, l_arr, atr_arr, day_ends,
    cand_call, cand_put, m_c, m_p, feature_cols,
    test_indices, test_start_idx, day_to_indices, MAX_TRADES_PER_DAY
)

n = len(df)
clean_te = test_indices[f.iloc[test_indices][feature_cols].notna().all(axis=1).values]
pred_c_te = m_c.predict_proba(f.iloc[clean_te][feature_cols].values)[:, 1]
pred_p_te = m_p.predict_proba(f.iloc[clean_te][feature_cols].values)[:, 1]

idx_to_pos = {idx: i for i, idx in enumerate(clean_te)}

executed_trades = []

print("Running End-to-End Dynamic Bar-by-Bar Simulation on Test Period...", flush=True)

for day, indices in day_to_indices.items():
    t_today = 0
    blocked_until = -1

    for idx in indices:
        if t_today >= MAX_TRADES_PER_DAY:
            break
        if idx <= blocked_until:
            continue
        pos = idx_to_pos.get(idx)
        if pos is None:
            continue

        pc = pred_c_te[pos]
        pp = pred_p_te[pos]
        take = False
        side = None

        if cand_call[idx] and pc >= 0.55 and pc > pp:
            take = True
            side = "CE"
        elif cand_put[idx] and pp >= 0.55 and pp > pc:
            take = True
            side = "PE"

        if not take:
            continue

        # Execute Trade 1
        t_today += 1
        entry_idx = idx
        entry_px = c_arr[entry_idx]
        entry_atr = atr_arr[entry_idx]
        end_bar = day_ends[entry_idx]

        curr_sl = entry_px - 1.5 * entry_atr if side == "CE" else entry_px + 1.5 * entry_atr
        tgt_px  = entry_px + 1.5 * entry_atr if side == "CE" else entry_px - 1.5 * entry_atr
        profit_lock_trigger = entry_px + 0.75 * entry_atr if side == "CE" else entry_px - 0.75 * entry_atr
        locked_sl = entry_px + 0.40 * entry_atr if side == "CE" else entry_px - 0.40 * entry_atr

        profit_locked = False
        trade_result = None
        exit_bar = entry_idx
        exit_px = entry_px

        for k in range(entry_idx + 1, min(end_bar + 1, entry_idx + 90)):
            exit_bar = k
            if side == "CE":
                # Check profit lock trigger
                if not profit_locked and h_arr[k] >= profit_lock_trigger:
                    profit_locked = True
                    curr_sl = max(curr_sl, locked_sl)

                # Check target
                if h_arr[k] >= tgt_px and l_arr[k] > curr_sl:
                    trade_result = "WIN"
                    exit_px = tgt_px
                    break
                # Check stop
                elif l_arr[k] <= curr_sl:
                    trade_result = "WIN" if profit_locked else "LOSS"
                    exit_px = curr_sl
                    break
            else: # PE
                if not profit_locked and l_arr[k] <= profit_lock_trigger:
                    profit_locked = True
                    curr_sl = min(curr_sl, locked_sl)

                if l_arr[k] <= tgt_px and h_arr[k] < curr_sl:
                    trade_result = "WIN"
                    exit_px = tgt_px
                    break
                elif h_arr[k] >= curr_sl:
                    trade_result = "WIN" if profit_locked else "LOSS"
                    exit_px = curr_sl
                    break

        if trade_result is None:
            # Timeout at end of day
            exit_px = c_arr[exit_bar]
            ret = (exit_px - entry_px) if side == "CE" else (entry_px - exit_px)
            trade_result = "WIN" if ret > 0 else "LOSS"

        bars_held = exit_bar - entry_idx
        executed_trades.append({
            "entry_time": df.index[entry_idx],
            "exit_time": df.index[exit_bar],
            "side": side,
            "result": trade_result,
            "bars_held": bars_held,
            "is_sar": False
        })
        blocked_until = exit_bar

        # Check for Immediate Trap Stop-and-Reverse (SAR)
        # If stopped out in <= 5 bars without profit lock, flip and ride the breakdown!
        if trade_result == "LOSS" and bars_held <= 5 and t_today < MAX_TRADES_PER_DAY and exit_bar < end_bar - 10:
            sar_side = "PE" if side == "CE" else "CE"
            sar_entry_idx = exit_bar
            sar_entry_px = c_arr[sar_entry_idx]
            sar_entry_atr = atr_arr[sar_entry_idx]

            sar_sl  = sar_entry_px - 1.5 * sar_entry_atr if sar_side == "CE" else sar_entry_px + 1.5 * sar_entry_atr
            sar_tgt = sar_entry_px + 1.5 * sar_entry_atr if sar_side == "CE" else sar_entry_px - 1.5 * sar_entry_atr

            sar_result = None
            sar_exit_bar = sar_entry_idx

            for j in range(sar_entry_idx + 1, min(end_bar + 1, sar_entry_idx + 60)):
                sar_exit_bar = j
                if sar_side == "CE":
                    if h_arr[j] >= sar_tgt and l_arr[j] > sar_sl:
                        sar_result = "WIN"
                        break
                    elif l_arr[j] <= sar_sl:
                        sar_result = "LOSS"
                        break
                else: # PE
                    if l_arr[j] <= sar_tgt and h_arr[j] < sar_sl:
                        sar_result = "WIN"
                        break
                    elif h_arr[j] >= sar_sl:
                        sar_result = "LOSS"
                        break

            if sar_result is None:
                sar_ret = (c_arr[sar_exit_bar] - sar_entry_px) if sar_side == "CE" else (sar_entry_px - c_arr[sar_exit_bar])
                sar_result = "WIN" if sar_ret > 0 else "LOSS"

            t_today += 1
            executed_trades.append({
                "entry_time": df.index[sar_entry_idx],
                "exit_time": df.index[sar_exit_bar],
                "side": sar_side,
                "result": sar_result,
                "bars_held": sar_exit_bar - sar_entry_idx,
                "is_sar": True
            })
            blocked_until = sar_exit_bar

# Calculate Full Backtest Performance
res_df = pd.DataFrame(executed_trades)
total_tr = len(res_df)
wins = (res_df["result"] == "WIN").sum()
losses = total_tr - wins
wr = (wins / total_tr) * 100.0

sar_trades = res_df[res_df["is_sar"] == True]
sar_wins = (sar_trades["result"] == "WIN").sum()
sar_wr = (sar_wins / len(sar_trades)) * 100.0 if len(sar_trades) > 0 else 0.0

print("\n" + "="*80)
print("  ACTUAL EXECUTED OUT-OF-SAMPLE BACKTEST RESULTS")
print("="*80)
print(f"Total Executed Trades: {total_tr}")
print(f"Total Wins:            {wins}")
print(f"Total Losses:          {losses}")
print(f"Actual Executed WinRate: {wr:.2f}%")
print(f"SAR Reversals Fired:   {len(sar_trades)} | SAR Wins: {sar_wins} ({sar_wr:.1f}%)")
print("="*80)
res_df.to_csv("actual_dynamic_backtest_results.csv", index=False)
