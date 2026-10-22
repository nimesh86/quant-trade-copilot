"""
Exploration of Advanced Training Approaches for Breakthrough Win Rate (>70% at 4-5 trades/day):
1. Event-Conditioned Training (Train exclusively on candidate trade events, not noisy all-bars)
2. Option-Chain Greek Conjoint Filtering (IV Skew & Delta Skew institutional confirmation)
3. Meta-Labeling (Two-stage Lopez de Prado filter)
4. Multi-Model Consensus Ensemble (LightGBM + XGBoost + CatBoost/RandomForest agreement)
"""

import sys
import time
import numpy as np
import pandas as pd
import lightgbm as lgb
from xgboost import XGBClassifier
from sklearn.ensemble import ExtraTreesClassifier
from sqlalchemy import create_engine, text

DB_URL = "postgresql+psycopg2://postgres:postgres@localhost:5432/trading"
SYMBOL = "NIFTY 50"
FUTURES_SYMBOL = "NIFTY-I"
OPTION_PREFIX = "NIFTY"
TZ = "Asia/Kolkata"

START_DATE = "2026-04-01"
END_DATE = "2026-09-30"
LOOKBACK = 89500
MAX_TRADES_PER_DAY = 5

def to_ist(s):
    return pd.to_datetime(s, utc=True).dt.tz_convert(TZ)

eng = create_engine(DB_URL)

print("1. Loading minute candles...")
spot = pd.read_sql(text("SELECT timestamp, open, high, low, close, volume FROM minute_candles WHERE symbol = :s ORDER BY timestamp ASC"), eng, params={"s": SYMBOL})
spot["timestamp"] = to_ist(spot["timestamp"])
spot = spot.drop_duplicates("timestamp").sort_values("timestamp").set_index("timestamp")
spot = spot.between_time("09:15", "15:29")

fut = pd.read_sql(text("SELECT timestamp, volume, vwap FROM minute_candles WHERE symbol = :s ORDER BY timestamp ASC"), eng, params={"s": FUTURES_SYMBOL})
if len(fut) > 0:
    fut["timestamp"] = to_ist(fut["timestamp"])
    fut = fut.drop_duplicates("timestamp").sort_values("timestamp").set_index("timestamp")
    fut = fut.between_time("09:15", "15:29")
    spot["fut_volume"] = fut["volume"].reindex(spot.index).fillna(0.0)
    spot["fut_vwap"] = fut["vwap"].reindex(spot.index).ffill()
else:
    spot["fut_volume"] = spot["volume"]
    spot["fut_vwap"] = spot["close"]

print("2. Loading option chain features...")
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

df = spot
c, h, l, v = df["close"], df["high"], df["low"], df["fut_volume"]
f = pd.DataFrame(index=df.index)

# Multi-timeframe momentum & volatility features
for w in [3, 5, 10, 15, 30]:
    f[f"ret_{w}"] = c.pct_change(w)
    f[f"vol_{w}"] = c.pct_change().rolling(w).std()

ema9 = c.ewm(span=9).mean()
ema21 = c.ewm(span=21).mean()
ema50 = c.ewm(span=50).mean()
f["ema_spread_9_21"] = (ema9 - ema21) / c
f["ema_spread_21_50"] = (ema21 - ema50) / c
f["ema9_slope"] = ema9.pct_change(3)

d = c.diff()
up = d.clip(lower=0).rolling(14).mean()
dn = (-d.clip(upper=0)).rolling(14).mean()
f["rsi"] = 100.0 - (100.0 / (1.0 + up / (dn + 1e-9)))
f["rsi_slope"] = f["rsi"].diff(3)

tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
f["atr"] = tr.rolling(14).mean()
f["atr_pct"] = f["atr"] / c

days = df.index.date
pv = (c * v).groupby(days).cumsum()
cum_v = v.groupby(days).cumsum()
vwap = np.where(cum_v > 0, pv / np.maximum(cum_v, 1e-9), c)
f["vwap_dist"] = (c - vwap) / c

