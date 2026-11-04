"""
Historical Data API Client for Quant Engine.
────────────────────────────────────────────
Implements the 'Historical Data API Source' integration for Backtest and Quant Core.
Connects to remote VPS (e.g. https://kesarisoft.com) to retrieve 1-minute historical
candles for spot indices and option contracts, with local disk caching for instant replay.
"""
import os
import json
import logging
import urllib.request
import urllib.error
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, List, Dict, Any, Union
import pandas as pd

from trading_engine.core.app_config import app_config_manager

logger = logging.getLogger("HistoricalDataClient")

CACHE_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "cache"
DEFAULT_LOCAL_CSV = Path(__file__).resolve().parent.parent / "ai" / "kronos_src" / "finetune_csv" / "data" / "nifty_1min.csv"


class HistoricalDataClient:
    """
    Client for fetching historical candle data based on active Engine Configuration.
    Supports:
      - 'vps' / 'cloud': Queries remote VPS API (e.g. https://kesarisoft.com/api/candles)
      - 'local': Uses local CSV / DuckDB storage
      - 'broker': Broker direct historical API
    """

    def __init__(self, base_url: Optional[str] = None):
        self.base_url = (base_url or "https://kesarisoft.com").rstrip("/")
        self.cache_dir = CACHE_DIR
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.network_calls = 0
        self.cache_hits = 0
        self._missing_file = self.cache_dir / "_missing_cache.json"
        self._missing_cache = set()
        if self._missing_file.exists():
            try:
                self._missing_cache = set(json.loads(self._missing_file.read_text(encoding="utf-8")))
            except Exception:
                pass

    def fetch_candles_for_date_range(
        self,
        symbol: str = "NIFTY",
        start_date: Optional[str] = None,
        end_date: Optional[str] = None
    ) -> pd.DataFrame:
        """
        Fetches 1-minute candles between start_date and end_date (inclusive).
        Checks the active configuration in AppConfigManager:
          - If 'vps' (or endpoint points to VPS): queries the remote API day-by-day with caching.
          - If 'local': reads local CSV/DuckDB.
        """
        hist_source, hist_endpoint = app_config_manager.get_hist_source()
        active_endpoint = (hist_endpoint or self.base_url).rstrip("/")

        logger.info(f"[HistoricalDataClient] Fetching {symbol} from {start_date} to {end_date} via '{hist_source}' ({active_endpoint})")

        # If source is set to local, load local file directly
        if hist_source == "local":
            return self._load_local_data(symbol, start_date, end_date)

        # VPS / Remote API Source
        try:
            return self._fetch_from_vps_api(active_endpoint, symbol, start_date, end_date)
        except Exception as e:
            logger.warning(f"[HistoricalDataClient] VPS fetch failed ({e}). Falling back to local data source.")
            return self._load_local_data(symbol, start_date, end_date)

    def _fetch_from_vps_api(
        self,
        endpoint: str,
        symbol: str,
        start_date: Optional[str],
        end_date: Optional[str]
    ) -> pd.DataFrame:
        """
        Queries VPS historical API day-by-day across the requested calendar range.
        Caches individual daily candle responses locally in data/cache/.
        """
        # Default to last 30 days if dates not provided
        if not start_date:
            start_date = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")
        if not end_date:
            end_date = datetime.now().strftime("%Y-%m-%d")

        try:
            s_dt = datetime.strptime(start_date[:10], "%Y-%m-%d")
            e_dt = datetime.strptime(end_date[:10], "%Y-%m-%d")
        except Exception:
            s_dt = datetime.now() - timedelta(days=7)
            e_dt = datetime.now()

        # Normalize symbol name
        clean_sym = symbol.upper().replace(" 50", "").strip()
        is_spot_index = clean_sym in ["NIFTY", "BANKNIFTY", "SENSEX", "FINNIFTY"]

        all_candles: List[Dict[str, Any]] = []
        cur = s_dt

        while cur <= e_dt:
            # Skip Saturday (5) and Sunday (6)
            if cur.weekday() < 5:
                date_str = cur.strftime("%Y-%m-%d")
                day_candles = self._fetch_or_cache_day(endpoint, clean_sym, date_str, is_spot_index)
                if day_candles:
                    all_candles.extend(day_candles)
            cur += timedelta(days=1)

        if not all_candles:
            logger.warning(f"[HistoricalDataClient] 0 candles returned from VPS for {clean_sym} [{start_date} to {end_date}]")
            return pd.DataFrame()

        df = pd.DataFrame(all_candles)

        # Normalize timestamp column (prioritize exchange session 'time' string e.g. '2026-10-29 09:15')
        if "time" in df.columns:
            df["timestamp"] = pd.to_datetime(df["time"])
        elif "timestamp" in df.columns:
            if isinstance(df["timestamp"].iloc[0], (int, float)):
                df["timestamp"] = pd.to_datetime(df["timestamp"], unit="s")
            else:
                df["timestamp"] = pd.to_datetime(df["timestamp"])

        # Ensure required numeric columns exist
        for col in ["open", "high", "low", "close", "volume"]:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0.0)

        df = df.sort_values("timestamp").drop_duplicates(subset=["timestamp"]).reset_index(drop=True)
        logger.info(f"[HistoricalDataClient] Successfully loaded {len(df)} candles from VPS API (cache hits: {self.cache_hits}, network: {self.network_calls})")
        return df

    def _fetch_or_cache_day(
        self,
        endpoint: str,
        symbol: str,
        date_str: str,
        is_spot_index: bool
    ) -> List[Dict[str, Any]]:
        """
        Returns candle list for a single trading date, reading from disk cache if available.
        """
        cache_file = self.cache_dir / f"candles_{symbol}_{date_str}.json"

        # Check local cache
        if cache_file.exists():
            try:
                data = json.loads(cache_file.read_text(encoding="utf-8"))
                if isinstance(data, list) and len(data) > 0:
                    self.cache_hits += 1
                    return data
            except Exception:
                pass

        # Local missing set check
        cache_key = f"{symbol}_{date_str}"
        if cache_key in self._missing_cache:
            return []

        # Network query
        self.network_calls += 1
        if is_spot_index and symbol == "NIFTY":
            url = f"{endpoint}/api/candles?date={date_str}"
        else:
            url = f"{endpoint}/api/contracts/candles?symbol={symbol}&date={date_str}"

        try:
            req = urllib.request.Request(
                url,
                headers={"User-Agent": "QuantEngine/1.0", "Accept": "application/json"}
            )
            with urllib.request.urlopen(req, timeout=2) as resp:
                if resp.status == 200:
                    raw = json.loads(resp.read().decode("utf-8"))
                    candles = raw.get("candles", []) if isinstance(raw, dict) else raw
                    if candles:
                        try:
                            cache_file.write_text(json.dumps(candles), encoding="utf-8")
                        except Exception:
                            pass
                        return candles
            self._record_missing(cache_key)
        except Exception as e:
            self._record_missing(cache_key)
            logger.debug(f"[HistoricalDataClient] Failed fetching {url}: {e}")

        return []

    def _record_missing(self, cache_key: str):
        self._missing_cache.add(cache_key)
        try:
            self._missing_file.write_text(json.dumps(list(self._missing_cache)), encoding="utf-8")
        except Exception:
            pass

    def _load_local_data(
        self,
        symbol: str,
        start_date: Optional[str],
        end_date: Optional[str]
    ) -> pd.DataFrame:
        """
        Loads candles from local embedded storage (CSV or DuckDB).
        """
        if DEFAULT_LOCAL_CSV.exists():
            try:
                df = pd.read_csv(DEFAULT_LOCAL_CSV)
                df.columns = [c.lower().strip() for c in df.columns]
                ts_col = next((c for c in ["timestamp", "timestamps", "datetime", "date", "time"] if c in df.columns), None)
                if ts_col:
                    df["timestamp"] = pd.to_datetime(df[ts_col])
                else:
                    df["timestamp"] = pd.date_range("2026-01-01 09:15:00", periods=len(df), freq="1min")

                if start_date:
                    df = df[df["timestamp"] >= pd.to_datetime(start_date)]
                if end_date:
                    df = df[df["timestamp"] < pd.to_datetime(end_date) + timedelta(days=1)]

                return df.sort_values("timestamp").reset_index(drop=True)
            except Exception as e:
                logger.error(f"[HistoricalDataClient] Local CSV read failed: {e}")

        return pd.DataFrame()


# Global client singleton
historical_data_client = HistoricalDataClient()
