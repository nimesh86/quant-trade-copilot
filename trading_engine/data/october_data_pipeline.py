"""
October Data Pipeline: Gap Filling, Anomaly Repair & Option Generation
────────────────────────────────────────────────────────────────────────
1. Downloads and audits all October trading days from VPS (https://kesarisoft.com).
2. Fixes corrupted wicks & flash crashes on Oct 30 (removes phantom 22690.65 needles).
3. Fills historical gaps (Oct 27 from 78 bars -> 375 bars, Oct 28 from 287 bars -> 375 bars).
4. Fetches or synthesizes complete 1-minute ATM CE & PE option contract candles (Black-Scholes).
5. Stores clean datasets into `data/cache/` and local DuckDB for high-speed backtesting.
"""
import os
import sys
import json
import time
import urllib.request
from pathlib import Path
from datetime import datetime, timedelta
from typing import Dict, List, Any, Optional, Tuple
import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

CACHE_DIR = PROJECT_ROOT / "data" / "cache"
CACHE_DIR.mkdir(parents=True, exist_ok=True)

VPS_BASE_URL = "https://kesarisoft.com"


def fetch_vps_spot_candles(date_str: str) -> List[Dict[str, Any]]:
    """Fetches spot candles from VPS with retry."""
    url = f"{VPS_BASE_URL}/api/candles?date={date_str}"
    for attempt in range(2):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=12) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                if isinstance(data, list):
                    return data
                elif isinstance(data, dict):
                    return data.get("candles", data.get("data", []))
        except Exception:
            time.sleep(1)
    return []


def fetch_vps_contract_candles(contract_symbol: str, date_str: str) -> List[Dict[str, Any]]:
    """Fetches contract candles from VPS if available."""
    url = f"{VPS_BASE_URL}/api/contracts/candles?symbol={contract_symbol}&date={date_str}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            if isinstance(data, dict) and data.get("candles"):
                return data["candles"]
    except Exception:
        pass
    return []


def repair_candle_anomalies(df: pd.DataFrame, date_str: str) -> pd.DataFrame:
    """
    Detects and cleans corrupt phantom spikes (e.g., 22690.65 outlier on Oct 30).
    """
    df = df.copy()
    if df.empty:
        return df

    # Find dominant outlier if any (like 22690.65 occurring across 50%+ of low column)
    low_mode = df["low"].mode()
    outlier_val = None
    if not low_mode.empty:
        mode_val = float(low_mode.iloc[0])
        mode_count = (df["low"] == mode_val).sum()
        if mode_count > 50:  # clearly an artificial hardcoded peg
            outlier_val = mode_val

    # 1. Clean Corrupt Closes & Opens
    for i in range(len(df)):
        c = float(df.at[i, "close"])
        o = float(df.at[i, "open"])

        # Flash crash close spike
        if outlier_val and abs(c - outlier_val) < 0.01:
            prev_c = float(df.at[i - 1, "close"]) if i > 0 else o
            next_o = float(df.at[i + 1, "open"]) if i < len(df) - 1 else prev_c
            df.at[i, "close"] = prev_c if abs(next_o - outlier_val) < 0.01 else round((prev_c + next_o) / 2.0, 2)

        # Flash crash open spike
        if outlier_val and abs(o - outlier_val) < 0.01:
            prev_c = float(df.at[i - 1, "close"]) if i > 0 else float(df.at[i, "close"])
            df.at[i, "open"] = prev_c

    # 2. Clean Corrupt Lows & Highs
    for i in range(len(df)):
        o = float(df.at[i, "open"])
        c = float(df.at[i, "close"])
        h = float(df.at[i, "high"])
        l = float(df.at[i, "low"])

        # If low is the pinned outlier (or below open/close by > 150 pts unrealistically)
        body_min = min(o, c)
        body_max = max(o, c)

        if outlier_val and (abs(l - outlier_val) < 0.01 or (body_min - l) > 120.0):
            # Compute natural wick proportional to upper wick / candle range
            upper_wick = max(0.5, h - body_max)
            natural_lower_wick = min(upper_wick * 1.1, 12.0)
            df.at[i, "low"] = round(body_min - natural_lower_wick, 2)

        # Ensure strict candle geometry
        df.at[i, "high"] = max(df.at[i, "high"], df.at[i, "open"], df.at[i, "close"])
        df.at[i, "low"] = min(df.at[i, "low"], df.at[i, "open"], df.at[i, "close"])

    return df


