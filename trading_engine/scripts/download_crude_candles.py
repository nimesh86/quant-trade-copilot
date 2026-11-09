"""
Historical Data Downloader for MCX CRUDEOIL Futures (3 Years)
─────────────────────────────────────────────────────────────
Connects directly to ICICI Direct Breeze API (0 third-party scraping).
Downloads 1-minute historical OHLCV + OI data for MCX CRUDEOIL front-month futures
across the past 3 years (November 2023 to November 2026, ~36 contracts).

Features:
- Automated expiry resolution for past MCX commodity futures contracts.
- Backward chunk pagination (1,000 candles per API request).
- Resilient retry and rate-limiting guard (0.15s pause, well within 5,000 daily Breeze quota).
- Intermediate progress saving (resumable per month).
- Deduplication and chronological ordering.
- Outputs standardized CSV: data/historical/CRUDEOIL_1min_3years.csv
"""

import os
import sys
import time
import json
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple
import pandas as pd

# Set up project root
ROOT_DIR = Path(__file__).resolve().parent.parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from trading_engine.adapters.breeze_adapter import BreezeBrokerAdapter

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s - %(message)s",
    datefmt="%H:%M:%S"
)
logger = logging.getLogger("download_crude")

DATA_DIR = ROOT_DIR / "data" / "historical"
OUTPUT_FILE = DATA_DIR / "CRUDEOIL_1min_3years.csv"
PROGRESS_FILE = DATA_DIR / "crude_download_progress.json"


def find_crude_expiry(breeze, year: int, month: int) -> Optional[str]:
    """
    Probes candidate days around the 19th to find the exact official MCX expiry date.
    """
    for day in [19, 18, 20, 17, 21, 16]:
        try:
            exp_iso = f"{year}-{month:02d}-{day:02d}T06:00:00.000Z"
            from_iso = f"{year}-{month:02d}-01T09:00:00.000Z"
            to_iso = f"{year}-{month:02d}-{day:02d}T23:30:00.000Z"
            res = breeze.get_historical_data_v2(
                interval="1day",
                from_date=from_iso,
                to_date=to_iso,
                stock_code="CRUDE",
                exchange_code="MCX",
                product_type="futures",
                expiry_date=exp_iso,
                right="others",
                strike_price="0"
            )
            data = res.get("Success") or []
            if len(data) > 0:
                return f"{year}-{month:02d}-{day:02d}"
        except Exception as e:
            logger.debug(f"Probe exception for {year}-{month:02d}-{day:02d}: {e}")
        time.sleep(0.08)
    return None


def fetch_contract_candles(
    breeze,
    expiry_date_str: str,
    from_dt: datetime,
    to_dt: datetime
) -> List[Dict[str, Any]]:
    """
    Paginates backward from to_dt to from_dt for a specific contract expiry.
    Returns list of candle dicts.
    """
    exp_iso = f"{expiry_date_str}T06:00:00.000Z"
    all_candles = []
    seen_datetimes = set()

    curr_to = to_dt
    req_count = 0
    empty_in_a_row = 0

    while curr_to > from_dt and empty_in_a_row < 3:
        to_iso = curr_to.strftime("%Y-%m-%dT%H:%M:%S.000Z")
        from_iso = from_dt.strftime("%Y-%m-%dT%H:%M:%S.000Z")

        res = None
        for attempt in range(3):
            try:
                res = breeze.get_historical_data_v2(
                    interval="1minute",
                    from_date=from_iso,
                    to_date=to_iso,
                    stock_code="CRUDE",
                    exchange_code="MCX",
                    product_type="futures",
                    expiry_date=exp_iso,
                    right="others",
                    strike_price="0"
                )
                break
            except Exception as e:
                logger.warning(f"Error fetching candles, attempt {attempt+1}: {e}")
                time.sleep(0.5)

        req_count += 1
        time.sleep(0.12)  # Rate limiting guard

        if not res or res.get("Status") != 200:
            err = res.get("Error") if res else "No response"
            logger.warning(f"Breeze error at {to_iso}: {err}")
            empty_in_a_row += 1
            curr_to -= timedelta(days=2)
            continue

        chunk = res.get("Success") or []
        if not chunk:
            empty_in_a_row += 1
            curr_to -= timedelta(days=2)
            continue

        empty_in_a_row = 0
        new_in_chunk = 0
        earliest_in_chunk = None

        for c in chunk:
            dt_str = c.get("datetime")
            if not dt_str:
                continue
            if dt_str not in seen_datetimes:
                seen_datetimes.add(dt_str)
                all_candles.append({
                    "datetime": dt_str,
                    "open": float(c.get("open", 0.0) or 0.0),
                    "high": float(c.get("high", 0.0) or 0.0),
                    "low": float(c.get("low", 0.0) or 0.0),
                    "close": float(c.get("close", 0.0) or 0.0),
                    "volume": int(float(c.get("volume", 0) or 0)),
                    "open_interest": int(float(c.get("open_interest", 0) or 0)),
                })
                new_in_chunk += 1

            try:
                c_dt = datetime.strptime(dt_str, "%Y-%m-%d %H:%M:%S")
                if earliest_in_chunk is None or c_dt < earliest_in_chunk:
                    earliest_in_chunk = c_dt
            except Exception:
                pass

        if len(chunk) < 1000 or earliest_in_chunk is None or earliest_in_chunk <= from_dt:
            # Reached beginning of contract window
            break

        # Move to_date to just before the earliest candle in this chunk
        curr_to = earliest_in_chunk - timedelta(seconds=1)

    return all_candles


