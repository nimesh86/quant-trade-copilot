python3 -c '
from find_high_winrate_model import get_engine, load_all_candles, load_option_features, compute_features, generate_barrier_labels, find_candidate_signals, build_training_subset, START_DATE, END_DATE, TZ
import numpy as np
import pandas as pd
from xgboost import XGBClassifier

eng = get_engine()
df = load_all_candles(eng)
opt_feats = load_option_features(eng, df.index)
f = compute_features(df, opt_feats)

ts = df.index
test_mask = (ts >= pd.to_datetime(START_DATE).tz_localize(TZ)) & (ts <= pd.to_datetime(END_DATE).tz_localize(TZ))
test_indices = np.flatnonzero(test_mask)
test_start_idx = test_indices[0]

eval_start_idx = test_start_idx - 100000
call_win, put_win = generate_barrier_labels(df, f["atr"], eval_start_idx)
cand_call, cand_put = find_candidate_signals(df, f)

lookback = 89490
train_pool_start = max(0, test_start_idx - lookback)
train_idx_raw = np.arange(train_pool_start, test_start_idx)

train_subset = build_training_subset(f.iloc[train_idx_raw], df.iloc[train_idx_raw], "session_prime")
actual_tr_idx = train_idx_raw[train_subset]

X_tr = f.iloc[actual_tr_idx].values
y_call_tr = call_win[actual_tr_idx]
y_put_tr = put_win[actual_tr_idx]
X_te = f.iloc[test_indices].values

m_call = XGBClassifier(n_estimators=250, learning_rate=0.03, max_depth=5, tree_method="hist", random_state=42, n_jobs=-1)
m_put = XGBClassifier(n_estimators=250, learning_rate=0.03, max_depth=5, tree_method="hist", random_state=42, n_jobs=-1)
m_call.fit(X_tr, y_call_tr)
m_put.fit(X_tr, y_put_tr)

pred_c = m_call.predict_proba(X_te)[:, 1]
pred_p = m_put.predict_proba(X_te)[:, 1]

pred_call_full = np.zeros(len(df))
pred_put_full = np.zeros(len(df))
pred_call_full[test_indices] = pred_c
pred_put_full[test_indices] = pred_p

day_to_indices = {}
for day, grp in pd.Series(test_indices).groupby(df.index[test_indices].date):
    day_to_indices[day] = grp.values

c_arr = df["close"].values
h_arr = df["high"].values
l_arr = df["low"].values
atr_arr = f["atr"].values
days = df.index.date
pos = pd.Series(np.arange(len(df)))
day_ends = pos.groupby(days).transform("max").values

for max_lots in [300, 1000, 3000, 10000, 50000]:
    cap = 100000.0
    trades = 0
    wins = 0
    monthly_pnl = {}
    for day, indices in day_to_indices.items():
        trades_today = 0
        blocked_until = -1
        for idx in indices:
            if trades_today >= 5 or idx <= blocked_until:
                continue
            p_c = pred_call_full[idx]
            p_p = pred_put_full[idx]
            take, side = False, None
            if cand_call[idx] and p_c >= 0.55 and p_c > p_p:
                take, side = True, "CE"
            elif cand_put[idx] and p_p >= 0.55 and p_p > p_c:
                take, side = True, "PE"
            if not take:
                continue
            
            trades_today += 1
            trades += 1
            atr = atr_arr[idx]
            tgt_dist = 1.5 * atr
            sl_dist = 1.5 * atr
            tgt_px = c_arr[idx] + tgt_dist if side == "CE" else c_arr[idx] - tgt_dist
            sl_px = c_arr[idx] - sl_dist if side == "CE" else c_arr[idx] + sl_dist
            
            # Compounding 7% risk
            est_prem = max(80.0, atr * 5.0)
            opt_sl = sl_dist * 0.35 + 1.0
            risk_per_lot = opt_sl * 65
            lots = max(1, min(int((cap * 0.07) // risk_per_lot), int(cap // (est_prem * 65)), max_lots))
            units = lots * 65
            
            end_b = day_ends[idx]
            res = "LOSS"
            exit_bar = idx
            for k in range(idx + 1, min(end_b + 1, idx + 90)):
                exit_bar = k
                if side == "CE":
                    if h_arr[k] >= tgt_px and l_arr[k] > sl_px:
                        res = "WIN"; break
                    elif l_arr[k] <= sl_px:
                        break
                else:
                    if l_arr[k] <= tgt_px and h_arr[k] < sl_px:
                        res = "WIN"; break
                    elif h_arr[k] >= sl_px:
                        break
            
            m_str = df.index[idx].strftime("%Y-%m")
            if res == "WIN":
                wins += 1
                opt_pts = tgt_dist * 0.65 - 1.0
                pnl = opt_pts * units - 40.0 * max(1, lots // 30)
            else:
                opt_pts = -(sl_dist * 0.35 + 1.0)
                pnl = opt_pts * units - 40.0 * max(1, lots // 30)
            cap += pnl
            monthly_pnl[m_str] = monthly_pnl.get(m_str, 0.0) + pnl
            blocked_until = exit_bar
    wr = wins / trades * 100.0 if trades > 0 else 0
    print(f"MaxLots={max_lots:5d} -> Trades={trades} | Wins={wins} ({wr:.1f}%) | Final Capital=INR {cap:,.2f}")
    print("  Monthly:", " | ".join([f"{k}: INR {v:,.0f}" for k, v in sorted(monthly_pnl.items())]))
'