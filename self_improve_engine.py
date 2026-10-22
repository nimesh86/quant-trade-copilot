"""
Self-improvement experiment engine for intraday option signals (reads your Postgres DB).

Setup:  pip install pandas numpy lightgbm xgboost sqlalchemy psycopg2-binary
Config: DB settings (DB_HOST, DB_PORT, DB_NAME, DB_USER, DB_PASSWORD) are set in the DATABASE
        section below, matching your local 'trading' database.

How a signal is judged:
  At each 5-min bar the model may signal BUY CE or BUY PE.
  Entry = the ATM contract (relative_strike = 0) of the nearest expiry at that bar.
  That SAME contract is then followed bar by bar (even if it stops being ATM).
  Success = its ltp reaches +TARGET before -STOP within HORIZON bars, same day.

Data split (keeps the result honest):
  [ ......... TRAIN pool ......... | VALIDATION (120d) | FINAL TEST (180d) ]
  Experiments are ranked on VALIDATION only. The winner is refit and scored once on FINAL TEST.
"""
import os
import random

import lightgbm as lgb
import numpy as np
import pandas as pd
from sqlalchemy import create_engine, text
from xgboost import XGBClassifier

# ---------------- CONFIG ----------------
SPOT_SYMBOL = "NIFTY-I"       # minute_candles symbol used for price/volume/OI features
INDEX_SYMBOL = "NIFTY 50"     # minute_candles spot index, used to find the true ATM strike
OPTION_SOURCE = "option_chain"   # "option_chain" (built by build_option_chain.py) or "option_minute_candles"
OPTION_PREFIX = "NIFTY"       # option_minute_candles symbols start with this
MACRO_SYMBOL = "NIFTY-I"      # features_macro.symbol (pcr, iv, oi_change)
K_STRIKES = 3                 # load option rows with |relative_strike| <= K
CANDLE_TS_IS_BAR_START = True # True if a 1-min candle stamped 09:15 covers 09:15-09:16
TZ = "Asia/Kolkata"

TARGET = 0.10                 # +10% on premium = success (must be >= 10%)
STOP = 0.10                   # stop-loss on premium
HORIZON = 12                  # bars allowed (12 x 5 min = 60 min)
COST = 0.01                   # round-trip cost + slippage, fraction of premium
MIN_PREMIUM = 15.0            # skip entries where premium is too small for a 10% move to mean anything
N_TRIALS = 10000
MIN_TRADES = 40
TEST_DAYS = 180               # last ~6 months, touched once at the end
VAL_DAYS = 120                # ~4 months used to rank the experiments
FIRST_TRADE_BAR = 6           # no entries while the first-30-min range is forming
SEED = 42
BASE_COLS = {"open", "high", "low", "close", "volume"}

assert TARGET >= 0.10, "Target below 10% is treated as short-term noise"
rng = random.Random(SEED)


# ---------------- DATABASE ----------------
from trading_engine.core.keyvault import get_secret
DB_HOST = os.getenv("DB_HOST", "localhost")
DB_PORT = int(os.getenv("DB_PORT", "5432"))
DB_NAME = os.getenv("DB_NAME", "trading")
DB_USER = os.getenv("DB_USER", "postgres")
DB_PASSWORD = get_secret("DB_PASSWORD", "postgres")


def get_engine():
    url = f"postgresql+psycopg2://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}"
    return create_engine(url)


def to_ist(s):
    return pd.to_datetime(s, utc=True).dt.tz_convert(TZ)