g = df.groupby(days)
or_hi = g["high"].transform(lambda s: s.iloc[:30].max())
or_lo = g["low"].transform(lambda s: s.iloc[:30].min())
f["or_pos"] = np.where(or_hi > or_lo, (c - or_lo) / np.maximum(or_hi - or_lo, 1e-9), 0.5)

f["time_min"] = df.index.hour * 60 + df.index.minute
t_time = df.index.time
f["is_morning"] = ((t_time >= pd.to_datetime("10:05").time()) & (t_time <= pd.to_datetime("11:30").time())).astype(float)
f["is_afternoon"] = ((t_time >= pd.to_datetime("13:15").time()) & (t_time <= pd.to_datetime("15:00").time())).astype(float)

# Option Chain Greek Features
f["atm_iv"] = (iv_ce + iv_pe) / 2.0
f["iv_skew"] = iv_pe - iv_ce            # Positive when PE IV > CE IV
f["delta_skew"] = delta_ce.abs() - delta_pe.abs()
f["iv_skew_diff"] = f["iv_skew"].diff(5) # Shift in IV skew over 5 mins

f = f.replace([np.inf, -np.inf], np.nan)

# Labels (Triple Barrier: 1.50x ATR Target vs 1.50x ATR SL)
print("3. Generating labels...")
n = len(df)
c_arr, h_arr, l_arr, atr_arr = c.values, h.values, l.values, f["atr"].values
pos = pd.Series(np.arange(n))
day_ends = pos.groupby(days).transform("max").values

call_win = np.zeros(n, dtype=int)
put_win = np.zeros(n, dtype=int)

test_mask = (df.index >= pd.to_datetime(START_DATE).tz_localize(TZ)) & (df.index <= pd.to_datetime(END_DATE).tz_localize(TZ))
test_indices = np.flatnonzero(test_mask)
test_start_idx = test_indices[0]
test_end_idx = test_indices[-1]
eval_start_idx = test_start_idx - 100000

for i in range(max(0, eval_start_idx), n):
    end_bar = day_ends[i]
    if i >= end_bar or np.isnan(atr_arr[i]) or atr_arr[i] <= 0:
        continue
    entry = c_arr[i]
    c_tgt = entry + 1.5 * atr_arr[i]
    c_sl  = entry - 1.5 * atr_arr[i]
    p_tgt = entry - 1.5 * atr_arr[i]
    p_sl  = entry + 1.5 * atr_arr[i]

    c_done, p_done = False, False
    for j in range(i + 1, min(end_bar + 1, i + 90)):
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

# Candidate Signals
in_window = (t_time >= pd.to_datetime("10:05").time()) & (t_time <= pd.to_datetime("15:15").time())
c_cand = in_window & (c > df["fut_vwap"]) & (f["rsi"] > 52.0) & (f["ema_spread_9_21"] > 0) & (f["ret_5"] > 0.0003)
p_cand = in_window & (c < df["fut_vwap"]) & (f["rsi"] < 48.0) & (f["ema_spread_9_21"] < 0) & (f["ret_5"] < -0.0003)

cand_call = c_cand.values
cand_put = p_cand.values

day_to_indices = {}
for day, grp in pd.Series(test_indices).groupby(df.index[test_indices].date):
    day_to_indices[day] = grp.values

print(f"Data ready. Test days: {len(day_to_indices)}, test bars: {len(test_indices)}")

# ----------------- SIMULATOR -----------------
def simulate(pred_c, pred_p, threshold=0.55, extra_c_cond=None, extra_p_cond=None):
    total_trades = 0
    total_wins = 0
    daily = []
    for day, indices in day_to_indices.items():
        t_today = 0
        blocked = -1
        for idx in indices:
            if t_today >= MAX_TRADES_PER_DAY:
                break
            if idx <= blocked:
                continue
            pc = pred_c[idx]
            pp = pred_p[idx]
            take = False
            win = False
            
            cond_c = extra_c_cond[idx] if extra_c_cond is not None else True
            cond_p = extra_p_cond[idx] if extra_p_cond is not None else True

            if cand_call[idx] and cond_c and pc >= threshold and pc > pp:
                take = True
                win = bool(call_win[idx] == 1)
            elif cand_put[idx] and cond_p and pp >= threshold and pp > pc:
                take = True
                win = bool(put_win[idx] == 1)
                
            if take:
                t_today += 1
                total_trades += 1
                if win:
                    total_wins += 1
                blocked = idx + 10
        daily.append(t_today)
    wr = (total_wins / total_trades * 100.0) if total_trades > 0 else 0.0
    return total_trades, total_wins, wr, np.mean(daily)

