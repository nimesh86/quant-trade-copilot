"""
Overnight Autonomous Discovery Engine for Intraday NIFTY Option Signals.
Designed for exhaustive overnight optimization across exact candle counts and unique market regimes.

Key Innovations:
  1. Exact Candle Windowing (fine step offsets around exact candle anchors).
  2. Custom / Regime-Segmented Datasets (Trend Expansion, Volatility Breakout, Prime Sessions, Expiry Gamma).
  3. Option Chain Feature Integration (ATM IV, IV Skew, Greeks).
  4. Ultra-Selective High-Conviction Probability Gating (wide probability margins).
  5. Real-time Leaderboard, Persistence (overnight_trials.csv, best_configs.csv), and Model Checkpointing.
"""
import os
import sys
import time
import json
import random
from pathlib import Path
from typing import Dict, Any, Tuple

import numpy as np
import pandas as pd
import lightgbm as lgb
from xgboost import XGBClassifier
from sqlalchemy import create_engine, text

# ---------------- CONFIG ----------------
SPOT_SYMBOL = "NIFTY-I"
INDEX_SYMBOL = "NIFTY 50"
OPTION_PREFIX = "NIFTY"
OPTION_SOURCE = "option_chain"
TZ = "Asia/Kolkata"
SEED = 42

DEFAULT_TARGET = 0.10
DEFAULT_STOP = 0.10
HORIZON = 12                  # 12 x 5 min = 60 min
COST = 0.01                   # 1% round-trip cost & slippage
MIN_PREMIUM = 15.0
MIN_TRADES = 25               # Minimum trades to qualify for leaderboard
TEST_DAYS = 180               # Holdout test set
VAL_DAYS = 120                # Out-of-sample validation ranking pool
FIRST_TRADE_BAR = 6           # First 30 min morning range
MIN_TRIALS = 5000             # Minimum trials guarantee
RUN_DURATION_HOURS = 8.0      # Run for the full 8-hour overnight window

BASE_COLS = {"open", "high", "low", "close", "volume"}
RESULTS_CSV = "overnight_trials.csv"
LEADERBOARD_CSV = "best_configs.csv"
MODELS_DIR = Path("saved_models")
MODELS_DIR.mkdir(exist_ok=True)

rng = random.Random(SEED)
np.random.seed(SEED)

# ---------------- DATABASE ----------------
from trading_engine.core.keyvault import get_secret
DB_HOST = os.getenv("DB_HOST", "localhost")
DB_PORT = int(os.getenv("DB_PORT", "5432"))
DB_NAME = os.getenv("DB_NAME", "trading")
DB_USER = os.getenv("DB_USER", "postgres")
DB_PASSWORD = get_secret("DB_PASSWORD", "postgres")


def get_engine():
    return create_engine(f"postgresql+psycopg2://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}")


def to_ist(s):
    return pd.to_datetime(s, utc=True).dt.tz_convert(TZ)


# ---------------- DATA LOADERS ----------------
def load_bars(eng):
    """Loads 1-min candles -> 5-min bars labeled with bar end time."""
    c = pd.read_sql(text("""
        SELECT timestamp, open, high, low, close, volume
        FROM minute_candles
        WHERE symbol = :s ORDER BY timestamp ASC
    """), eng, params={"s": SPOT_SYMBOL})
    assert len(c), f"No rows for {SPOT_SYMBOL}"
    c["timestamp"] = to_ist(c["timestamp"])
    c = c.drop_duplicates("timestamp").set_index("timestamp")
    c = c.between_time("09:15", "15:29")
    b = c.resample("5min", closed="left", label="right").agg({
        "open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"
    }).dropna(subset=["close"])
    return b