def load_bars(eng):
    """1-min candles -> 5-min bars. Bar label = bar END time, so a bar at 09:20 only uses data up to 09:20."""
    c = pd.read_sql(text(
        "SELECT timestamp, open, high, low, close, volume, oi FROM minute_candles "
        "WHERE symbol = :s ORDER BY timestamp"), eng, params={"s": SPOT_SYMBOL})
    assert len(c), f"No rows in minute_candles for symbol {SPOT_SYMBOL}"
    c["timestamp"] = to_ist(c["timestamp"])
    c = c.drop_duplicates("timestamp").set_index("timestamp")
    if not CANDLE_TS_IS_BAR_START:
        c.index = c.index - pd.Timedelta(minutes=1)
    c = c.between_time("09:15", "15:29")
    b = c.resample("5min", closed="left", label="right").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last",
         "volume": "sum", "oi": "last"}).dropna(subset=["close"])
    b["fut_oi_chg"] = (b["oi"].replace(0, np.nan).pct_change(3)
                       .replace([np.inf, -np.inf], np.nan))
    return b.drop(columns="oi")


def add_macro(eng, bars):
    m = pd.read_sql(text(
        "SELECT timestamp, pcr, iv, oi_change AS macro_oi_change FROM features_macro "
        "WHERE symbol = :s ORDER BY timestamp"), eng, params={"s": MACRO_SYMBOL})
    if m.empty:
        print(f"features_macro has no rows for {MACRO_SYMBOL}; continuing without pcr/iv.")
        return bars
    m["timestamp"] = to_ist(m["timestamp"])
    m = m.drop_duplicates("timestamp").set_index("timestamp")
    return bars.join(m.reindex(bars.index, method="ffill", tolerance=pd.Timedelta("10min")))


def index_close(eng, bars):
    """Spot-index close per bar (for the true ATM strike). Falls back to futures."""
    c = pd.read_sql(text("SELECT timestamp, close FROM minute_candles "
                         "WHERE symbol = :s ORDER BY timestamp"),
                    eng, params={"s": INDEX_SYMBOL})
    if c.empty:
        print(f"WARNING: no candles for {INDEX_SYMBOL}; using {SPOT_SYMBOL} to find ATM. "
              "Futures trade at a premium to spot, so ATM can be off by 1-2 strikes.")
        return bars["close"].values
    c["timestamp"] = to_ist(c["timestamp"])
    c = c.drop_duplicates("timestamp").set_index("timestamp")
    if not CANDLE_TS_IS_BAR_START:
        c.index = c.index - pd.Timedelta(minutes=1)
    s = c["close"].resample("5min", closed="left", label="right").last()
    return s.reindex(bars.index).values


def load_option_chain(eng, bars):
    """Reads the prepared option_chain table (see build_option_chain.py), nearest expiry, ATM +/- K."""
    q = text(f"""
        SELECT timestamp, symbol, expiry, option_type, relative_strike, ltp
        FROM option_chain
        WHERE underlying = :u
          AND relative_strike BETWEEN {-int(K_STRIKES)} AND {int(K_STRIKES)}
          AND ltp > 0
    """)
    o = pd.read_sql(q, eng, params={"u": OPTION_PREFIX})
    assert len(o), "option_chain has no rows; run build_option_chain.py first"
    o["timestamp"] = to_ist(o["timestamp"])
    o["expiry"] = pd.to_datetime(o["expiry"])
    o = o[o["expiry"] == o.groupby("timestamp")["expiry"].transform("min")]
    pos = pd.Series(np.arange(len(bars)), index=bars.index)
    o["bar"] = pos.reindex(o["timestamp"]).values
    o = o.dropna(subset=["bar"])
    o["bar"] = o["bar"].astype(int)
    return o


