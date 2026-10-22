"""
Fast Evaluation of Multi-Stage Two-Model Filters.
Uses pre-computed features and vectorized conditions for rapid experimentation.
"""
from datetime import time
import numpy as np
import pandas as pd
import lightgbm as lgb
from trading_engine.config import EngineConfig
from trading_engine.feeders.historical_db import HistoricalDBFeeder

cfg = EngineConfig()
feeder = HistoricalDBFeeder(cfg)
feeder.initialize()

spot = feeder.spot_df
f = feeder.feature_df
test_idx = feeder.test_indices
test_start_idx = feeder.test_start_idx

c = spot["close"].values
h = spot["high"].values
l = spot["low"].values
atr = f["atr"].values
vwap = spot["fut_vwap"].values
rsi = f["rsi"].values
ema_spread = f["ema_spread_9_21"].values
ret_5 = f["ret_5"].values
iv_skew_diff = f["iv_skew_diff"].values
htf_trend = f["htf_trend"].values
or_pos = f["or_pos"].values

days = spot.index.date
pos = pd.Series(np.arange(len(spot)))
day_ends = pos.groupby(days).transform("max").values

train_start = max(0, test_start_idx - cfg.lookback_candles)
train_idx = np.arange(train_start, test_start_idx)
feature_cols = [col for col in f.columns if col != "atr"]
clean_tr = train_idx[f.iloc[train_idx][feature_cols].notna().all(axis=1).values]

print("Generating labels...", flush=True)
call_win = np.zeros(len(spot), dtype=int)
put_win = np.zeros(len(spot), dtype=int)
for i in train_idx:
    end_b = day_ends[i]
    if i >= end_b or np.isnan(atr[i]) or atr[i] <= 0: continue
    c_tgt = c[i] + 1.25 * atr[i]
    c_sl  = c[i] - 1.25 * atr[i]
    p_tgt = c[i] - 1.25 * atr[i]
    p_sl  = c[i] + 1.25 * atr[i]
    c_d, p_d = False, False
    for j in range(i + 1, min(end_b + 1, i + 90)):
        if not c_d:
            if h[j] >= c_tgt and l[j] > c_sl: call_win[i] = 1; c_d = True
            elif l[j] <= c_sl: c_d = True
        if not p_d:
            if l[j] <= p_tgt and h[j] < p_sl: put_win[i] = 1; p_d = True
            elif h[j] >= p_sl: p_d = True
        if c_d and p_d: break

print("Training Primary Models...", flush=True)
m_c = lgb.LGBMClassifier(n_estimators=150, learning_rate=0.03, num_leaves=31, random_state=42, verbose=-1, n_jobs=-1)
m_p = lgb.LGBMClassifier(n_estimators=150, learning_rate=0.03, num_leaves=31, random_state=42, verbose=-1, n_jobs=-1)
m_c.fit(f.iloc[clean_tr][feature_cols].values, call_win[clean_tr])
m_p.fit(f.iloc[clean_tr][feature_cols].values, put_win[clean_tr])

clean_te = test_idx[f.iloc[test_idx][feature_cols].notna().all(axis=1).values]
X_te = f.iloc[clean_te][feature_cols].values
p_c_te = m_c.predict_proba(X_te)[:, 1]
p_p_te = m_p.predict_proba(X_te)[:, 1]
idx_map = {idx: i for i, idx in enumerate(clean_te)}

W_START = time(10, 5)
W_END = time(15, 15)

# Test various configurations
print("\n" + "="*85)
print("TESTING MULTI-STAGE CONFIRMATION PIPELINES (TARGET >= 10% OPTION MOVE)")
print("="*85)

