#!/usr/bin/env python3
"""
NIFTY 50 AMD + Volume/TPO Profile Strategy Scanner
===================================================
Detects and lists historical days matching the institutional:
  Accumulation -> Volume/TPO Profile -> Manipulation -> Distribution
framework on NIFTY 50 1-minute historical data in PostgreSQL.
"""

import argparse
import os
import sys
from datetime import datetime, time, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
from dotenv import load_dotenv
from sqlalchemy import create_engine

# Load environment configuration
load_dotenv()
DEFAULT_DB_URL = os.getenv(
    "DATABASE_URL",
    os.getenv("DB_URL", "postgresql+psycopg2://postgres:postgres@localhost:5432/trading")
)


def compute_tpo_profile(sub_df: pd.DataFrame, bin_size: float = 5.0):
    """
    Computes Time-Price Opportunity (TPO / Market Profile) for the accumulation window.
    Determines Point of Control (POC), Value Area High (VAH), and Value Area Low (VAL).
    """
    p_min = np.floor(sub_df["low"].min() / bin_size) * bin_size
    p_max = np.ceil(sub_df["high"].max() / bin_size) * bin_size
    bins = np.arange(p_min, p_max + bin_size, bin_size)
    counts = np.zeros(len(bins))

    for _, row in sub_df.iterrows():
        b_idx = np.where((bins >= row["low"]) & (bins <= row["high"]))[0]
        counts[b_idx] += 1

    poc_idx = int(np.argmax(counts))
    poc = float(bins[poc_idx])

    # 70% Value Area calculation
    total_tpo = np.sum(counts)
    if total_tpo == 0:
        return float(sub_df["low"].min()), float((sub_df["low"].min() + sub_df["high"].max()) / 2), float(sub_df["high"].max())

    target_tpo = 0.70 * total_tpo
    current_tpo = counts[poc_idx]
    va_indices = {poc_idx}
    up_idx = poc_idx + 1
    down_idx = poc_idx - 1

    while current_tpo < target_tpo:
        count_up = counts[up_idx] if up_idx < len(bins) else -1
        count_down = counts[down_idx] if down_idx >= 0 else -1

        if count_up == -1 and count_down == -1:
            break

        if count_up >= count_down:
            current_tpo += count_up
            va_indices.add(up_idx)
            up_idx += 1
        else:
            current_tpo += count_down
            va_indices.add(down_idx)
            down_idx -= 1

    val = float(bins[min(va_indices)])
    vah = float(bins[max(va_indices)])
    return val, poc, vah