def load_options(eng, bars):
    """Option candles for the nearest expiry, aligned to the 5-min bars, ATM strikes +/- K."""
    shift = 1 if CANDLE_TS_IS_BAR_START else 0   # a candle stamped 09:19 closes at 09:20
    ist = f"(timestamp AT TIME ZONE '{TZ}')"
    q = text(f"""
        SELECT timestamp, symbol, expiry, option_type, strike, close AS ltp
        FROM option_minute_candles
        WHERE symbol LIKE :prefix
          AND option_type IN ('CE', 'PE')
          AND close > 0
          AND MOD(CAST(EXTRACT(MINUTE FROM {ist}) AS integer) + {shift}, 5) = 0
          AND expiry >= CAST({ist} AS date)
          AND expiry <= CAST({ist} AS date) + 7
    """)
    o = pd.read_sql(q, eng, params={"prefix": OPTION_PREFIX + "%"})
    assert len(o), "No rows in option_minute_candles after filters; run diagnose_option_data.py"
    o["timestamp"] = to_ist(o["timestamp"]) + pd.Timedelta(minutes=shift)
    o["expiry"] = pd.to_datetime(o["expiry"])
    pos = pd.Series(np.arange(len(bars)), index=bars.index)
    o["bar"] = pos.reindex(o["timestamp"]).values
    o = o.dropna(subset=["bar"])
    o["bar"] = o["bar"].astype(int)
    o["spot"] = index_close(eng, bars)[o["bar"].values]
    o = o.dropna(subset=["spot"])
    atm = np.round(o["spot"] / 50.0) * 50.0
    o["relative_strike"] = ((o["strike"] - atm) / 50.0).round().astype(int)
    o = o[o["relative_strike"].abs() <= K_STRIKES]
    o = o[o["expiry"] == o.groupby("timestamp")["expiry"].transform("min")]
    return o.drop(columns="spot")


# ---------------- OUTCOMES ----------------
def score_path(p, i, end):
    """Follow one contract from bar i; return (realised return, target_hit)."""
    j = min(i + HORIZON, end)
    if j <= i or not (p[i] >= MIN_PREMIUM):
        return np.nan, False
    path = p[i + 1:j + 1] / p[i] - 1
    t = np.flatnonzero(path >= TARGET)
    s = np.flatnonzero(path <= -STOP)
    t0 = t[0] if len(t) else 10 ** 9
    s0 = s[0] if len(s) else 10 ** 9
    if t0 < s0:
        return TARGET, True
    if s0 < 10 ** 9:
        return -STOP, False          # same-bar tie counts as stop (conservative)
    return path[-1], False           # timeout: exit at horizon


def contract_outcomes(entry_syms, opt, n, day_end):
    ret, hit = np.full(n, np.nan), np.zeros(n, bool)
    by_sym = {s: g for s, g in opt.groupby("symbol")}
    for s, ii in pd.Series(np.arange(n)).groupby(entry_syms):
        g = by_sym[s]
        dense = np.full(n, np.nan)
        dense[g["bar"].values] = g["ltp"].values
        dense = pd.Series(dense).ffill().values
        for i in ii.values:
            ret[i], hit[i] = score_path(dense, i, day_end[i])
    return ret, hit


def entry_symbols(opt, n, side):
    atm = opt[(opt["relative_strike"] == 0) & (opt["option_type"] == side)]
    atm = atm.drop_duplicates("bar").set_index("bar")["symbol"]
    return atm.reindex(range(n)).values


# ---------------- FEATURES ----------------
def day_info(df):
    days = df.index.date
    pos = pd.Series(np.arange(len(df)))
    return days, pos.groupby(days).transform("max").values, pos.groupby(days).cumcount().values


def make_features(df, days, bar_no, windows):
    c = df["close"]
    f = pd.DataFrame(index=df.index)
    for w in windows:
        f[f"ret_{w}"] = c.pct_change(w)
        f[f"vol_{w}"] = c.pct_change().rolling(w).std()
        f[f"rng_{w}"] = (df["high"].rolling(w).max() - df["low"].rolling(w).min()) / c
    ema20, ema50 = c.ewm(span=20).mean(), c.ewm(span=50).mean()
    f["ema_spread"] = (ema20 - ema50) / c
    f["ema20_slope"] = ema20.pct_change(3)
    d = c.diff()
    up = d.clip(lower=0).rolling(14).mean()
    dn = (-d.clip(upper=0)).rolling(14).mean()
    f["rsi"] = 100 - 100 / (1 + up / (dn + 1e-9))
    f["rsi_chg"] = f["rsi"].diff(3)
    tr = pd.concat([df["high"] - df["low"], (df["high"] - c.shift()).abs(),
                    (df["low"] - c.shift()).abs()], axis=1).max(axis=1)
    f["atr_pct"] = tr.rolling(14).mean() / c
    pv = (c * df["volume"]).groupby(days).cumsum()
    v = df["volume"].groupby(days).cumsum()
    f["vwap_dist"] = np.where(v > 0, c / (pv / np.maximum(v, 1e-9)) - 1.0, 0.0)
    g = df.groupby(days)
    or_hi = g["high"].transform(lambda s: s.iloc[:FIRST_TRADE_BAR].max())
    or_lo = g["low"].transform(lambda s: s.iloc[:FIRST_TRADE_BAR].min())
    f["or_pos"] = np.where(or_hi > or_lo, (c - or_lo) / np.maximum(or_hi - or_lo, 1e-9), 0.5)
    f.loc[bar_no < FIRST_TRADE_BAR, "or_pos"] = np.nan      # no lookahead
    f["bar_no"] = bar_no
    for col in df.columns:
        if col not in BASE_COLS:
            if df[col].notna().mean() > 0.5:
                f[col] = df[col]
    return f.replace([np.inf, -np.inf], np.nan)


