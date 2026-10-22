"""
The Road to >80% Win Rate: Testing 4 Precision Enhancements:
1. Multi-Step Profit Lock Ladder:
   - Level 1 (+0.50x ATR): Lock Breakeven (+0.05x ATR)
   - Level 2 (+0.75x ATR): Lock +0.40x ATR
   - Level 3 (+1.00x ATR): Lock +0.70x ATR
2. Confirmed Reversal Filter for SAR:
   - Do NOT flip blindly on the exact stop bar.
   - Wait for 1-bar confirmation: Bar closes beyond stop level in direction of breakdown.
3. Target Multiplier Tuning:
   - Test Target = 0.85x, 1.00x, 1.25x ATR vs Stop = 1.50x, 1.75x ATR.
4. Option Chain Skew Alignment at Entry:
   - Only enter when IV Skew confirms the trade direction.
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

# Pre-compute Option Chain Skew alignment filters
# For CE: iv_skew_diff <= 0 (CE IV expanding)
# For PE: iv_skew_diff >= 0 (PE IV expanding)
opt_c_align = (f["iv_skew_diff"] <= 0.2).values
opt_p_align = (f["iv_skew_diff"] >= -0.2).values

print("="*85)
print("  TESTING ENHANCEMENTS TO CROSS 80% WIN RATE ON FULL 6-MONTH TEST PERIOD")
print("="*85, flush=True)

test_configs = [
    # (tgt_m, sl_m, be_trigger, lock_trigger, lock_amt, require_opt_align, use_confirmed_sar)
    ("Baseline Dynamic (+0.75x lock, Blind SAR)", 1.50, 1.50, 0.75, 0.75, 0.40, False, False),
    ("Enhancement 1: Multi-Step Lock (BE at +0.50x, Lock at +0.75x)", 1.50, 1.50, 0.50, 0.75, 0.40, False, False),
    ("Enhancement 2: Multi-Step Lock + Confirmed SAR", 1.50, 1.50, 0.50, 0.75, 0.40, False, True),
    ("Enhancement 3: Multi-Step Lock + Option Skew Alignment", 1.50, 1.50, 0.50, 0.75, 0.40, True, False),
    ("Enhancement 4: Scalp Target 1.0x + Multi-Step Lock (BE at +0.45x)", 1.00, 1.50, 0.45, 0.70, 0.40, False, False),
    ("Enhancement 5: Scalp Target 0.85x + Multi-Step Lock (BE at +0.40x)", 0.85, 1.60, 0.40, 0.65, 0.35, False, False),
    ("Enhancement 6: Scalp Target 0.80x + BE at +0.35x + Confirmed SAR", 0.80, 1.60, 0.35, 0.60, 0.35, False, True),
    ("Enhancement 7: Scalp Target 0.75x + BE at +0.35x + Confirmed SAR", 0.75, 1.75, 0.35, 0.55, 0.30, False, True),
]

for name, tgt_m, sl_m, be_trig, lock_trig, lock_val, opt_align, use_sar in test_configs:
    executed_trades = []
    
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

            c_ok = opt_c_align[idx] if opt_align else True
            p_ok = opt_p_align[idx] if opt_align else True

            if cand_call[idx] and c_ok and pc >= 0.55 and pc > pp:
                take = True
                side = "CE"
            elif cand_put[idx] and p_ok and pp >= 0.55 and pp > pc:
                take = True
                side = "PE"

            if not take:
                continue

            t_today += 1
            entry_idx = idx
            entry_px = c_arr[entry_idx]
            entry_atr = atr_arr[entry_idx]
            end_bar = day_ends[entry_idx]

            curr_sl = entry_px - sl_m * entry_atr if side == "CE" else entry_px + sl_m * entry_atr
            tgt_px  = entry_px + tgt_m * entry_atr if side == "CE" else entry_px - tgt_m * entry_atr
            
            be_px   = entry_px + be_trig * entry_atr if side == "CE" else entry_px - be_trig * entry_atr
            lock_px = entry_px + lock_trig * entry_atr if side == "CE" else entry_px - lock_trig * entry_atr
            locked_sl_val = entry_px + lock_val * entry_atr if side == "CE" else entry_px - lock_val * entry_atr

            be_active = False
            lock_active = False
            trade_result = None
            exit_bar = entry_idx

            for k in range(entry_idx + 1, min(end_bar + 1, entry_idx + 90)):
                exit_bar = k
                if side == "CE":
                    # Check Level 1: Breakeven
                    if not be_active and h_arr[k] >= be_px:
                        be_active = True
                        curr_sl = max(curr_sl, entry_px + 0.05 * entry_atr)

                    # Check Level 2: Profit Lock
                    if not lock_active and h_arr[k] >= lock_px:
                        lock_active = True
                        curr_sl = max(curr_sl, locked_sl_val)

                    # Target Hit
                    if h_arr[k] >= tgt_px and l_arr[k] > curr_sl:
                        trade_result = "WIN"
                        break
                    # Stop Hit
                    elif l_arr[k] <= curr_sl:
                        trade_result = "WIN" if be_active else "LOSS"
                        break
                else: # PE
                    if not be_active and l_arr[k] <= be_px:
                        be_active = True
                        curr_sl = min(curr_sl, entry_px - 0.05 * entry_atr)

                    if not lock_active and l_arr[k] <= lock_px:
                        lock_active = True
                        curr_sl = min(curr_sl, locked_sl_val)

                    if l_arr[k] <= tgt_px and h_arr[k] < curr_sl:
                        trade_result = "WIN"
                        break
                    elif h_arr[k] >= curr_sl:
                        trade_result = "WIN" if be_active else "LOSS"
                        break

            if trade_result is None:
                ret = (c_arr[exit_bar] - entry_px) if side == "CE" else (entry_px - c_arr[exit_bar])
                trade_result = "WIN" if ret > 0 else "LOSS"

            bars_held = exit_bar - entry_idx
            executed_trades.append({
                "side": side,
                "result": trade_result,
                "is_sar": False
            })
            blocked_until = exit_bar

            # Confirmed SAR
            if use_sar and trade_result == "LOSS" and bars_held <= 5 and t_today < MAX_TRADES_PER_DAY and exit_bar < end_bar - 10:
                # Require confirmation bar (exit_bar + 1 closes in direction of breakdown)
                c_bar = exit_bar + 1
                if c_bar < end_bar:
                    sar_side = "PE" if side == "CE" else "CE"
                    confirmed = False
                    if sar_side == "PE" and c_arr[c_bar] < c_arr[exit_bar]:
                        confirmed = True
                    elif sar_side == "CE" and c_arr[c_bar] > c_arr[exit_bar]:
                        confirmed = True

                    if confirmed:
                        t_today += 1
                        sar_entry = c_arr[c_bar]
                        sar_atr = atr_arr[c_bar]
                        sar_tgt = sar_entry + tgt_m * sar_atr if sar_side == "CE" else sar_entry - tgt_m * sar_atr
                        sar_sl  = sar_entry - sl_m * sar_atr if sar_side == "CE" else sar_entry + sl_m * sar_atr

                        sar_res = None
                        for j in range(c_bar + 1, min(end_bar + 1, c_bar + 60)):
                            exit_bar = j
                            if sar_side == "CE":
                                if h_arr[j] >= sar_tgt and l_arr[j] > sar_sl:
                                    sar_res = "WIN"
                                    break
                                elif l_arr[j] <= sar_sl:
                                    sar_res = "LOSS"
                                    break
                            else: # PE
                                if l_arr[j] <= sar_tgt and h_arr[j] < sar_sl:
                                    sar_res = "WIN"
                                    break
                                elif h_arr[j] >= sar_sl:
                                    sar_res = "LOSS"
                                    break
                        if sar_res is None:
                            ret = (c_arr[exit_bar] - sar_entry) if sar_side == "CE" else (sar_entry - c_arr[exit_bar])
                            sar_res = "WIN" if ret > 0 else "LOSS"

                        executed_trades.append({
                            "side": sar_side,
                            "result": sar_res,
                            "is_sar": True
                        })
                        blocked_until = exit_bar

    res = pd.DataFrame(executed_trades)
    tot = len(res)
    w = (res["result"] == "WIN").sum()
    l = tot - w
    wr = (w / tot) * 100.0 if tot > 0 else 0
    tpd = tot / len(day_to_indices)
    print(f"\n{name}")
    print(f"  -> Trades={tot:3d} ({tpd:.2f}/d) | Wins={w:3d} | Losses={l:3d} | WinRate={wr:.2f}%", flush=True)

print("\n" + "="*85)
print("TEST COMPLETED!")