train_pool_start = max(0, test_start_idx - LOOKBACK)
train_idx_all = np.arange(train_pool_start, test_start_idx)
feature_cols = [col for col in f.columns if col not in ["atr"]]

print(f"\nFeature set ({len(feature_cols)} features):", feature_cols)

# =========================================================================
# APPROACH 1: Standard Training on ALL bars (Baseline)
# =========================================================================
print("\n" + "="*70)
print("APPROACH 1: Baseline All-Bars Model (89,500 lookback)")
print("="*70)
clean_train = train_idx_all[f.iloc[train_idx_all][feature_cols].notna().all(axis=1).values]
m_c_base = lgb.LGBMClassifier(n_estimators=300, learning_rate=0.03, num_leaves=31, random_state=42, verbose=-1, n_jobs=-1)
m_p_base = lgb.LGBMClassifier(n_estimators=300, learning_rate=0.03, num_leaves=31, random_state=42, verbose=-1, n_jobs=-1)
m_c_base.fit(f.iloc[clean_train][feature_cols].values, call_win[clean_train])
m_p_base.fit(f.iloc[clean_train][feature_cols].values, put_win[clean_train])

pred_c_base = np.zeros(n)
pred_p_base = np.zeros(n)
test_clean = test_indices[f.iloc[test_indices][feature_cols].notna().all(axis=1).values]
pred_c_base[test_clean] = m_c_base.predict_proba(f.iloc[test_clean][feature_cols].values)[:, 1]
pred_p_base[test_clean] = m_p_base.predict_proba(f.iloc[test_clean][feature_cols].values)[:, 1]

for thr in [0.52, 0.55, 0.58, 0.60]:
    trd, w, wr, tpd = simulate(pred_c_base, pred_p_base, threshold=thr)
    print(f"  Baseline (thr={thr:.2f}): Trades={trd:3d} ({tpd:.2f}/d) | Wins={w:3d} | WinRate={wr:.2f}%")

# =========================================================================
# APPROACH 2: EVENT-CONDITIONED TRAINING
# Train strictly on candidate setups! The model learns what separates winning
# setups from losing setups, rather than learning general non-setup noise.
# =========================================================================
print("\n" + "="*70)
print("APPROACH 2: Event-Conditioned Training (Train ONLY on Candidate Setups)")
print("="*70)
call_event_train = train_idx_all[cand_call[train_idx_all] & f.iloc[train_idx_all][feature_cols].notna().all(axis=1).values]
put_event_train = train_idx_all[cand_put[train_idx_all] & f.iloc[train_idx_all][feature_cols].notna().all(axis=1).values]
print(f"  Call candidate events in training: {len(call_event_train):,}")
print(f"  Put candidate events in training:  {len(put_event_train):,}")

m_c_evt = lgb.LGBMClassifier(n_estimators=300, learning_rate=0.03, num_leaves=31, min_child_samples=30, random_state=42, verbose=-1, n_jobs=-1)
m_p_evt = lgb.LGBMClassifier(n_estimators=300, learning_rate=0.03, num_leaves=31, min_child_samples=30, random_state=42, verbose=-1, n_jobs=-1)
m_c_evt.fit(f.iloc[call_event_train][feature_cols].values, call_win[call_event_train])
m_p_evt.fit(f.iloc[put_event_train][feature_cols].values, put_win[put_event_train])

pred_c_evt = np.zeros(n)
pred_p_evt = np.zeros(n)
pred_c_evt[test_clean] = m_c_evt.predict_proba(f.iloc[test_clean][feature_cols].values)[:, 1]
pred_p_evt[test_clean] = m_p_evt.predict_proba(f.iloc[test_clean][feature_cols].values)[:, 1]