# ---------------- MODELS ----------------
def make_model(cfg):
    if cfg["kind"] == "lgbm":
        return lgb.LGBMClassifier(
            objective="multiclass", num_class=3, n_estimators=cfg["n"],
            learning_rate=cfg["lr"], num_leaves=cfg["leaves"],
            min_child_samples=cfg["mcs"], subsample=0.8, subsample_freq=1,
            colsample_bytree=0.8, random_state=SEED, verbose=-1)
    return XGBClassifier(
        objective="multi:softprob", n_estimators=cfg["n"], learning_rate=cfg["lr"],
        max_depth=cfg["depth"], min_child_weight=cfg["mcs"] / 20, subsample=0.8,
        colsample_bytree=0.8, random_state=SEED, n_jobs=-1)


def sample_cfg():
    return dict(
        train_days=rng.choice([90, 180, 270, 365, 540, 720]),
        windows=rng.choice([(3, 6, 12), (5, 10, 20), (5, 20, 60), (10, 30, 60), (3, 12, 36)]),
        kind=rng.choice(["lgbm", "xgb"]),
        n=rng.choice([150, 300, 500]), lr=rng.choice([0.02, 0.05, 0.1]),
        leaves=rng.choice([7, 15, 31]), depth=rng.choice([3, 4, 6]),
        mcs=rng.choice([50, 100, 200]),
        thr=rng.choice([0.35, 0.40, 0.45, 0.50, 0.55]))


# ---------------- EVALUATION ----------------
def evaluate(P, idx, thr, R):
    p_ce, p_pe = P[:, 1], P[:, 2]
    sig = np.where((p_ce >= thr) & (p_ce > p_pe), 1,
                   np.where((p_pe >= thr) & (p_pe > p_ce), 2, 0))
    rets, hits, blocked = [], [], -1
    for k in np.flatnonzero(sig):
        i = idx[k]
        if i <= blocked:            # cooldown: no overlapping trades
            continue
        blocked = i + HORIZON
        side = "ce" if sig[k] == 1 else "pe"
        rets.append(R["r_" + side][i] - COST)
        hits.append(R["h_" + side][i])
    if not rets:
        return dict(n=0, hit=np.nan, ev=np.nan, pf=np.nan)
    r, h = np.array(rets), np.array(hits)
    gl = -r[r < 0].sum()
    return dict(n=len(r), hit=h.mean(), ev=r.mean(),
                pf=(r[r > 0].sum() / gl) if gl > 0 else np.inf)