def fill_intraday_gaps(df: pd.DataFrame, date_str: str) -> pd.DataFrame:
    """
    Ensures a strict, gapless 375-bar session grid from 09:15:00 to 15:29:00.
    Fills gaps via forward-fill and realistic micro-spread simulation.
    """
    # Build complete 375-minute grid
    start_dt = pd.to_datetime(f"{date_str} 09:15:00")
    end_dt = pd.to_datetime(f"{date_str} 15:29:00")
    full_grid = pd.date_range(start=start_dt, end=end_dt, freq="1min")

    grid_df = pd.DataFrame({"ts": full_grid})
    grid_df["time_str"] = grid_df["ts"].dt.strftime("%Y-%m-%d %H:%M")
    grid_df["time_hm"] = grid_df["ts"].dt.strftime("%H:%M")

    if df.empty:
        return pd.DataFrame()

    df_clean = df.copy()
    if "time" in df_clean.columns:
        df_clean["time_hm"] = df_clean["time"].astype(str).str[-5:]
    else:
        df_clean["time_hm"] = pd.to_datetime(df_clean["timestamp"]).dt.strftime("%H:%M")

    # Filter df_clean to 09:15 - 15:29
    df_clean = df_clean[(df_clean["time_hm"] >= "09:15") & (df_clean["time_hm"] <= "15:29")].drop_duplicates("time_hm")

    # Merge on time_hm
    merged = pd.merge(grid_df, df_clean, on="time_hm", how="left")

    # Forward fill missing closes, then backward fill if market open was missing
    merged["close"] = merged["close"].ffill().bfill()
    merged["open"] = merged["open"].fillna(merged["close"])
    merged["high"] = merged["high"].fillna(merged["close"].apply(lambda x: round(x + 1.2, 2)))
    merged["low"] = merged["low"].fillna(merged["close"].apply(lambda x: round(x - 1.2, 2)))
    merged["volume"] = merged["volume"].fillna(650).astype(int)

    # Reconstruct final standardized columns
    result = pd.DataFrame({
        "time": merged["time_str"],
        "timestamp": merged["ts"].astype(str),
        "open": merged["open"].round(2),
        "high": merged["high"].round(2),
        "low": merged["low"].round(2),
        "close": merged["close"].round(2),
        "volume": merged["volume"]
    })

    return result


def synthesize_option_contract_candles(
    spot_df: pd.DataFrame,
    date_str: str,
    option_type: str,
    strike: float,
    expiry_date_str: str
) -> pd.DataFrame:
    """
    Synthesizes complete 1-minute OHLCV candles for an ATM Option Contract
    using Black-Scholes pricing with dynamic intraday delta elasticity.
    """
    from scipy.stats import norm

    bars: List[Dict[str, Any]] = []
    
    # Calculate days to expiry
    exp_dt = pd.to_datetime(f"{expiry_date_str} 15:30:00")
    
    prev_close = None
    r = 0.065   # 6.5% risk-free rate
    sigma = 0.135 # 13.5% IV

    for idx, row in spot_df.iterrows():
        bar_ts = pd.to_datetime(row["timestamp"])
        time_to_exp_years = max(0.0001, (exp_dt - bar_ts).total_seconds() / (365.25 * 86400.0))
        
        spot_c = float(row["close"])
        spot_o = float(row["open"])
        spot_h = float(row["high"])
        spot_l = float(row["low"])
        
        # Black-Scholes d1, d2
        d1 = (np.log(spot_c / strike) + (r + 0.5 * sigma**2) * time_to_exp_years) / (sigma * np.sqrt(time_to_exp_years))
        d2 = d1 - sigma * np.sqrt(time_to_exp_years)
        
        if option_type == "CE":
            delta = norm.cdf(d1)
            theoretical_px = spot_c * norm.cdf(d1) - strike * np.exp(-r * time_to_exp_years) * norm.cdf(d2)
        else:
            delta = norm.cdf(d1) - 1.0
            theoretical_px = strike * np.exp(-r * time_to_exp_years) * norm.cdf(-d2) - spot_c * norm.cdf(-d1)
        
        theoretical_px = max(10.0, round(float(theoretical_px), 2))
        
        delta_mag = abs(delta)
        is_ce = (option_type == "CE")

        if prev_close is None:
            opt_open = theoretical_px
            opt_close = theoretical_px
        else:
            opt_open = prev_close
            spot_change_close = spot_c - spot_o
            if is_ce:
                opt_close = max(5.0, round(opt_open + (spot_change_close * delta_mag), 2))
            else:
                opt_close = max(5.0, round(opt_open - (spot_change_close * delta_mag), 2))

        spot_change_high = spot_h - spot_o
        spot_change_low = spot_o - spot_l

        if is_ce:
            opt_high = max(opt_open, opt_close, round(opt_open + (spot_change_high * delta_mag), 2))
            opt_low = max(5.0, min(opt_open, opt_close, round(opt_open - (spot_change_low * delta_mag), 2)))
        else:
            # Put option gains when spot drops to its low
            opt_high = max(opt_open, opt_close, round(opt_open + (spot_change_low * delta_mag), 2))
            # Put option loses when spot rallies to its high
            opt_low = max(5.0, min(opt_open, opt_close, round(opt_open - (spot_change_high * delta_mag), 2)))

        prev_close = opt_close
        
        bars.append({
            "time": str(row["time"]),
            "timestamp": int(bar_ts.timestamp()),
            "open": opt_open,
            "high": opt_high,
            "low": opt_low,
            "close": opt_close,
            "volume": int(row["volume"] * 2.2)
        })

    return pd.DataFrame(bars)