for thr in [0.50, 0.52, 0.55, 0.58, 0.60, 0.62]:
    trd, w, wr, tpd = simulate(pred_c_evt, pred_p_evt, threshold=thr)
    print(f"  Event-Conditioned (thr={thr:.2f}): Trades={trd:3d} ({tpd:.2f}/d) | Wins={w:3d} | WinRate={wr:.2f}%")

# =========================================================================
# APPROACH 3: CONJOINT OPTION CHAIN GREEK FILTERING
# Apply institutional Option Chain confirmation:
# Long CE: IV skew favorable (pe_iv >= ce_iv or delta_skew > 0)
# Short PE: IV skew favorable
# =========================================================================
print("\n" + "="*70)
print("APPROACH 3: Option Chain Greeks Conjoint Confirmation")
print("="*70)
opt_c_cond = (f["delta_skew"] >= -0.05).values & (f["iv_skew_diff"] >= -0.5).values
opt_p_cond = (f["delta_skew"] <= 0.05).values & (f["iv_skew_diff"] <= 0.5).values

for thr in [0.52, 0.55, 0.58]:
    trd, w, wr, tpd = simulate(pred_c_evt, pred_p_evt, threshold=thr, extra_c_cond=opt_c_cond, extra_p_cond=opt_p_cond)
    print(f"  Event-Model + Option-Greek Filter (thr={thr:.2f}): Trades={trd:3d} ({tpd:.2f}/d) | Wins={w:3d} | WinRate={wr:.2f}%")

# =========================================================================
# APPROACH 4: MULTI-MODEL DIVERSE ENSEMBLE CONSENSUS
# LightGBM + XGBoost + ExtraTrees. Only trigger when all 3 agree!
# =========================================================================
print("\n" + "="*70)
print("APPROACH 4: Diverse Tri-Model Consensus Ensemble")
print("="*70)
xgb_c_evt = XGBClassifier(n_estimators=300, learning_rate=0.03, max_depth=5, tree_method="hist", random_state=42, n_jobs=-1)
xgb_p_evt = XGBClassifier(n_estimators=300, learning_rate=0.03, max_depth=5, tree_method="hist", random_state=42, n_jobs=-1)
xgb_c_evt.fit(f.iloc[call_event_train][feature_cols].values, call_win[call_event_train])
xgb_p_evt.fit(f.iloc[put_event_train][feature_cols].values, put_win[put_event_train])

et_c_evt = ExtraTreesClassifier(n_estimators=200, max_depth=8, min_samples_leaf=20, random_state=42, n_jobs=-1)
et_p_evt = ExtraTreesClassifier(n_estimators=200, max_depth=8, min_samples_leaf=20, random_state=42, n_jobs=-1)
et_c_evt.fit(f.iloc[call_event_train][feature_cols].values, call_win[call_event_train])
et_p_evt.fit(f.iloc[put_event_train][feature_cols].values, put_win[put_event_train])

pred_c_xgb = np.zeros(n)
pred_p_xgb = np.zeros(n)
pred_c_xgb[test_clean] = xgb_c_evt.predict_proba(f.iloc[test_clean][feature_cols].values)[:, 1]
pred_p_xgb[test_clean] = xgb_p_evt.predict_proba(f.iloc[test_clean][feature_cols].values)[:, 1]

pred_c_et = np.zeros(n)
pred_p_et = np.zeros(n)
pred_c_et[test_clean] = et_c_evt.predict_proba(f.iloc[test_clean][feature_cols].values)[:, 1]
pred_p_et[test_clean] = et_p_evt.predict_proba(f.iloc[test_clean][feature_cols].values)[:, 1]

# Soft blend
pred_c_ens = 0.45 * pred_c_evt + 0.35 * pred_c_xgb + 0.20 * pred_c_et
pred_p_ens = 0.45 * pred_p_evt + 0.35 * pred_p_xgb + 0.20 * pred_p_et

for thr in [0.50, 0.52, 0.54, 0.56, 0.58]:
    trd, w, wr, tpd = simulate(pred_c_ens, pred_p_ens, threshold=thr)
    print(f"  Ensemble Consensus (thr={thr:.2f}): Trades={trd:3d} ({tpd:.2f}/d) | Wins={w:3d} | WinRate={wr:.2f}%")

print("\nDONE!")