# ---------------- MAIN ----------------
def main():
    eng = get_engine()
    bars = add_macro(eng, load_bars(eng))
    n = len(bars)
    opt = load_option_chain(eng, bars) if OPTION_SOURCE == "option_chain" else load_options(eng, bars)
    days, day_end, bar_no = day_info(bars)

    # expiry-day and days-to-expiry features (from the nearest expiry at each bar)
    exp = pd.to_datetime(opt.groupby("bar")["expiry"].first()).reindex(range(n))
    bar_dates = pd.DatetimeIndex(pd.to_datetime(bars.index.date)).values
    dte = (exp.values - bar_dates) / np.timedelta64(1, "D")
    bars["dte"] = dte
    bars["is_expiry"] = (dte == 0).astype(float)

    r_ce, h_ce = contract_outcomes(entry_symbols(opt, n, "CE"), opt, n, day_end)
    r_pe, h_pe = contract_outcomes(entry_symbols(opt, n, "PE"), opt, n, day_end)
    R = dict(r_ce=r_ce, h_ce=h_ce, r_pe=r_pe, h_pe=h_pe)
    y = np.where(h_ce & ~h_pe, 1, np.where(h_pe & ~h_ce, 2, 0))
    ok = ~np.isnan(r_ce) & ~np.isnan(r_pe) & (bar_no >= FIRST_TRADE_BAR)

    ts = bars.index
    print(f"DATA AUDIT: {n} bars from {ts[ok].min()} to {ts[ok].max()}; "
          f"{ok.sum()} bars have usable CE+PE entries ({ok.mean():.0%}); "
          f"base hit rate CE={h_ce[ok].mean():.2%} PE={h_pe[ok].mean():.2%}")

    end = ts[ok].max()
    test_start = end - pd.Timedelta(days=TEST_DAYS)
    val_start = test_start - pd.Timedelta(days=VAL_DAYS)
    gap = pd.Timedelta(days=1)       # embargo so labels never straddle a split
    cache = {}

    def feats(w):
        if w not in cache:
            X = make_features(bars, days, bar_no, w)
            cache[w] = (X.values.astype(float), X.notna().all(axis=1).values & ok)
        return cache[w]

    def window(valid, a, b):
        return np.flatnonzero((ts >= a) & (ts < b) & valid)

    rows, cfgs = [], []
    for t in range(N_TRIALS):
        cfg = sample_cfg()
        X, valid = feats(cfg["windows"])
        tr = window(valid, val_start - pd.Timedelta(days=cfg["train_days"]), val_start - gap)
        va = window(valid, val_start, test_start - gap)
        if len(tr) < 1000 or len(va) == 0:
            continue
        model = make_model(cfg)
        model.fit(X[tr], y[tr])
        m = evaluate(model.predict_proba(X[va]), va, cfg["thr"], R)
        rows.append({**cfg, **m, "score": m["ev"] if m["n"] >= MIN_TRADES else -9})
        cfgs.append(cfg)
        print(f"trial {t+1}/{N_TRIALS}  n={m['n']}  hit={m['hit']:.2f}  ev={m['ev']:.4f}")

    if not rows:
        print("No trial had enough training rows. Option history is too short for these windows.")
        return
    res = pd.DataFrame(rows).sort_values("score", ascending=False)
    res.to_csv("trials.csv", index=False)
    print("\nTop 5 configs by VALIDATION:\n", res.head(5).to_string())
    if res["score"].max() <= -9:
        print("No config produced enough trades on validation. Stop here.")
        return

    best = cfgs[int(np.argmax([r["score"] for r in rows]))]
    X, valid = feats(best["windows"])
    tr = window(valid, test_start - pd.Timedelta(days=best["train_days"]), test_start - gap)
    te = window(valid, test_start, end + gap)
    model = make_model(best)
    model.fit(X[tr], y[tr])
    m = evaluate(model.predict_proba(X[te]), te, best["thr"], R)
    print("\nBest config:", best)
    if m["n"] == 0:
        print("FINAL TEST: the winning config produced no trades.")
        return
    print(f"FINAL TEST (last {TEST_DAYS} days, touched once): trades={m['n']}  hit rate={m['hit']:.2%}  "
          f"avg net return/trade={m['ev']:.2%}  profit factor={m['pf']:.2f}")
    print(f"Base rate of 'target hit' on ANY bar in test: CE={h_ce[te].mean():.2%} "
          f"PE={h_pe[te].mean():.2%} (your signals must beat this)")


if __name__ == "__main__":
    main()