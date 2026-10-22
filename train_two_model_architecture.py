"""
Two-Model Quantitative Trading Architecture:
Model 1 (Primary Generator): Identifies technical breakouts/breakdowns (CE / PE) aiming for >=1.50x ATR targets.
Model 2 (Meta-Labeling Gatekeeper): Evaluates the signal quality, market regime, order flow/Greeks, and
prunes false-positive traps to achieve >80% Win Rate.
"""
import sys
import numpy as np
import pandas as pd
import lightgbm as lgb
from sqlalchemy import create_engine, text
from sklearn.model_selection import KFold

DB_URL = "postgresql+psycopg2://postgres:postgres@localhost:5432/trading"
SYMBOL = "NIFTY 50"
FUTURES_SYMBOL = "NIFTY-I"
OPTION_PREFIX = "NIFTY"
TZ = "Asia/Kolkata"
START_DATE = "2026-04-01"
END_DATE = "2026-09-30"
LOOKBACK = 89505
MAX_TRADES_PER_DAY = 5

def to_ist(s):
    return pd.to_datetime(s, utc=True).dt.tz_convert(TZ)

print("1. Loading minute candles & option chain from database...", flush=True)
eng = create_engine(DB_URL)
spot = pd.read_sql(text("SELECT timestamp, open, high, low, close, volume FROM minute_candles WHERE symbol = :s ORDER BY timestamp ASC"), eng, params={"s": SYMBOL})
spot["timestamp"] = to_ist(spot["timestamp"])
spot = spot.drop_duplicates("timestamp").sort_values("timestamp").set_index("timestamp").between_time("09:15", "15:29")

fut = pd.read_sql(text("SELECT timestamp, volume, vwap FROM minute_candles WHERE symbol = :s ORDER BY timestamp ASC"), eng, params={"s": FUTURES_SYMBOL})
if len(fut) > 0:
    fut["timestamp"] = to_ist(fut["timestamp"])
    fut = fut.drop_duplicates("timestamp").sort_values("timestamp").set_index("timestamp").between_time("09:15", "15:29")
    spot["fut_volume"] = fut["volume"].reindex(spot.index).fillna(0.0)
    spot["fut_vwap"] = fut["vwap"].reindex(spot.index).ffill()
else:
    spot["fut_volume"] = spot["volume"]
    spot["fut_vwap"] = spot["close"]

opt = pd.read_sql(text("""
    SELECT timestamp, option_type, relative_strike, iv, delta
    FROM option_chain
    WHERE underlying = :u AND relative_strike = 0 AND ltp > 0
"""), eng, params={"u": OPTION_PREFIX})
opt["timestamp"] = to_ist(opt["timestamp"])
ce = opt[opt["option_type"] == "CE"].drop_duplicates("timestamp").set_index("timestamp")
pe = opt[opt["option_type"] == "PE"].drop_duplicates("timestamp").set_index("timestamp")

iv_ce = ce["iv"].reindex(spot.index).ffill().fillna(15.0)
iv_pe = pe["iv"].reindex(spot.index).ffill().fillna(15.0)
delta_ce = ce["delta"].reindex(spot.index).ffill().fillna(0.50)
delta_pe = pe["delta"].reindex(spot.index).ffill().fillna(-0.50)

c, h, l, o_px, v = spot["close"], spot["high"], spot["low"], spot["open"], spot["fut_volume"]
f = pd.DataFrame(index=spot.index)

for w in [3, 5, 10, 15, 30]:
    f[f"ret_{w}"] = c.pct_change(w)
    f[f"vol_{w}"] = c.pct_change().rolling(w).std()

delta = c.diff()
gain = delta.clip(lower=0).rolling(14).mean()
loss = (-delta.clip(upper=0)).rolling(14).mean()
rs = gain / loss.replace(0, np.nan)
f["rsi"] = 100 - (100 / (1 + rs))

ema9 = c.ewm(span=9).mean()
ema21 = c.ewm(span=21).mean()
f["ema_spread_9_21"] = (ema9 - ema21) / c
f["htf_trend"] = (c.rolling(60).mean() - c.rolling(240).mean()) / c

tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
f["atr"] = tr.rolling(14).mean()
f["atr_pct"] = f["atr"] / c

# Candle morphology
candle_range = (h - l).replace(0, 1e-6)
f["body_ratio"] = (c - o_px).abs() / candle_range
f["upper_wick"] = (h - np.maximum(c, o_px)) / candle_range
f["lower_wick"] = (np.minimum(c, o_px) - l) / candle_range
f["vol_ratio"] = v / (v.rolling(10).mean().replace(0, 1e-6))

