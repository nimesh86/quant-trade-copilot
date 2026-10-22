"""
Refined 6-Month Backtest: Real Economic Target (1.50x ATR >= 10-15% Option Move)
With Exhaustion Filter + Confirmed Trap Reversal Layer + 7% Compounding Sizing.
"""
import pandas as pd
import numpy as np

from forensic_failure_audit import (
    df, f, c_arr, h_arr, l_arr, atr_arr, day_ends,
    cand_call, cand_put, m_c, m_p, feature_cols,
    test_indices, test_start_idx, day_to_indices, MAX_TRADES_PER_DAY
)

clean_te = test_indices[f.iloc[test_indices][feature_cols].notna().all(axis=1).values]
pred_c_te = m_c.predict_proba(f.iloc[clean_te][feature_cols].values)[:, 1]
pred_p_te = m_p.predict_proba(f.iloc[clean_te][feature_cols].values)[:, 1]
idx_to_pos = {idx: i for i, idx in enumerate(clean_te)}

rsi = f["rsi"].values
ret5 = f["ret_5"].values
or_pos = f["or_pos"].values

W_ML, W_FLOW, W_TECH = 0.55, 0.20, 0.25

executed_trades = []
initial_capital = 100000.0
capital = initial_capital
MAX_UNITS = 1950
LOT_SIZE = 65

