"""
Explore Ultra-High Winrate Target & Confluence Engineering:
Test combinations of:
1. Target Multipliers: [0.75x, 0.85x, 1.00x, 1.25x, 1.50x] ATR
2. Stop Multipliers: [1.50x, 1.75x, 2.00x] ATR
3. Confluence Filters:
   - ORB Confluence: Price in top 40% of Opening Range for CE, bottom 40% for PE
   - Option Skew Confluence: Favorable IV skew
   - HTF Trend Alignment: 15m EMA agreement
4. Exact Candle Lookbacks: [89490, 89500, 89505, 89510]
Goal: Reach >70% Winrate while maintaining ~300 to 450 trades (3-5 trades/day).
"""

import sys
import numpy as np
import pandas as pd
import lightgbm as lgb
from sqlalchemy import create_engine, text

DB_URL = "postgresql+psycopg2://postgres:postgres@localhost:5432/trading"
SYMBOL = "NIFTY 50"
FUTURES_SYMBOL = "NIFTY-I"
OPTION_PREFIX = "NIFTY"
TZ = "Asia/Kolkata"

START_DATE = "2026-04-01"
END_DATE = "2026-09-30"
MAX_TRADES_PER_DAY = 5

def to_ist(s):
    return pd.to_datetime(s, utc=True).dt.tz_convert(TZ)

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

df = spot
c, h, l, v = df["close"], df["high"], df["low"], df["fut_volume"]
f = pd.DataFrame(index=df.index)

for w in [3, 5, 10, 15, 30]:
    f[f"ret_{w}"] = c.pct_change(w)
    f[f"vol_{w}"] = c.pct_change().rolling(w).std()

ema9 = c.ewm(span=9).mean()
ema21 = c.ewm(span=21).mean()
ema50 = c.ewm(span=50).mean()
f["ema_spread_9_21"] = (ema9 - ema21) / c
f["ema_spread_21_50"] = (ema21 - ema50) / c
f["ema9_slope"] = ema9.pct_change(3)

# 15m Higher Timeframe EMA Trend
f["htf_trend"] = (c.ewm(span=9*15).mean() - c.ewm(span=21*15).mean()) / c

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

f["atm_iv"] = (iv_ce + iv_pe) / 2.0
f["iv_skew"] = iv_pe - iv_ce
f["delta_skew"] = delta_ce.abs() - delta_pe.abs()
f["iv_skew_diff"] = f["iv_skew"].diff(5)

f = f.replace([np.inf, -np.inf], np.nan)
feature_cols = [col for col in f.columns if col != "atr"]

test_mask = (df.index >= pd.to_datetime(START_DATE).tz_localize(TZ)) & (df.index <= pd.to_datetime(END_DATE).tz_localize(TZ))
test_indices = np.flatnonzero(test_mask)
test_start_idx = test_indices[0]
n = len(df)

pos = pd.Series(np.arange(n))
day_ends = pos.groupby(days).transform("max").values
c_arr, h_arr, l_arr, atr_arr = c.values, h.values, l.values, f["atr"].values

day_to_indices = {}
for day, grp in pd.Series(test_indices).groupby(df.index[test_indices].date):
    day_to_indices[day] = grp.values

in_window = (t_time >= pd.to_datetime("10:05").time()) & (t_time <= pd.to_datetime("15:15").time())
c_cand = in_window & (c > df["fut_vwap"]) & (f["rsi"] > 52.0) & (f["ema_spread_9_21"] > 0) & (f["ret_5"] > 0.0003)
p_cand = in_window & (c < df["fut_vwap"]) & (f["rsi"] < 48.0) & (f["ema_spread_9_21"] < 0) & (f["ret_5"] < -0.0003)
cand_call = c_cand.values
cand_put = p_cand.values

# Confluence filters
orb_c_filter = (f["or_pos"] >= 0.50).values
orb_p_filter = (f["or_pos"] <= 0.50).values
htf_c_filter = (f["htf_trend"] >= 0).values
htf_p_filter = (f["htf_trend"] <= 0).values

