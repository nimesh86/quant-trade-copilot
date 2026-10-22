"""
Forensic Failure Audit: Autopsy of Every Losing Trade from the Primary Model.
Goal:
1. Extract every single trade that failed in the 6-month test period (2026-04-01 to 2026-09-30).
2. Trace the exact bar-by-bar price path after entry:
   - Max Favorable Excursion (MFE): Did it go into profit before dying? How much?
   - Max Adverse Excursion (MAE): How fast did it hit the stop loss?
   - Time-to-Stop: Did it die immediately (1-3 bars) or bleed out slowly (15+ bars)?
3. Forensic Feature Breakdown:
   - What were the Option Chain Greeks (ATM IV, IV Skew, Delta Skew)?
   - What were the candle structures (wick/body ratio, volume surge)?
   - What were the higher-timeframe trends and Opening Range positions?
4. Statistical clustering into distinct Failure Archetypes.
5. Trace back to Training Lookback (89,505 bars):
   - Was this data present in the training set?
   - Was it drowned out / overwritten by normal market conditions?
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
LOOKBACK = 89505
MAX_TRADES_PER_DAY = 5

def to_ist(s):
    return pd.to_datetime(s, utc=True).dt.tz_convert(TZ)

eng = create_engine(DB_URL)

print("1. Loading minute candles & option chain from database...", flush=True)
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
c, h, l, o_px, v = df["close"], df["high"], df["low"], df["open"], df["fut_volume"]
f = pd.DataFrame(index=df.index)

# Features
for w in [3, 5, 10, 15, 30]:
    f[f"ret_{w}"] = c.pct_change(w)
    f[f"vol_{w}"] = c.pct_change().rolling(w).std()

ema9 = c.ewm(span=9).mean()
ema21 = c.ewm(span=21).mean()
ema50 = c.ewm(span=50).mean()
f["ema_spread_9_21"] = (ema9 - ema21) / c
f["ema_spread_21_50"] = (ema21 - ema50) / c
f["ema9_slope"] = ema9.pct_change(3)

# 15m Higher Timeframe Trend
f["htf_trend"] = (c.ewm(span=9*15).mean() - c.ewm(span=21*15).mean()) / c

d = c.diff()
up = d.clip(lower=0).rolling(14).mean()
dn = (-d.clip(upper=0)).rolling(14).mean()
f["rsi"] = 100.0 - (100.0 / (1.0 + up / (dn + 1e-9)))
f["rsi_slope"] = f["rsi"].diff(3)

tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
f["atr"] = tr.rolling(14).mean()
f["atr_pct"] = f["atr"] / c

# Candle morphology & wick metrics
candle_range = (h - l).replace(0, 1e-6)
f["body_ratio"] = (c - o_px).abs() / candle_range
f["upper_wick"] = (h - np.maximum(c, o_px)) / candle_range
f["lower_wick"] = (np.minimum(c, o_px) - l) / candle_range
f["vol_ratio"] = v / (v.rolling(10).mean().replace(0, 1e-6))

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

# Labels for Primary Model (1.5x Tgt vs 1.5x SL)
print("2. Generating Triple Barrier labels (1.5x ATR)...", flush=True)
pos = pd.Series(np.arange(n))
day_ends = pos.groupby(days).transform("max").values
c_arr, h_arr, l_arr, atr_arr = c.values, h.values, l.values, f["atr"].values

call_win = np.zeros(n, dtype=int)
put_win = np.zeros(n, dtype=int)

for i in range(max(0, test_start_idx - 100000), n):
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

# Candidate setups
in_window = (t_time >= pd.to_datetime("10:05").time()) & (t_time <= pd.to_datetime("15:15").time())
c_cand = in_window & (c > df["fut_vwap"]) & (f["rsi"] > 52.0) & (f["ema_spread_9_21"] > 0) & (f["ret_5"] > 0.0003)
p_cand = in_window & (c < df["fut_vwap"]) & (f["rsi"] < 48.0) & (f["ema_spread_9_21"] < 0) & (f["ret_5"] < -0.0003)
cand_call = c_cand.values
cand_put = p_cand.values

# Train Primary Model on Lookback 89505
print("3. Training Primary Model on 89,505 bars...", flush=True)
train_pool_start = max(0, test_start_idx - LOOKBACK)
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

# 4. Extract Every Trade & Conduct Detailed Forensic Autopsy
print("4. Executing Forensic Autopsy of all trades...", flush=True)
day_to_indices = {}
for day, grp in pd.Series(test_indices).groupby(df.index[test_indices].date):
    day_to_indices[day] = grp.values

trades = []
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
        side = None
        win = False
        if cand_call[idx] and pc >= 0.55 and pc > pp:
            take = True
            side = "CE"
            win = bool(call_win[idx] == 1)
        elif cand_put[idx] and pp >= 0.55 and pp > pc:
            take = True
            side = "PE"
            win = bool(put_win[idx] == 1)

        if take:
            # Autopsy path forward
            entry_px = c_arr[idx]
            entry_atr = atr_arr[idx]
            end_bar = day_ends[idx]
            
            bars_to_exit = 0
            exit_reason = "TIMEOUT"
            mfe_pts = 0.0 # Max Favorable Excursion
            mae_pts = 0.0 # Max Adverse Excursion
            opp_side_hit_tgt = False # Would the OPPOSITE side have WON?

            tgt_px = entry_px + (1.5 * entry_atr if side == "CE" else -1.5 * entry_atr)
            sl_px  = entry_px - (1.5 * entry_atr if side == "CE" else -1.5 * entry_atr)
            opp_tgt_px = sl_px # Opposing side target is our SL level

            for k in range(idx + 1, min(end_bar + 1, idx + 90)):
                bars_to_exit += 1
                if side == "CE":
                    fav = h_arr[k] - entry_px
                    adv = entry_px - l_arr[k]
                    mfe_pts = max(mfe_pts, fav)
                    mae_pts = max(mae_pts, adv)
                    if h_arr[k] >= tgt_px and l_arr[k] > sl_px:
                        exit_reason = "TARGET"
                        break
                    elif l_arr[k] <= sl_px:
                        exit_reason = "STOP"
                        opp_side_hit_tgt = True
                        break
                else: # PE
                    fav = entry_px - l_arr[k]
                    adv = h_arr[k] - entry_px
                    mfe_pts = max(mfe_pts, fav)
                    mae_pts = max(mae_pts, adv)
                    if l_arr[k] <= tgt_px and h_arr[k] < sl_px:
                        exit_reason = "TARGET"
                        break
                    elif h_arr[k] >= sl_px:
                        exit_reason = "STOP"
                        opp_side_hit_tgt = True
                        break

            mfe_atr = mfe_pts / entry_atr if entry_atr > 0 else 0
            mae_atr = mae_pts / entry_atr if entry_atr > 0 else 0

            trade_record = {
                "idx": idx,
                "timestamp": df.index[idx],
                "side": side,
                "win": int(win),
                "exit_reason": exit_reason,
                "bars_to_exit": bars_to_exit,
                "mfe_atr": round(mfe_atr, 3),
                "mae_atr": round(mae_atr, 3),
                "opp_side_win": int(opp_side_hit_tgt),
                "prob": pc if side == "CE" else pp,
                "upper_wick": f.iloc[idx]["upper_wick"],
                "lower_wick": f.iloc[idx]["lower_wick"],
                "body_ratio": f.iloc[idx]["body_ratio"],
                "vol_ratio": f.iloc[idx]["vol_ratio"],
                "or_pos": f.iloc[idx]["or_pos"],
                "htf_trend": f.iloc[idx]["htf_trend"],
                "iv_skew": f.iloc[idx]["iv_skew"],
                "iv_skew_diff": f.iloc[idx]["iv_skew_diff"],
                "atm_iv": f.iloc[idx]["atm_iv"],
                "vwap_dist": f.iloc[idx]["vwap_dist"],
                "rsi": f.iloc[idx]["rsi"],
                "time_min": f.iloc[idx]["time_min"],
            }
            trades.append(trade_record)
            t_today += 1
            blocked = idx + 10

tdf = pd.DataFrame(trades)
print(f"\nTotal trades simulated: {len(tdf)}")
print(f"Wins: {tdf['win'].sum()} ({tdf['win'].mean()*100:.1f}%)")
print(f"Losses: {len(tdf) - tdf['win'].sum()} ({(1-tdf['win'].mean())*100:.1f}%)")

# 5. FORENSIC AUTOPSY OF LOSING TRADES
losses = tdf[tdf["win"] == 0].copy()
print("\n" + "="*80)
print("  FORENSIC FAILURE AUDIT REPORT (ANALYSIS OF ALL LOSING TRADES)")
print("="*80)

# Failure Mode 1: Reversal Traps (Opposite side would have hit target)
opp_wins = losses["opp_side_win"].sum()
print(f"1. DIRECT REVERSAL TRAPS: {opp_wins} out of {len(losses)} losing trades ({opp_wins/len(losses)*100:.1f}%)")
print(f"   -> Meaning: In {opp_wins/len(losses)*100:.1f}% of losses, the market moved straight to the opposite barrier!")
print(f"   -> IF FLIPPED, these {opp_wins} losses become WINNING TRADES!")

# Failure Mode 2: Profit Giveback (Went into profit >= 0.5x ATR, then reversed to stop)
profit_giveback = (losses["mfe_atr"] >= 0.50).sum()
print(f"\n2. PROFIT GIVEBACK TRADES: {profit_giveback} out of {len(losses)} losing trades ({profit_giveback/len(losses)*100:.1f}%)")
print(f"   -> Meaning: In {profit_giveback} trades, the position was up >= +0.50x ATR in profit before dying!")
print(f"   -> Average MFE on giveback trades: {losses[losses['mfe_atr'] >= 0.50]['mfe_atr'].mean():.2f}x ATR")

# Failure Mode 3: Quick Death (Stopped out in <= 5 bars) vs Slow Bleed
quick_death = (losses["bars_to_exit"] <= 5).sum()
slow_bleed = (losses["bars_to_exit"] > 15).sum()
print(f"\n3. SPEED OF FAILURE:")
print(f"   -> Immediate Reversal (<= 5 bars): {quick_death} trades ({quick_death/len(losses)*100:.1f}%)")
print(f"   -> Slow Bleed / Stagnation (> 15 bars): {slow_bleed} trades ({slow_bleed/len(losses)*100:.1f}%)")

# 6. STATISTICAL SIGNATURE COMPARISON (WINS VS LOSSES)
print("\n" + "="*80)
print("  STATISTICAL SIGNATURE: WINNING TRADES VS LOSING TRADES")
print("="*80)
wins = tdf[tdf["win"] == 1]
comparison = pd.DataFrame({
    "Winning Trades Mean": wins[["upper_wick", "lower_wick", "body_ratio", "vol_ratio", "or_pos", "htf_trend", "iv_skew", "iv_skew_diff", "atm_iv", "mfe_atr", "bars_to_exit"]].mean(),
    "Losing Trades Mean": losses[["upper_wick", "lower_wick", "body_ratio", "vol_ratio", "or_pos", "htf_trend", "iv_skew", "iv_skew_diff", "atm_iv", "mfe_atr", "bars_to_exit"]].mean(),
})
comparison["Forensic Delta"] = comparison["Winning Trades Mean"] - comparison["Losing Trades Mean"]
print(comparison.to_string())

tdf.to_csv("forensic_autopsy_trades.csv", index=False)
print("\nForensic autopsy saved to forensic_autopsy_trades.csv")