days = spot.index.date
pv = (c * v).groupby(days).cumsum()
cum_v = v.groupby(days).cumsum()
vwap = np.where(cum_v > 0, pv / np.maximum(cum_v, 1e-9), c)
f["vwap_dist"] = (c - vwap) / c

g = spot.groupby(days)
or_hi = g["high"].transform(lambda s: s.iloc[:30].max())
or_lo = g["low"].transform(lambda s: s.iloc[:30].min())
f["or_pos"] = np.where(or_hi > or_lo, (c - or_lo) / np.maximum(or_hi - or_lo, 1e-9), 0.5)

f["time_min"] = spot.index.hour * 60 + spot.index.minute
t_time = spot.index.time
f["is_morning"] = ((t_time >= pd.to_datetime("10:05").time()) & (t_time <= pd.to_datetime("11:30").time())).astype(float)
f["is_afternoon"] = ((t_time >= pd.to_datetime("13:15").time()) & (t_time <= pd.to_datetime("15:00").time())).astype(float)

f["atm_iv"] = (iv_ce + iv_pe) / 2.0
f["iv_skew"] = iv_pe - iv_ce
f["delta_skew"] = delta_ce.abs() - delta_pe.abs()
f["iv_skew_diff"] = f["iv_skew"].diff(5)

f = f.replace([np.inf, -np.inf], np.nan)
feature_cols = [col for col in f.columns if col != "atr"]

test_mask = (spot.index >= pd.to_datetime(START_DATE).tz_localize(TZ)) & (spot.index <= pd.to_datetime(END_DATE).tz_localize(TZ))
test_indices = np.flatnonzero(test_mask)
test_start_idx = test_indices[0]
n = len(spot)

# Labels for 1.50x Target vs 1.50x SL
print("2. Generating Triple Barrier 1.50x ATR Labels...", flush=True)
pos = pd.Series(np.arange(n))
day_ends = pos.groupby(days).transform("max").values
c_arr, h_arr, l_arr, atr_arr = c.values, h.values, l.values, f["atr"].values

call_win = np.zeros(n, dtype=int)
put_win = np.zeros(n, dtype=int)

for i in range(n):
    end_b = day_ends[i]
    if i >= end_b or np.isnan(atr_arr[i]) or atr_arr[i] <= 0:
        continue
    entry = c_arr[i]
    c_tgt = entry + 1.50 * atr_arr[i]
    c_sl  = entry - 1.50 * atr_arr[i]
    p_tgt = entry - 1.50 * atr_arr[i]
    p_sl  = entry + 1.50 * atr_arr[i]

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

# Candidate setups
in_window = (t_time >= pd.to_datetime("10:05").time()) & (t_time <= pd.to_datetime("15:15").time())
c_cand = in_window & (c > spot["fut_vwap"]) & (f["rsi"] > 52.0) & (f["ema_spread_9_21"] > 0) & (f["ret_5"] > 0.0003)
p_cand = in_window & (c < spot["fut_vwap"]) & (f["rsi"] < 48.0) & (f["ema_spread_9_21"] < 0) & (f["ret_5"] < -0.0003)
cand_call = c_cand.values
cand_put = p_cand.values

train_pool_start = max(0, test_start_idx - LOOKBACK)
train_idx = np.arange(train_pool_start, test_start_idx)
clean_tr = train_idx[f.iloc[train_idx][feature_cols].notna().all(axis=1).values]

print(f"3. Generating Out-Of-Fold predictions on {len(clean_tr):,} training bars to train Model 2...", flush=True)
kf = KFold(n_splits=5, shuffle=False)
oof_pred_c = np.zeros(len(clean_tr))
oof_pred_p = np.zeros(len(clean_tr))
X_tr = f.iloc[clean_tr][feature_cols].values
y_c_tr = call_win[clean_tr]
y_p_tr = put_win[clean_tr]

for tr_fold, val_fold in kf.split(clean_tr):
    m_c_f = lgb.LGBMClassifier(n_estimators=150, learning_rate=0.03, num_leaves=31, random_state=42, verbose=-1, n_jobs=-1)
    m_p_f = lgb.LGBMClassifier(n_estimators=150, learning_rate=0.03, num_leaves=31, random_state=42, verbose=-1, n_jobs=-1)
    m_c_f.fit(X_tr[tr_fold], y_c_tr[tr_fold])
    m_p_f.fit(X_tr[tr_fold], y_p_tr[tr_fold])
    oof_pred_c[val_fold] = m_c_f.predict_proba(X_tr[val_fold])[:, 1]
    oof_pred_p[val_fold] = m_p_f.predict_proba(X_tr[val_fold])[:, 1]