for day, indices in day_to_indices.items():
    t_today = 0
    blocked_until = -1
    for idx in indices:
        if t_today >= MAX_TRADES_PER_DAY or idx <= blocked_until:
            continue
        pos = idx_to_pos.get(idx)
        if pos is None:
            continue
        pc, pp = pred_c_te[pos], pred_p_te[pos]
        
        tech_c = 1.0 + (0.1 if rsi[idx] > 55 else 0.0) + (0.1 if ret5[idx] > 0.001 else 0.0)
        tech_p = 1.0 + (0.1 if rsi[idx] < 45 else 0.0) + (0.1 if ret5[idx] < -0.001 else 0.0)
        
        score_c = W_ML * pc + W_FLOW * 0.50 + W_TECH * tech_c
        score_p = W_ML * pp + W_FLOW * 0.50 + W_TECH * tech_p
        
        take, side = False, None
        # Exhaustion filter
        if cand_call[idx] and score_c >= 0.656 and score_c > score_p and or_pos[idx] <= 0.90:
            take, side = True, "CE"
        elif cand_put[idx] and score_p >= 0.656 and score_p > score_c and or_pos[idx] >= 0.10:
            take, side = True, "PE"
        if not take:
            continue

        t_today += 1
        entry_idx = idx
        entry_px, entry_atr, end_bar = c_arr[entry_idx], atr_arr[entry_idx], day_ends[entry_idx]
        
        # 7% compounding risk sizing
        stop_dist = 1.50 * entry_atr
        option_delta = 0.50
        stop_risk_per_unit = max(2.0, stop_dist * option_delta)
        risk_budget = capital * 0.07
        units = int(risk_budget // (stop_risk_per_unit * LOT_SIZE)) * LOT_SIZE
        units = max(LOT_SIZE, min(units, MAX_UNITS))

        tgt_px = entry_px + 1.50 * entry_atr if side == "CE" else entry_px - 1.50 * entry_atr
        sl_px  = entry_px - 1.50 * entry_atr if side == "CE" else entry_px + 1.50 * entry_atr

        res, exit_px = "LOSS", sl_px
        exit_bar = entry_idx
        for k in range(entry_idx + 1, min(end_bar + 1, entry_idx + 90)):
            exit_bar = k
            if side == "CE":
                if h_arr[k] >= tgt_px and l_arr[k] > sl_px:
                    res, exit_px = "WIN", tgt_px
                    break
                elif l_arr[k] <= sl_px:
                    break
            else:
                if l_arr[k] <= tgt_px and h_arr[k] < sl_px:
                    res, exit_px = "WIN", tgt_px
                    break
                elif h_arr[k] >= sl_px:
                    break
        
        spot_pts = (exit_px - entry_px) if side == "CE" else (entry_px - exit_px)
        opt_pts = (spot_pts - 1.0) * option_delta
        net_pnl = opt_pts * units - 40.0
        capital += net_pnl
        bars = exit_bar - entry_idx

        executed_trades.append({
            "entry_time": df.index[entry_idx],
            "month": df.index[entry_idx].strftime("%Y-%m"),
            "side": side,
            "units": units,
            "pts": spot_pts,
            "res": res,
            "net_pnl": net_pnl,
            "capital": capital,
            "is_sar": False
        })
        blocked_until = exit_bar

        # Trap Reversal if stopped within 4 bars
        if res == "LOSS" and bars <= 4 and t_today < MAX_TRADES_PER_DAY and exit_bar < end_bar - 10:
            c_bar = exit_bar + 1
            if c_bar < end_bar:
                sar_side = "PE" if side == "CE" else "CE"
                confirmed = (sar_side == "PE" and c_arr[c_bar] < c_arr[exit_bar]) or \
                            (sar_side == "CE" and c_arr[c_bar] > c_arr[exit_bar])
                if confirmed:
                    t_today += 1
                    s_entry, s_atr = c_arr[c_bar], atr_arr[c_bar]
                    s_tgt = s_entry + 1.50 * s_atr if sar_side == "CE" else s_entry - 1.50 * s_atr
                    s_sl  = s_entry - 1.50 * s_atr if sar_side == "CE" else s_entry + 1.50 * s_atr
                    s_res, s_exit_px = "LOSS", s_sl
                    s_exit_bar = c_bar
                    for j in range(c_bar + 1, min(end_bar + 1, c_bar + 90)):
                        s_exit_bar = j
                        if sar_side == "CE":
                            if h_arr[j] >= s_tgt and l_arr[j] > s_sl:
                                s_res, s_exit_px = "WIN", s_tgt
                                break
                            elif l_arr[j] <= s_sl:
                                break
                        else:
                            if l_arr[j] <= s_tgt and h_arr[j] < s_sl:
                                s_res, s_exit_px = "WIN", s_tgt
                                break
                            elif h_arr[j] >= s_sl:
                                break
                    s_pts = (s_exit_px - s_entry) if sar_side == "CE" else (s_entry - s_exit_px)
                    s_opt = (s_pts - 1.0) * option_delta
                    s_pnl = s_opt * units - 40.0
                    capital += s_pnl
                    executed_trades.append({
                        "entry_time": df.index[c_bar],
                        "month": df.index[c_bar].strftime("%Y-%m"),
                        "side": sar_side,
                        "units": units,
                        "pts": s_pts,
                        "res": s_res,
                        "net_pnl": s_pnl,
                        "capital": capital,
                        "is_sar": True
                    })
                    blocked_until = s_exit_bar

tdf = pd.DataFrame(executed_trades)
print("="*85)
print("REFINED ENGINE 6-MONTH BACKTEST REPORT (TARGET 1.50x ATR, SCORE >= 0.656)")
print("="*85)
print("TOTAL TRADES:", len(tdf))
wins_count = (tdf["res"] == "WIN").sum()
losses_count = len(tdf) - wins_count
wr = (wins_count / len(tdf)) * 100.0 if len(tdf) > 0 else 0
print(f"WINS: {wins_count} | LOSSES: {losses_count} | WIN RATE: {wr:.2f}%")
print(f"INITIAL CAPITAL: ₹{initial_capital:,.2f}")
print(f"FINAL CAPITAL:   ₹{capital:,.2f}")
print(f"NET PROFIT:      ₹{capital - initial_capital:,.2f}")
print("="*85)

month_summary = []
running_cap = initial_capital
for m, grp in tdf.groupby("month"):
    m_wins = (grp["res"] == "WIN").sum()
    m_tot = len(grp)
    m_wr = m_wins / m_tot * 100.0 if m_tot > 0 else 0
    pnl = grp["net_pnl"].sum()
    start_c = running_cap
    running_cap += pnl
    end_c = running_cap
    month_summary.append({
        "Month": m,
        "Trades": m_tot,
        "Wins": m_wins,
        "Losses": m_tot - m_wins,
        "WinRate (%)": round(m_wr, 2),
        "Spot Pts Sum": round(grp["pts"].sum(), 2),
        "Start Cap (₹)": round(start_c, 2),
        "Net PnL (₹)": round(pnl, 2),
        "End Cap (₹)": round(end_c, 2),
        "Return (%)": round((pnl / start_c) * 100.0, 2)
    })

m_df = pd.DataFrame(month_summary)
print(m_df.to_string(index=False))
m_df.to_csv("refined_month_wise_report.csv", index=False)
tdf.to_csv("refined_trades.csv", index=False)