def scan_amd_profile_setups(
    db_url: str,
    start_date: str = "2026-04-01",
    end_date: str = "2026-10-15",
    acc_end_str: str = "10:15",
    min_sweep_pts: float = 8.0,
    max_reclaim_candles: int = 15,
    min_rr_target: float = 1.5,
    bin_size: float = 5.0,
):
    print("=" * 80)
    print(" NIFTY 50 ACCUMULATION -> VOLUME PROFILE -> MANIPULATION -> DISTRIBUTION")
    print("=" * 80)
    print(f"Database URL           : {db_url.split('@')[-1] if '@' in db_url else db_url}")
    print(f"Date Range             : {start_date} to {end_date}")
    print(f"Accumulation Window    : 09:15 to {acc_end_str} IST")
    print(f"Min Manipulation Sweep : {min_sweep_pts} pts beyond VAH/VAL")
    print(f"Max Reclaim Tolerance  : {max_reclaim_candles} mins")
    print(f"Min Distribution Target: {min_rr_target}R / Opposite Value Area")
    print("=" * 80)

    eng = create_engine(db_url)
    query = f"""
        SELECT timestamp, open, high, low, close, volume 
        FROM minute_candles 
        WHERE symbol = 'NIFTY 50' 
          AND timestamp >= '{start_date}'
          AND timestamp <= '{end_date} 23:59:59'
        ORDER BY timestamp ASC
    """

    print("Fetching NIFTY 50 minute candles from database...")
    df = pd.read_sql(query, eng)
    if df.empty:
        print("[!] No candles found for NIFTY 50 in specified date range.")
        return []

    df["timestamp"] = pd.to_datetime(df["timestamp"])
    df["ist_time"] = df["timestamp"].dt.tz_convert("Asia/Kolkata")
    df["date"] = df["ist_time"].dt.date
    df["time"] = df["ist_time"].dt.time

    total_days = df["date"].nunique()
    print(f"Loaded {len(df):,} candles across {total_days} trading days.\nAnalyzing sessions...")

    acc_end_h, acc_end_m = map(int, acc_end_str.split(":"))
    acc_end_time = time(acc_end_h, acc_end_m)

    results = []

    for d, day_df in df.groupby("date"):
        day_df = day_df.sort_values("ist_time").reset_index(drop=True)

        # 1. ACCUMULATION PHASE
        acc_df = day_df[(day_df["time"] >= time(9, 15)) & (day_df["time"] <= acc_end_time)]
        if len(acc_df) < 40:
            continue

        acc_high = float(acc_df["high"].max())
        acc_low = float(acc_df["low"].min())
        acc_range = acc_high - acc_low

        # Filter out runaway trend openings (> 200 pts) or flat invalid ranges (< 20 pts)
        if acc_range < 20.0 or acc_range > 220.0:
            continue

        # 2. VOLUME / TPO PROFILE PHASE
        val, poc, vah = compute_tpo_profile(acc_df, bin_size=bin_size)

        # Post-accumulation session (until 15:25 IST)
        post_df = day_df[(day_df["time"] > acc_end_time) & (day_df["time"] <= time(15, 25))].reset_index(drop=True)
        if len(post_df) < 45:
            continue

        # 3. MANIPULATION PHASE (Spring or Upthrust)
        for setup_type in ["BULLISH_SPRING", "BEARISH_UPTHRUST"]:
            swept = False
            sweep_idx = None
            reclaim_idx = None
            sweep_extreme = None
            sweep_time = None

            for i, row in post_df.iterrows():
                # Allow manipulation to form until 13:30 IST
                if row["time"] > time(13, 30) and not swept:
                    break

                if setup_type == "BULLISH_SPRING":
                    if not swept:
                        # Sweep below VAL
                        if row["low"] <= (val - min_sweep_pts):
                            swept = True
                            sweep_idx = i
                            sweep_extreme = float(row["low"])
                            sweep_time = row["time"]
                    else:
                        if row["low"] < sweep_extreme:
                            sweep_extreme = float(row["low"])
                        # Reclaim back above VAL
                        if row["close"] > val:
                            if (i - sweep_idx) <= max_reclaim_candles:
                                reclaim_idx = i
                            break
                        if (i - sweep_idx) > max_reclaim_candles:
                            break

                elif setup_type == "BEARISH_UPTHRUST":
                    if not swept:
                        # Sweep above VAH
                        if row["high"] >= (vah + min_sweep_pts):
                            swept = True
                            sweep_idx = i
                            sweep_extreme = float(row["high"])
                            sweep_time = row["time"]
                    else:
                        if row["high"] > sweep_extreme:
                            sweep_extreme = float(row["high"])
                        # Reclaim back below VAH
                        if row["close"] < vah:
                            if (i - sweep_idx) <= max_reclaim_candles:
                                reclaim_idx = i
                            break
                        if (i - sweep_idx) > max_reclaim_candles:
                            break

            # If manipulation verified and reclaimed cleanly
            if reclaim_idx is not None and sweep_extreme is not None:
                reclaim_row = post_df.iloc[reclaim_idx]
                entry_price = float(reclaim_row["close"])
                entry_time = reclaim_row["time"]
                sl_price = sweep_extreme
                risk = abs(entry_price - sl_price)

                if risk < 4.0 or risk > 65.0:
                    continue

                # 4. DISTRIBUTION PHASE
                dist_df = post_df.iloc[reclaim_idx + 1:].reset_index(drop=True)
                if len(dist_df) == 0:
                    continue

                target_hit = False
                sl_hit = False
                max_favorable = 0.0
                dist_time = None
                opposing_va = vah if setup_type == "BULLISH_SPRING" else val
                min_pts_target = max(risk * min_rr_target, abs(opposing_va - entry_price))

                for _, d_row in dist_df.iterrows():
                    if setup_type == "BULLISH_SPRING":
                        if d_row["low"] <= sl_price:
                            sl_hit = True
                            break
                        fav = float(d_row["high"]) - entry_price
                        if fav > max_favorable:
                            max_favorable = fav
                        if (d_row["high"] >= entry_price + min_pts_target) or (d_row["high"] >= vah):
                            target_hit = True
                            if dist_time is None:
                                dist_time = d_row["time"]
                    else:
                        if d_row["high"] >= sl_price:
                            sl_hit = True
                            break
                        fav = entry_price - float(d_row["low"])
                        if fav > max_favorable:
                            max_favorable = fav
                        if (d_row["low"] <= entry_price - min_pts_target) or (d_row["low"] <= val):
                            target_hit = True
                            if dist_time is None:
                                dist_time = d_row["time"]

                if target_hit and not sl_hit:
                    achieved_rr = max_favorable / risk if risk > 0 else 0
                    results.append({
                        "date": str(d),
                        "direction": "BULLISH (Spring)" if setup_type == "BULLISH_SPRING" else "BEARISH (Upthrust)",
                        "acc_range_low": round(acc_low, 1),
                        "acc_range_high": round(acc_high, 1),
                        "acc_width_pts": round(acc_range, 1),
                        "profile_val": round(val, 1),
                        "profile_poc": round(poc, 1),
                        "profile_vah": round(vah, 1),
                        "sweep_time": str(sweep_time)[:8],
                        "sweep_extreme": round(sweep_extreme, 1),
                        "sweep_depth_pts": round(abs(sweep_extreme - (val if setup_type == "BULLISH_SPRING" else vah)), 1),
                        "entry_time": str(entry_time)[:8],
                        "entry_price": round(entry_price, 1),
                        "sl_price": round(sl_price, 1),
                        "risk_pts": round(risk, 1),
                        "dist_target_time": str(dist_time)[:8] if dist_time else "EOD",
                        "max_gain_pts": round(max_favorable, 1),
                        "rr_ratio": round(achieved_rr, 2),
                    })
                    break  # One setup per day to avoid double counting

    return results