def load_option_chain_data(eng, bars):
    """Loads option_chain with IV, Greeks, and strikes aligned to bars."""
    q = text("""
        SELECT timestamp, symbol, expiry, strike, option_type, relative_strike, ltp, iv, delta, theta, vega
        FROM option_chain
        WHERE underlying = :u
          AND relative_strike BETWEEN -4 AND 4
          AND ltp > 0
    """)
    o = pd.read_sql(q, eng, params={"u": OPTION_PREFIX})
    assert len(o), "option_chain has no rows; run build_option_chain.py"
    o["timestamp"] = to_ist(o["timestamp"])
    o["expiry"] = pd.to_datetime(o["expiry"])
    # Nearest expiry per bar
    o = o[o["expiry"] == o.groupby("timestamp")["expiry"].transform("min")]
    pos = pd.Series(np.arange(len(bars)), index=bars.index)
    o["bar"] = pos.reindex(o["timestamp"]).values
    o = o.dropna(subset=["bar"])
    o["bar"] = o["bar"].astype(int)
    return o


# ---------------- OUTCOMES EVALUATION ----------------
def score_path(p, i, end, target, stop):
    j = min(i + HORIZON, end)
    if j <= i or not (p[i] >= MIN_PREMIUM):
        return np.nan, False
    path = p[i + 1:j + 1] / p[i] - 1.0
    t = np.flatnonzero(path >= target)
    s = np.flatnonzero(path <= -stop)
    t0 = t[0] if len(t) else 10 ** 9
    s0 = s[0] if len(s) else 10 ** 9
    if t0 < s0:
        return target, True
    if s0 < 10 ** 9:
        return -stop, False
    return path[-1], False


def contract_outcomes(entry_syms, opt, n, day_end, target=DEFAULT_TARGET, stop=DEFAULT_STOP):
    ret, hit = np.full(n, np.nan), np.zeros(n, bool)
    by_sym = {s: g for s, g in opt.groupby("symbol")}
    for s, ii in pd.Series(np.arange(n)).groupby(entry_syms):
        if s not in by_sym:
            continue
        g = by_sym[s]
        dense = np.full(n, np.nan)
        dense[g["bar"].values] = g["ltp"].values
        dense = pd.Series(dense).ffill().values
        for i in ii.values:
            ret[i], hit[i] = score_path(dense, i, day_end[i], target, stop)
    return ret, hit


def entry_symbols(opt, n, side):
    atm = opt[(opt["relative_strike"] == 0) & (opt["option_type"] == side)]
    atm = atm.drop_duplicates("bar").set_index("bar")["symbol"]
    return atm.reindex(range(n)).values


def day_info(df):
    days = df.index.date
    pos = pd.Series(np.arange(len(df)))
    return days, pos.groupby(days).transform("max").values, pos.groupby(days).cumcount().values


# ---------------- FEATURE ENGINEERING ----------------
def make_features(df, days, bar_no, windows, opt_features=None):
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
    f["rsi"] = 100.0 - 100.0 / (1.0 + up / (dn + 1e-9))
    f["rsi_chg"] = f["rsi"].diff(3)

    tr = pd.concat([df["high"] - df["low"], (df["high"] - c.shift()).abs(),
                    (df["low"] - c.shift()).abs()], axis=1).max(axis=1)
    f["atr_pct"] = tr.rolling(14).mean() / c

    # Safe VWAP & Opening Range
    pv = (c * df["volume"]).groupby(days).cumsum()
    v = df["volume"].groupby(days).cumsum()
    f["vwap_dist"] = np.where(v > 0, c / (pv / np.maximum(v, 1e-9)) - 1.0, 0.0)

    g = df.groupby(days)
    or_hi = g["high"].transform(lambda s: s.iloc[:FIRST_TRADE_BAR].max())
    or_lo = g["low"].transform(lambda s: s.iloc[:FIRST_TRADE_BAR].min())
    f["or_pos"] = np.where(or_hi > or_lo, (c - or_lo) / np.maximum(or_hi - or_lo, 1e-9), 0.5)
    f.loc[bar_no < FIRST_TRADE_BAR, "or_pos"] = np.nan
    f["bar_no"] = bar_no

    # Option Chain Enhanced Features
    if opt_features is not None:
        for k, s in opt_features.items():
            f[k] = s

    # Auxiliary columns
    for col in df.columns:
        if col not in BASE_COLS:
            if df[col].notna().mean() > 0.5:
                f[col] = df[col]

    return f.replace([np.inf, -np.inf], np.nan)