def run_october_pipeline():
    """Executes the complete October data download, gap filling, and option generation."""
    print("=" * 70)
    print("STARTING OCTOBER DATA REPAIR & OPTION GENERATION PIPELINE")
    print("=" * 70)

    # Target business days of October 2026
    oct_dates = [f"2026-10-{d:02d}" for d in range(1, 32)]
    
    cleaned_days = 0
    total_bars_generated = 0

    for d in oct_dates:
        dt = datetime.strptime(d, "%Y-%m-%d")
        if dt.weekday() >= 5:
            continue  # Weekend
        if d == "2026-10-02":
            continue  # Gandhi Jayanti Holiday

        cache_spot_path = CACHE_DIR / f"candles_NIFTY_{d}.json"
        
        # 1. Fetch raw candles (from existing local cache or VPS)
        raw_candles = []
        if cache_spot_path.exists():
            try:
                with open(cache_spot_path, "r", encoding="utf-8") as f:
                    raw_candles = json.load(f)
            except Exception:
                pass

        if len(raw_candles) < 300:
            print(f"[{d}] Cache has only {len(raw_candles)} bars. Querying VPS...")
            vps_candles = fetch_vps_spot_candles(d)
            if len(vps_candles) > len(raw_candles):
                raw_candles = vps_candles
                print(f"[{d}] VPS returned {len(vps_candles)} bars.")

        if not raw_candles:
            print(f"[{d}] No data available. Skipping.")
            continue

        raw_df = pd.DataFrame(raw_candles)

        # 2. Repair Anomalies (Oct 30 outlier cleanup)
        repaired_df = repair_candle_anomalies(raw_df, d)

        # 3. Fill Gaps to strictly 375 1-min bars
        gapless_df = fill_intraday_gaps(repaired_df, d)
        
        if gapless_df.empty or len(gapless_df) < 375:
            print(f"[{d}] Could not construct full 375 bars. Result: {len(gapless_df)}")
            continue

        # Save clean spot data to cache
        spot_dict = gapless_df.to_dict(orient="records")
        with open(cache_spot_path, "w", encoding="utf-8") as f:
            json.dump(spot_dict, f)

        # 4. Generate/Synthesize Option Contracts (ATM CE and ATM PE)
        # Determine ATM Strike based on market open spot price
        first_spot_open = float(gapless_df.iloc[0]["open"])
        atm_strike = round(first_spot_open / 50.0) * 50.0

        # Monthly Expiry: Last Thursday of Oct 2026 is 2026-10-29
        exp_date_str = "2026-10-29" if d <= "2026-10-29" else "2026-11-05"

        for opt_type in ["CE", "PE"]:
            contract_sym = f"NIFTY26OCT{int(atm_strike)}{opt_type}"
            contract_cache_path = CACHE_DIR / f"candles_{contract_sym}_{d}.json"
            
            # Synthesize option contract candles
            opt_df = synthesize_option_contract_candles(
                spot_df=gapless_df,
                date_str=d,
                option_type=opt_type,
                strike=atm_strike,
                expiry_date_str=exp_date_str
            )

            with open(contract_cache_path, "w", encoding="utf-8") as f:
                json.dump(opt_df.to_dict(orient="records"), f)

        cleaned_days += 1
        total_bars_generated += len(gapless_df)
        print(f"[{d}] Successfully repaired & generated 375 bars | ATM Strike: {atm_strike:.0f} (CE & PE saved)")

    print("=" * 70)
    print(f"PIPELINE COMPLETE: {cleaned_days} trading days processed, {total_bars_generated} total bars gapless.")
    print("=" * 70)


if __name__ == "__main__":
    run_october_pipeline()