for score_thr in [0.55, 0.60, 0.65]:
    for require_confirm in [True, False]:
        for use_sar in [True, False]:
            executed = []
            blocked_until = -1
            current_day = None
            t_today = 0

            for idx in clean_te:
                d = spot.index[idx].date()
                if d != current_day:
                    current_day = d
                    t_today = 0
                if t_today >= 5 or idx <= blocked_until:
                    continue

                t = spot.index[idx].time()
                if not (W_START <= t <= W_END):
                    continue

                pos_i = idx_map[idx]
                pc, pp = p_c_te[pos_i], p_p_te[pos_i]

                side = None
                if c[idx] > vwap[idx] and rsi[idx] > 52 and ema_spread[idx] > 0 and ret_5[idx] > 0.0003 and pc >= score_thr and pc > pp:
                    if or_pos[idx] < 0.88 and htf_trend[idx] > -0.002:
                        side = "CE"
                elif c[idx] < vwap[idx] and rsi[idx] < 48 and ema_spread[idx] < 0 and ret_5[idx] < -0.0003 and pp >= score_thr and pp > pc:
                    if or_pos[idx] > 0.12 and htf_trend[idx] < 0.002:
                        side = "PE"

                if side is None:
                    continue

                entry_bar = idx
                if require_confirm:
                    c_bar = idx + 1
                    if c_bar >= day_ends[idx]: continue
                    # Confirmation check
                    if side == "CE" and c[c_bar] < l[idx]: continue
                    if side == "PE" and c[c_bar] > h[idx]: continue
                    entry_bar = c_bar

                t_today += 1
                entry_p = c[entry_bar]
                entry_a = atr[entry_bar]
                end_b = day_ends[entry_bar]
                tgt_dist = 1.25 * entry_a
                sl_dist  = 1.25 * entry_a
                tgt_p = entry_p + tgt_dist if side == "CE" else entry_p - tgt_dist
                sl_p  = entry_p - sl_dist if side == "CE" else entry_p + sl_dist

                res = "LOSS"
                exit_b = entry_bar
                for k in range(entry_bar + 1, min(end_b + 1, entry_bar + 90)):
                    exit_b = k
                    if side == "CE":
                        if h[k] >= tgt_p and l[k] > sl_p: res = "WIN"; break
                        elif l[k] <= sl_p: break
                    else:
                        if l[k] <= tgt_p and h[k] < sl_p: res = "WIN"; break
                        elif h[k] >= sl_p: break

                executed.append({"res": res, "is_sar": False})
                blocked_until = exit_b

                # Confirmed SAR
                if use_sar and res == "LOSS" and (exit_b - entry_bar) <= 5 and t_today < 5 and exit_b < end_b - 15:
                    sar_bar = exit_b + 1
                    if sar_bar < end_b:
                        sar_side = "PE" if side == "CE" else "CE"
                        # Check SAR confirmation
                        sar_conf = (c[sar_bar] < c[exit_b]) if sar_side == "PE" else (c[sar_bar] > c[exit_b])
                        if sar_conf:
                            t_today += 1
                            s_entry_p = c[sar_bar]
                            s_entry_a = atr[sar_bar]
                            s_tgt_dist = 1.25 * s_entry_a
                            s_sl_dist  = 1.25 * s_entry_a
                            s_tgt_p = s_entry_p - s_tgt_dist if sar_side == "PE" else s_entry_p + s_tgt_dist
                            s_sl_p  = s_entry_p + s_sl_dist if sar_side == "PE" else s_entry_p - s_sl_dist

                            sar_res = "LOSS"
                            sar_exit_b = sar_bar
                            for k in range(sar_bar + 1, min(end_b + 1, sar_bar + 60)):
                                sar_exit_b = k
                                if sar_side == "PE":
                                    if l[k] <= s_tgt_p and h[k] < s_sl_p: sar_res = "WIN"; break
                                    elif h[k] >= s_sl_p: break
                                else:
                                    if h[k] >= s_tgt_p and l[k] > s_sl_p: sar_res = "WIN"; break
                                    elif l[k] <= s_sl_p: break
                            
                            executed.append({"res": sar_res, "is_sar": True})
                            blocked_until = sar_exit_b

            tdf = pd.DataFrame(executed)
            if len(tdf) > 0:
                tot = len(tdf)
                w = (tdf["res"] == "WIN").sum()
                wr = w / tot * 100.0
                print(f"Thr={score_thr:.2f} | Confirm={str(require_confirm):5s} | SAR={str(use_sar):5s} -> Trades={tot:3d} | Wins={w:3d} | Losses={tot-w:3d} | WinRate={wr:5.2f}%")