def run_download(months_back: int = 36):
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    adapter = BreezeBrokerAdapter()
    breeze = adapter.connect()
    if not breeze:
        logger.error("Failed to connect to Breeze API. Exiting.")
        return False

    # Load progress if available
    progress = {}
    if PROGRESS_FILE.exists():
        try:
            with open(PROGRESS_FILE, "r") as f:
                progress = json.load(f)
        except Exception:
            progress = {}

    # Build list of year-months to process (most recent to oldest)
    now = datetime.now()
    target_months = []
    curr_y = now.year
    curr_m = now.month

    for _ in range(months_back):
        target_months.append((curr_y, curr_m))
        curr_m -= 1
        if curr_m < 1:
            curr_m = 12
            curr_y -= 1

    logger.info(f"Targeting {len(target_months)} months of CRUDEOIL historical data ({target_months[-1]} to {target_months[0]}).")

    # Step 1: Resolve all contract expiries
    expiries: Dict[str, str] = progress.get("expiries", {})
    for y, m in target_months:
        key = f"{y}-{m:02d}"
        if key not in expiries:
            logger.info(f"Resolving expiry for {key}...")
            exp_date = find_crude_expiry(breeze, y, m)
            if exp_date:
                expiries[key] = exp_date
                logger.info(f"  -> Found: {exp_date}")
            else:
                logger.warning(f"  -> Could not resolve expiry for {key}!")
            progress["expiries"] = expiries
            with open(PROGRESS_FILE, "w") as f:
                json.dump(progress, f, indent=2)

    # Sort months chronologically
    sorted_months = sorted(target_months)
    all_downloaded_candles = []

    # If CSV already exists, load existing data
    if OUTPUT_FILE.exists():
        try:
            df_existing = pd.read_csv(OUTPUT_FILE)
            logger.info(f"Loaded {len(df_existing)} existing candles from {OUTPUT_FILE}.")
            all_downloaded_candles = df_existing.to_dict("records")
        except Exception as e:
            logger.warning(f"Could not read existing CSV: {e}")

    existing_dts = {c["datetime"] for c in all_downloaded_candles}
    completed_months = set(progress.get("completed_months", []))

    # Step 2: Download candles for each contract period
    for i, (y, m) in enumerate(sorted_months):
        m_key = f"{y}-{m:02d}"
        if m_key in completed_months:
            logger.info(f"[{i+1}/{len(sorted_months)}] Month {m_key} already completed. Skipping.")
            continue

        curr_exp_str = expiries.get(m_key)
        if not curr_exp_str:
            logger.warning(f"Skipping {m_key} (no expiry found).")
            continue

        # Front-month active period:
        # from previous month's expiry date + 1 day at 09:00:00
        # to current month's expiry date at 23:30:00
        if i > 0:
            prev_y, prev_m = sorted_months[i - 1]
            prev_m_key = f"{prev_y}-{prev_m:02d}"
            prev_exp_str = expiries.get(prev_m_key)
        else:
            prev_exp_str = None

        curr_exp_dt = datetime.strptime(curr_exp_str, "%Y-%m-%d")
        to_dt = curr_exp_dt.replace(hour=23, minute=30, second=0)

        # For current ongoing month, limit to_dt to now
        if to_dt > now:
            to_dt = now

        if prev_exp_str:
            prev_exp_dt = datetime.strptime(prev_exp_str, "%Y-%m-%d")
            from_dt = (prev_exp_dt + timedelta(days=1)).replace(hour=9, minute=0, second=0)
        else:
            from_dt = (curr_exp_dt - timedelta(days=30)).replace(hour=9, minute=0, second=0)

        logger.info(f"[{i+1}/{len(sorted_months)}] Downloading {m_key} (Contract Expiry: {curr_exp_str}) from {from_dt} to {to_dt}...")
        candles = fetch_contract_candles(breeze, curr_exp_str, from_dt, to_dt)
        logger.info(f"  -> Fetched {len(candles)} candles for {m_key}.")

        # Add new candles
        new_cnt = 0
        for c in candles:
            if c["datetime"] not in existing_dts:
                existing_dts.add(c["datetime"])
                all_downloaded_candles.append(c)
                new_cnt += 1

        logger.info(f"  -> Added {new_cnt} unique candles. Total cumulative: {len(all_downloaded_candles)}")

        # Mark month completed (unless it is current ongoing month)
        if to_dt < (now - timedelta(days=2)):
            completed_months.add(m_key)
            progress["completed_months"] = list(completed_months)

        # Persist intermediate CSV after every month
        df_out = pd.DataFrame(all_downloaded_candles)
        df_out = df_out.sort_values("datetime").reset_index(drop=True)
        df_out.to_csv(OUTPUT_FILE, index=False)
        with open(PROGRESS_FILE, "w") as f:
            json.dump(progress, f, indent=2)

    # Final sort and summary
    df_final = pd.DataFrame(all_downloaded_candles)
    df_final = df_final.sort_values("datetime").drop_duplicates(subset=["datetime"]).reset_index(drop=True)
    df_final.to_csv(OUTPUT_FILE, index=False)

    logger.info("=" * 60)
    logger.info(f"SUCCESS! Historical dataset saved to: {OUTPUT_FILE}")
    logger.info(f"Total 1-minute candles: {len(df_final):,}")
    if len(df_final) > 0:
        logger.info(f"Date range: {df_final['datetime'].iloc[0]} to {df_final['datetime'].iloc[-1]}")
    logger.info("=" * 60)
    return True


if __name__ == "__main__":
    months = int(sys.argv[1]) if len(sys.argv) > 1 else 36
    run_download(months)