# Fit full Model 1 on training set
print("Fitting full Primary Model 1 on all historical lookback...", flush=True)
m_c_full = lgb.LGBMClassifier(n_estimators=250, learning_rate=0.03, num_leaves=31, random_state=42, verbose=-1, n_jobs=-1)
m_p_full = lgb.LGBMClassifier(n_estimators=250, learning_rate=0.03, num_leaves=31, random_state=42, verbose=-1, n_jobs=-1)
m_c_full.fit(X_tr, y_c_tr)
m_p_full.fit(X_tr, y_p_tr)

# Build training dataset for Model 2 (Meta-Gatekeeper)
print("Building Meta-Dataset for Model 2 from out-of-fold primary signals...", flush=True)
meta_records = []
clean_tr_arr = clean_tr

for i, real_idx in enumerate(clean_tr_arr):
    pc, pp = oof_pred_c[i], oof_pred_p[i]
    is_c = cand_call[real_idx] and (pc >= 0.52) and (pc > pp)
    is_p = cand_put[real_idx] and (pp >= 0.52) and (pp > pc)
    if not (is_c or is_p):
        continue

    side = "CE" if is_c else "PE"
    primary_win = (call_win[real_idx] == 1) if side == "CE" else (put_win[real_idx] == 1)

    rec = {
        "side_is_ce": 1.0 if side == "CE" else 0.0,
        "prob_primary": pc if side == "CE" else pp,
        "prob_margin": abs(pc - pp),
        "upper_wick": f["upper_wick"].values[real_idx],
        "lower_wick": f["lower_wick"].values[real_idx],
        "body_ratio": f["body_ratio"].values[real_idx],
        "or_pos": f["or_pos"].values[real_idx],
        "htf_trend": f["htf_trend"].values[real_idx],
        "rsi": f["rsi"].values[real_idx],
        "ema_spread_9_21": f["ema_spread_9_21"].values[real_idx],
        "ret_5": f["ret_5"].values[real_idx],
        "ret_15": f["ret_15"].values[real_idx],
        "atr_pct": f["atr_pct"].values[real_idx],
        "iv_skew": f["iv_skew"].values[real_idx],
        "iv_skew_diff": f["iv_skew_diff"].values[real_idx],
        "atm_iv": f["atm_iv"].values[real_idx],
        "time_min": f["time_min"].values[real_idx],
        "label_win": 1 if primary_win else 0
    }
    meta_records.append(rec)

meta_df = pd.DataFrame(meta_records)
print(f"Meta-Dataset created: {len(meta_df):,} trades. Base win rate: {meta_df['label_win'].mean()*100:.2f}%", flush=True)

meta_features = [col for col in meta_df.columns if col != "label_win"]
m2 = lgb.LGBMClassifier(
    n_estimators=200, learning_rate=0.03, num_leaves=15, min_child_samples=30,
    random_state=42, verbose=-1, n_jobs=-1
)
m2.fit(meta_df[meta_features].values, meta_df["label_win"].values)
print("Model 2 (Meta-Gatekeeper) trained successfully.", flush=True)

# Save feature importance
imp = pd.Series(m2.feature_importances_, index=meta_features).sort_values(ascending=False)
print("\nModel 2 Feature Importance:")
print(imp.to_string())

# 4. Out-of-Sample Evaluation on 6 Months
print("\n4. Testing Two-Model Architecture on 6-Month Test Period (2026-04-01 to 2026-09-30)...", flush=True)
clean_te = test_indices[f.iloc[test_indices][feature_cols].notna().all(axis=1).values]
X_te = f.iloc[clean_te][feature_cols].values
pred_c_te = m_c_full.predict_proba(X_te)[:, 1]
pred_p_te = m_p_full.predict_proba(X_te)[:, 1]
idx_to_pos = {idx: i for i, idx in enumerate(clean_te)}

day_to_indices = {}
for idx in clean_te:
    d = spot.index[idx].date()
    if d not in day_to_indices:
        day_to_indices[d] = []
    day_to_indices[d].append(idx)