# ---------------- UNIQUE REGIME MASKS ----------------
def get_regime_mask(bars, regime_mode):
    """Generates unique custom training subsets based on market regimes."""
    c = bars["close"]
    n = len(bars)
    if regime_mode == "all_market":
        return np.ones(n, dtype=bool)

    if regime_mode == "trend_expansion":
        # Strong directional momentum
        ema20, ema50 = c.ewm(span=20).mean(), c.ewm(span=50).mean()
        spread = ((ema20 - ema50) / c).abs()
        return (spread > spread.median()).values

    if regime_mode == "volatility_breakout":
        # High volatility expansion
        vol = c.pct_change().rolling(12).std()
        return (vol > vol.median()).values

    if regime_mode == "session_prime":
        # Morning & afternoon active momentum sessions (exclude midday chop)
        t = bars.index.time
        is_morning = (t >= pd.to_datetime("09:30").time()) & (t <= pd.to_datetime("11:30").time())
        is_afternoon = (t >= pd.to_datetime("13:30").time()) & (t <= pd.to_datetime("15:00").time())
        return (is_morning | is_afternoon)

    if regime_mode == "expiry_gamma":
        # High gamma days near expiry
        if "dte" in bars.columns:
            return (bars["dte"] <= 1.0).values
        return np.ones(n, dtype=bool)

    return np.ones(n, dtype=bool)


# ---------------- HYPERPARAMETER & CANDLE SAMPLING ----------------
CANDLE_ANCHORS = [1500, 3000, 5000, 7500, 10000, 12500, 15000, 17500, 20000, 25000, 30000, 35000, 40000, 45000, 50000, 55000]
CANDLE_OFFSETS = [-250, -100, -50, -25, -10, -5, -2, -1, 0, 1, 2, 5, 10, 25, 50, 100, 250]


def sample_cfg():
    anchor = rng.choice(CANDLE_ANCHORS)
    offset = rng.choice(CANDLE_OFFSETS)
    n_candles = max(1200, anchor + offset)

    return dict(
        n_candles=n_candles,
        regime_mode=rng.choice(["all_market", "trend_expansion", "volatility_breakout", "session_prime", "expiry_gamma"]),
        windows=rng.choice([(2, 5, 15), (3, 6, 12), (5, 10, 20), (5, 20, 60), (10, 30, 60), (3, 12, 36)]),
        kind=rng.choice(["lgbm", "xgb"]),
        n=rng.choice([150, 250, 350, 500]),
        lr=rng.choice([0.015, 0.03, 0.05, 0.08]),
        leaves=rng.choice([7, 15, 31, 63]),
        depth=rng.choice([3, 4, 5, 6]),
        mcs=rng.choice([30, 50, 100, 200]),
        thr=rng.choice([0.45, 0.50, 0.55, 0.60, 0.65, 0.70, 0.75]),
        margin=rng.choice([0.05, 0.10, 0.15, 0.20, 0.25]),
        target=rng.choice([0.10, 0.12, 0.15]),
        stop=rng.choice([0.08, 0.10])
    )


def make_model(cfg):
    if cfg["kind"] == "lgbm":
        return lgb.LGBMClassifier(
            objective="multiclass", num_class=3, n_estimators=cfg["n"],
            learning_rate=cfg["lr"], num_leaves=cfg["leaves"],
            min_child_samples=cfg["mcs"], subsample=0.8, subsample_freq=1,
            colsample_bytree=0.8, random_state=SEED, verbose=-1, n_jobs=-1
        )
    return XGBClassifier(
        objective="multi:softprob", n_estimators=cfg["n"], learning_rate=cfg["lr"],
        max_depth=cfg["depth"], min_child_weight=cfg["mcs"] / 20.0, subsample=0.8,
        colsample_bytree=0.8, random_state=SEED, n_jobs=-1
    )