def main():
    parser = argparse.ArgumentParser(description="Scan NIFTY 50 for Accumulation -> Profile -> Manipulation -> Distribution setups.")
    parser.add_argument("--start-date", default="2026-04-01", help="Start date (YYYY-MM-DD)")
    parser.add_argument("--end-date", default="2026-10-15", help="End date (YYYY-MM-DD)")
    parser.add_argument("--db-url", default=DEFAULT_DB_URL, help="PostgreSQL connection string")
    parser.add_argument("--acc-end", default="10:15", help="End of accumulation phase (HH:MM IST, e.g. 10:15 or 10:30)")
    parser.add_argument("--min-sweep", type=float, default=8.0, help="Minimum manipulation sweep beyond VAL/VAH in points")
    parser.add_argument("--max-reclaim", type=int, default=15, help="Maximum candles allowed to reclaim the range")
    parser.add_argument("--min-rr", type=float, default=1.5, help="Minimum Risk-Reward for distribution confirmation")
    parser.add_argument("--output-csv", default="nifty_amd_profile_setups.csv", help="CSV path to save results")

    args = parser.parse_args()

    results = scan_amd_profile_setups(
        db_url=args.db_url,
        start_date=args.start_date,
        end_date=args.end_date,
        acc_end_str=args.acc_end,
        min_sweep_pts=args.min_sweep,
        max_reclaim_candles=args.max_reclaim,
        min_rr_target=args.min_rr,
    )

    if not results:
        print("\nNo setups matched the criteria.")
        return

    res_df = pd.DataFrame(results)
    res_df.to_csv(args.output_csv, index=False)
    print(f"\n[+] Successfully exported {len(res_df)} setups to: {args.output_csv}\n")

    # Display Pretty Table
    print(f"{'#':<3} | {'Date':<10} | {'Direction':<18} | {'VAL / POC / VAH':<22} | {'Sweep Time':<10} | {'Reclaim':<10} | {'Risk':<6} | {'Max Move':<9} | {'R:R':<6}")
    print("-" * 115)
    for idx, r in enumerate(results, start=1):
        va_str = f"{r['profile_val']:.0f} / {r['profile_poc']:.0f} / {r['profile_vah']:.0f}"
        print(
            f"{idx:<3} | {r['date']:<10} | {r['direction']:<18} | {va_str:<22} | "
            f"{r['sweep_time']:<10} | {r['entry_time']:<10} | {r['risk_pts']:<6.1f} | "
            f"+{r['max_gain_pts']:<8.1f} | {r['rr_ratio']:<6.2f}R"
        )

    print("-" * 115)
    print(f"Summary Statistics:")
    print(f"  Total Valid Setups Identified : {len(res_df)}")
    print(f"  Bullish Springs               : {len(res_df[res_df['direction'].str.contains('BULLISH')])}")
    print(f"  Bearish Upthrusts             : {len(res_df[res_df['direction'].str.contains('BEARISH')])}")
    print(f"  Average Risk (Pts)            : {res_df['risk_pts'].mean():.1f} pts")
    print(f"  Average Distribution Move     : {res_df['max_gain_pts'].mean():.1f} pts")
    print(f"  Average Realized R:R Ratio    : {res_df['rr_ratio'].mean():.2f}R")
    print(f"  Highest Single Move           : {res_df['max_gain_pts'].max():.1f} pts ({res_df.loc[res_df['max_gain_pts'].idxmax(), 'date']})")
    print("=" * 115)


if __name__ == "__main__":
    main()