# Test various gatekeeper thresholds
for meta_threshold in [0.50, 0.53, 0.55, 0.58, 0.60]:
    executed_trades = []
    capital = 100000.0
    LOT_SIZE = 65

    for day, indices in day_to_indices.items():
        t_today = 0
        blocked_until = -1
        for idx in indices:
            if t_today >= MAX_TRADES_PER_DAY or idx <= blocked_until:
                continue
            pos_idx = idx_to_pos.get(idx)
            if pos_idx is None:
                continue
            pc, pp = pred_c_te[pos_idx], pred_p_te[pos_idx]
            
            # Step 1: Model 1 Candidate Filter
            cand_side = None
            if cand_call[idx] and pc >= 0.52 and pc > pp:
                cand_side = "CE"
            elif cand_put[idx] and pp >= 0.52 and pp > pc:
                cand_side = "PE"
            if cand_side is None:
                continue

            # Step 2: Model 2 Meta-Gatekeeper Evaluation
            meta_vec = np.array([
                1.0 if cand_side == "CE" else 0.0,
                pc if cand_side == "CE" else pp,
                abs(pc - pp),
                f["upper_wick"].values[idx],
                f["lower_wick"].values[idx],
                f["body_ratio"].values[idx],
                f["or_pos"].values[idx],
                f["htf_trend"].values[idx],
                f["rsi"].values[idx],
                f["ema_spread_9_21"].values[idx],
                f["ret_5"].values[idx],
                f["ret_15"].values[idx],
                f["atr_pct"].values[idx],
                f["iv_skew"].values[idx],
                f["iv_skew_diff"].values[idx],
                f["atm_iv"].values[idx],
                f["time_min"].values[idx]
            ]).reshape(1, -1)

            prob_meta_win = float(m2.predict_proba(meta_vec)[0, 1])

            # Gatekeeper Decision
            if prob_meta_win < meta_threshold:
                continue # VETO trade!

            # Execute Approved Trade
            t_today += 1
            entry_idx = idx
            entry_px, entry_atr, end_bar = c_arr[entry_idx], atr_arr[entry_idx], day_ends[entry_idx]
            tgt_dist = 1.50 * entry_atr
            sl_dist  = 1.50 * entry_atr
            tgt_px = entry_px + tgt_dist if cand_side == "CE" else entry_px - tgt_dist
            sl_px  = entry_px - sl_dist if cand_side == "CE" else entry_px + sl_dist

            est_premium = max(80.0, entry_atr * 5.0)
            opt_sl_pts = sl_dist * 0.35 + 1.0
            risk_per_lot = opt_sl_pts * LOT_SIZE
            risk_budget = capital * 0.07
            max_lots_by_risk = int(risk_budget / risk_per_lot) if risk_per_lot > 0 else 0
            max_lots_by_capital = int(capital / (est_premium * LOT_SIZE))
            n_lots = max(1, min(max_lots_by_risk, max_lots_by_capital, 30))
            units = n_lots * LOT_SIZE

            res = "LOSS"
            exit_bar = entry_idx
            for k in range(entry_idx + 1, min(end_bar + 1, entry_idx + 90)):
                exit_bar = k
                if cand_side == "CE":
                    if h_arr[k] >= tgt_px and l_arr[k] > sl_px:
                        res = "WIN"; break
                    elif l_arr[k] <= sl_px:
                        break
                else:
                    if l_arr[k] <= tgt_px and h_arr[k] < sl_px:
                        res = "WIN"; break
                    elif h_arr[k] >= sl_px:
                        break

            if res == "WIN":
                pnl = (tgt_dist * 0.65 - 1.0) * units - 40.0
            else:
                pnl = -(sl_dist * 0.35 + 1.0) * units - 40.0
            capital += pnl

            executed_trades.append({
                "month": spot.index[entry_idx].strftime("%Y-%m"),
                "side": cand_side,
                "res": res,
                "pnl": pnl,
                "capital": capital
            })
            blocked_until = exit_bar

    tdf = pd.DataFrame(executed_trades)
    tot = len(tdf)
    w = (tdf["res"] == "WIN").sum() if tot > 0 else 0
    wr = (w / tot * 100.0) if tot > 0 else 0.0
    print(f"Meta-Threshold {meta_threshold:.2f}: Trades={tot:3d} | Wins={w:3d} | Losses={tot-w:3d} | WinRate={wr:6.2f}% | Final Capital=₹{capital:,.2f} | Net PnL=₹{capital-100000:+,.2f}")

print("\nDone.", flush=True)