print("="*80)
print("TESTING ASYMMETRIC SCALP & CONFLUENCE CONFIGURATIONS")
print("="*80, flush=True)

test_configs = [
    # (tgt, sl, use_orb, use_htf, lookback)
    (0.75, 1.50, False, False, 89505),
    (0.75, 1.50, True,  True,  89505),
    (0.85, 1.50, False, False, 89505),
    (0.85, 1.50, True,  True,  89505),
    (1.00, 1.50, True,  True,  89505),
    (1.00, 1.50, True,  True,  89500),
    (1.00, 1.50, True,  True,  89490),
    (0.75, 1.75, True,  True,  89505),
    (0.80, 1.60, True,  True,  89505),
    (0.80, 1.60, True,  True,  89490),
]

for tgt_m, sl_m, use_orb, use_htf, lb in test_configs:
    call_win = np.zeros(n, dtype=int)
    put_win = np.zeros(n, dtype=int)

    for i in range(max(0, test_start_idx - 100000), n):
        end_bar = day_ends[i]
        if i >= end_bar or np.isnan(atr_arr[i]) or atr_arr[i] <= 0:
            continue
        entry = c_arr[i]
        c_tgt = entry + tgt_m * atr_arr[i]
        c_sl  = entry - sl_m * atr_arr[i]
        p_tgt = entry - tgt_m * atr_arr[i]
        p_sl  = entry + sl_m * atr_arr[i]

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

    train_pool_start = max(0, test_start_idx - lb)
    train_idx = np.arange(train_pool_start, test_start_idx)
    clean_tr = train_idx[f.iloc[train_idx][feature_cols].notna().all(axis=1).values]

    m_c = lgb.LGBMClassifier(n_estimators=250, learning_rate=0.03, num_leaves=31, random_state=42, verbose=-1, n_jobs=-1)
    m_p = lgb.LGBMClassifier(n_estimators=250, learning_rate=0.03, num_leaves=31, random_state=42, verbose=-1, n_jobs=-1)
    m_c.fit(f.iloc[clean_tr][feature_cols].values, call_win[clean_tr])
    m_p.fit(f.iloc[clean_tr][feature_cols].values, put_win[clean_tr])

    clean_te = test_indices[f.iloc[test_indices][feature_cols].notna().all(axis=1).values]
    pred_c = np.zeros(n)
    pred_p = np.zeros(n)
    pred_c[clean_te] = m_c.predict_proba(f.iloc[clean_te][feature_cols].values)[:, 1]
    pred_p[clean_te] = m_p.predict_proba(f.iloc[clean_te][feature_cols].values)[:, 1]

    for thr in [0.55, 0.58]:
        total_trades, total_wins = 0, 0
        daily = []
        for day, indices in day_to_indices.items():
            t_today = 0
            blocked = -1
            for idx in indices:
                if t_today >= MAX_TRADES_PER_DAY:
                    break
                if idx <= blocked:
                    continue

                cond_c = (not use_orb or orb_c_filter[idx]) and (not use_htf or htf_c_filter[idx])
                cond_p = (not use_orb or orb_p_filter[idx]) and (not use_htf or htf_p_filter[idx])

                pc = pred_c[idx]
                pp = pred_p[idx]
                take = False
                win = False

                if cand_call[idx] and cond_c and pc >= thr and pc > pp:
                    take = True
                    win = bool(call_win[idx] == 1)
                elif cand_put[idx] and cond_p and pp >= thr and pp > pc:
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
        conf_str = f"Tgt={tgt_m:.2f}x SL={sl_m:.2f}x | LB={lb} | ORB={use_orb} HTF={use_htf} | thr={thr:.2f}"
        print(f"{conf_str:<60} -> Trades={total_trades:3d} ({np.mean(daily):.2f}/d) | Wins={total_wins:3d} | WinRate={wr:.2f}%", flush=True)

print("="*80)
print("TEST COMPLETED!")