# ---------------- EVALUATION WITH PROBABILITY MARGIN ----------------
def evaluate(P, idx, thr, margin, R):
    p_ce, p_pe = P[:, 1], P[:, 2]
    # High-conviction entry with probability margin between Call and Put
    sig = np.where((p_ce >= thr) & (p_ce - p_pe >= margin), 1,
                   np.where((p_pe >= thr) & (p_pe - p_ce >= margin), 2, 0))

    rets, hits, blocked = [], [], -1
    for k in np.flatnonzero(sig):
        i = idx[k]
        if i <= blocked:
            continue
        blocked = i + HORIZON
        side = "ce" if sig[k] == 1 else "pe"
        rets.append(R["r_" + side][i] - COST)
        hits.append(R["h_" + side][i])

    if not rets:
        return dict(n=0, hit=0.0, ev=-1.0, pf=0.0)

    r, h = np.array(rets), np.array(hits)
    gl = -r[r < 0].sum()
    pf = (r[r > 0].sum() / gl) if gl > 0 else (99.0 if r.sum() > 0 else 0.0)
    return dict(n=len(r), hit=float(h.mean()), ev=float(r.mean()), pf=float(pf))


# ---------------- MAIN DISCOVERY ENGINE ----------------
def main():
    print("=" * 80)
    print("🚀 LAUNCHING OVERNIGHT AUTONOMOUS OPTION SIGNAL DISCOVERY ENGINE")
    print(f"Goal: 5,000 Exhaustive Trials across Candle Sizes, Regimes & High Accuracy")
    print("=" * 80)

    eng = get_engine()
    bars = load_bars(eng)
    n = len(bars)
    opt = load_option_chain_data(eng, bars)
    days, day_end, bar_no = day_info(bars)

    # Expiry metadata
    exp = pd.to_datetime(opt.groupby("bar")["expiry"].first()).reindex(range(n))
    bar_dates = pd.DatetimeIndex(pd.to_datetime(bars.index.date)).values
    dte = (exp.values - bar_dates) / np.timedelta64(1, "D")
    bars["dte"] = dte
    bars["is_expiry"] = (dte == 0).astype(float)

    # Extract ATM IV and Skew features from option_chain
    atm_ce = opt[(opt["relative_strike"] == 0) & (opt["option_type"] == "CE")].drop_duplicates("bar").set_index("bar")
    atm_pe = opt[(opt["relative_strike"] == 0) & (opt["option_type"] == "PE")].drop_duplicates("bar").set_index("bar")
    opt_feats = {
        "atm_iv": atm_ce["iv"].reindex(range(n)).fillna(16.5).values,
        "iv_skew": (atm_pe["iv"].reindex(range(n)) - atm_ce["iv"].reindex(range(n))).fillna(0.0).values,
        "atm_delta_skew": (atm_ce["delta"].abs().reindex(range(n)) - atm_pe["delta"].abs().reindex(range(n))).fillna(0.0).values
    }

    # Outcomes cache by target/stop combination
    outcomes_cache = {}

    def get_outcomes(target, stop):
        key = (target, stop)
        if key not in outcomes_cache:
            r_ce, h_ce = contract_outcomes(entry_symbols(opt, n, "CE"), opt, n, day_end, target, stop)
            r_pe, h_pe = contract_outcomes(entry_symbols(opt, n, "PE"), opt, n, day_end, target, stop)
            R = dict(r_ce=r_ce, h_ce=h_ce, r_pe=r_pe, h_pe=h_pe)
            y = np.where(h_ce & ~h_pe, 1, np.where(h_pe & ~h_ce, 2, 0))
            ok = ~np.isnan(r_ce) & ~np.isnan(r_pe) & (bar_no >= FIRST_TRADE_BAR)
            outcomes_cache[key] = (R, y, ok)
        return outcomes_cache[key]

    # Pre-populate base outcomes
    R_base, y_base, ok_base = get_outcomes(DEFAULT_TARGET, DEFAULT_STOP)

    ts = bars.index
    end = ts[ok_base].max()
    test_start = end - pd.Timedelta(days=TEST_DAYS)
    val_start = test_start - pd.Timedelta(days=VAL_DAYS)
    gap = pd.Timedelta(days=1)

    print(f"Data audit: {n:,} 5-min bars from {ts[ok_base].min():%Y-%m-%d} to {end:%Y-%m-%d}")
    print(f"Split Windows: [Train (by candle count) | Validation: {val_start:%Y-%m-%d} | Holdout Test: {test_start:%Y-%m-%d}]")
    print("-" * 80)

    # Feature matrix cache
    feature_cache = {}

    def get_features(windows):
        if windows not in feature_cache:
            feature_cache[windows] = make_features(bars, days, bar_no, windows, opt_feats)
        return feature_cache[windows]

    # Load previous trials if available
    results = []
    if os.path.exists(RESULTS_CSV):
        try:
            df_prev = pd.read_csv(RESULTS_CSV)
            if not df_prev.empty and "trial" in df_prev.columns:
                results = df_prev.to_dict("records")
                print(f"Loaded {len(results)} existing trials from {RESULTS_CSV}. Resuming seamlessly...", flush=True)
        except Exception:
            pass

    start_trial = max([int(r.get("trial", 0)) for r in results], default=0) + 1
    start_time = time.time()
    trial_idx = start_trial

    print(f"Starting overnight session at trial {trial_idx}. Running for 8 hours (minimum 5,000 trials)...", flush=True)

    while True:
        elapsed_sec = time.time() - start_time
        elapsed_hours = elapsed_sec / 3600.0

        # Terminate ONLY after BOTH minimum trials AND 8 hours have elapsed
        if trial_idx > MIN_TRIALS and elapsed_hours >= RUN_DURATION_HOURS:
            print(f"\nCompleted 8-hour window ({elapsed_hours:.2f} hrs) and {trial_idx - 1} trials!", flush=True)
            break

        cfg = sample_cfg()
        R, y, ok = get_outcomes(cfg["target"], cfg["stop"])
        X = get_features(cfg["windows"])

        regime_mask = get_regime_mask(bars, cfg["regime_mode"])
        valid = X.notna().all(axis=1).values & ok & regime_mask

        # EXACT CANDLE WINDOWING
        val_idx = np.flatnonzero((ts >= val_start) & (ts < test_start - gap) & valid)
        if len(val_idx) < 10:
            trial_idx += 1
            continue

        # Slice exact N candles immediately preceding validation
        pre_val_valid = np.flatnonzero((ts < val_start - gap) & valid)
        if len(pre_val_valid) < 500:
            trial_idx += 1
            continue
        tr_idx = pre_val_valid[-cfg["n_candles"]:] if len(pre_val_valid) > cfg["n_candles"] else pre_val_valid

        try:
            model = make_model(cfg)
            model.fit(X.values[tr_idx], y[tr_idx])
            m = evaluate(model.predict_proba(X.values[val_idx]), val_idx, cfg["thr"], cfg["margin"], R)
        except Exception as e:
            trial_idx += 1
            continue

        score = (m["hit"] * 100.0) + (m["ev"] * 1000.0) if m["n"] >= MIN_TRADES else -99.0
        record = {
            "trial": trial_idx,
            "candles": cfg["n_candles"],
            "regime": cfg["regime_mode"],
            "model": cfg["kind"],
            "thr": cfg["thr"],
            "margin": cfg["margin"],
            "target": cfg["target"],
            "stop": cfg["stop"],
            "trades": m["n"],
            "hit_rate": round(m["hit"], 4),
            "ev": round(m["ev"], 4),
            "pf": round(m["pf"], 2),
            "score": round(score, 2),
            "cfg": json.dumps(cfg)
        }
        results.append(record)

        # Real-time feedback
        if m["hit"] >= 0.60 and m["n"] >= MIN_TRADES:
            tag = "🔥 HIGH WIN RATE"
        elif m["hit"] >= 0.52 and m["n"] >= MIN_TRADES:
            tag = "✅ PROFITABLE"
        else:
            tag = "  "

        if trial_idx % 10 == 0 or "🔥" in tag:
            print(f"[{trial_idx:5d}] ({elapsed_hours:4.2f}h) | {cfg['regime_mode'][:12]:12s} | {cfg['n_candles']:5d}c | thr={cfg['thr']:.2f} mrg={cfg['margin']:.2f} | trd={m['n']:3d} | hit={m['hit']*100:5.1f}% | EV={m['ev']*100:+5.2f}% | PF={m['pf']:5.2f} {tag}", flush=True)

        # Save checkpoint periodically every 25 trials
        if trial_idx % 25 == 0:
            df_res = pd.DataFrame(results).sort_values("hit_rate", ascending=False)
            df_res.to_csv(RESULTS_CSV, index=False)
            leaderboard = df_res[df_res["trades"] >= MIN_TRADES].head(35)
            leaderboard.to_csv(LEADERBOARD_CSV, index=False)

        trial_idx += 1

    print("\n" + "=" * 80)
    print("OVERNIGHT EXPLORATION COMPLETE! EVALUATING TOP CONFIG ON TEST SET...")
    print("=" * 80)

    df_res = pd.DataFrame(results).sort_values("hit_rate", ascending=False)
    df_res.to_csv(RESULTS_CSV, index=False)
    leaderboard = df_res[df_res["trades"] >= MIN_TRADES]

    if leaderboard.empty:
        print("No configurations met minimum trade threshold.")
        return

    best_record = leaderboard.iloc[0]
    best_cfg = json.loads(best_record["cfg"])
    best_cfg["windows"] = tuple(best_cfg["windows"])
    print(f"\n🏆 WINNING CONFIGURATION (Validation Hit Rate: {best_record['hit_rate']*100:.2f}%):")
    print(json.dumps(best_cfg, indent=2))

    # Evaluate Winner on 180-Day Holdout Test Set
    R_best, y_best, ok_best = get_outcomes(best_cfg["target"], best_cfg["stop"])
    X_best = get_features(best_cfg["windows"])
    regime_mask_best = get_regime_mask(bars, best_cfg["regime_mode"])
    valid_best = X_best.notna().all(axis=1).values & ok_best & regime_mask_best

    pre_test_valid = np.flatnonzero((ts < test_start - gap) & valid_best)
    tr_test = pre_test_valid[-best_cfg["n_candles"]:] if len(pre_test_valid) > best_cfg["n_candles"] else pre_test_valid
    te_idx = np.flatnonzero((ts >= test_start) & (ts <= end) & valid_best)

    final_model = make_model(best_cfg)
    final_model.fit(X_best.values[tr_test], y_best[tr_test])
    m_test = evaluate(final_model.predict_proba(X_best.values[te_idx]), te_idx, best_cfg["thr"], best_cfg["margin"], R_best)

    print("\n" + "=" * 80)
    print(f"FINAL HOLDOUT TEST REPORT (Last 180 Days, Touched Exactly Once):")
    print(f"  • Trades Taken  : {m_test['n']}")
    print(f"  • Hit Rate      : {m_test['hit']*100:.2f}%")
    print(f"  • Net Return/Tr : {m_test['ev']*100:+.2f}%")
    print(f"  • Profit Factor : {m_test['pf']:.2f}")
    print("=" * 80)


if __name__ == "__main__":
    main()
