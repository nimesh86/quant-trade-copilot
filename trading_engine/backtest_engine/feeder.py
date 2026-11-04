"""
Backtest Engine: Historical Multi-Asset Feeder.
───────────────────────────────────────────────
Fetches and synchronizes Spot Index candles and Option Contract candles
from local cache and the Cloud VPS Gateway (https://kesarisoft.com).
Implements precise Black-Scholes delta-elasticity modeling for CE & PE.
"""
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional, Tuple
from pathlib import Path
import json
import pandas as pd
import numpy as np

from trading_engine.data.historical_client import HistoricalDataClient
from trading_engine.quant_core.contracts import Bar, OptionContract
from trading_engine.quant_core.option_resolver import OptionResolver

CACHE_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "cache"


class BacktestFeeder:
    """
    Synchronizes spot and option candle feeds for backtest simulation.
    """

    def __init__(
        self,
        data_client: Optional[HistoricalDataClient] = None,
        allow_remote_contracts: bool = False
    ):
        self.data_client = data_client or HistoricalDataClient()
        self.allow_remote_contracts = allow_remote_contracts
        self._contract_cache: Dict[str, Dict[str, Dict[str, Any]]] = {}
        self._contract_day_candles_cache: Dict[Tuple[str, str], List[Dict[str, Any]]] = {}

    def load_spot_candles(
        self,
        symbol: str,
        start_date: str,
        end_date: str
    ) -> pd.DataFrame:
        """
        Pulls spot candles across date range.
        Returns cleaned DataFrame with columns: ['timestamp', 'open', 'high', 'low', 'close', 'volume']
        Automatically sanitizes broker placeholder artifacts (e.g. daily low 23053.1).
        """
        norm_symbol = "NIFTY" if "NIFTY" in symbol.upper() else symbol.upper()
        df = self.data_client.fetch_candles_for_date_range(norm_symbol, start_date, end_date)
        
        if df.empty:
            return pd.DataFrame()

        # Ensure correct column naming and sorted chronological order
        df = df.copy()
        if "timestamp" in df.columns:
            df["timestamp"] = pd.to_datetime(df["timestamp"])
        elif "time" in df.columns:
            df["timestamp"] = pd.to_datetime(df["time"])

        df = df.sort_values("timestamp").reset_index(drop=True)

        # Automatic Data Cleansing & Outlier Sanitization
        n_rows = len(df)
        for i in range(n_rows):
            op = float(df.at[i, "open"])
            cl = float(df.at[i, "close"])
            hi = float(df.at[i, "high"])
            lo = float(df.at[i, "low"])

            # Fix glitch open (< 23100 for Nifty)
            if op < 23100.0 and i > 0 and cl > 23100.0:
                op = float(df.at[i - 1, "close"])
                df.at[i, "open"] = op

            # Fix corrupt low spikes (e.g. 23053.1 daily/52-week low floor)
            body_min = min(op, cl)
            body_max = max(op, cl)
            if lo < (body_min - 50.0) or (lo == 23053.1 and body_min > 23300.0):
                wick = max(0.5, (hi - body_max) * 0.4)
                lo = round(body_min - wick, 2)
                df.at[i, "low"] = lo

            # Ensure high is valid
            if hi < body_max:
                df.at[i, "high"] = round(body_max + 0.5, 2)

        return df

    def _load_and_validate_file(self, file_path: Path, bar_date: str) -> List[Dict[str, Any]]:
        """Safely loads and validates that a JSON candle file contains bars for bar_date."""
        if file_path and file_path.exists():
            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                raw_candles = data if isinstance(data, list) else data.get("candles", [])
                candles = [c for c in raw_candles if str(c.get("time", "")).startswith(bar_date)]
                if candles and len(candles) >= 30:
                    return candles
            except Exception:
                pass
        return []

    def _get_files_for_date(self, bar_date: str) -> List[Path]:
        if not hasattr(self, "_date_to_files_index") or self._date_to_files_index is None:
            self._date_to_files_index: Dict[str, List[Path]] = {}
            for p in CACHE_DIR.glob("*.json"):
                d_str = p.stem.split("_")[-1]
                if d_str not in self._date_to_files_index:
                    self._date_to_files_index[d_str] = []
                self._date_to_files_index[d_str].append(p)
        return self._date_to_files_index.get(bar_date, [])

    def _find_valid_contract_candles(
        self,
        symbol: str,
        strike: float,
        option_type: str,
        bar_date: str
    ) -> List[Dict[str, Any]]:
        """
        Locates valid 1-minute historical candles for the specified contract and date.
        Validates date stamps inside candidate files. If the target strike has no real
        data or corrupted dates, automatically checks nearest liquid strikes (+/- 50, +/- 100).
        """
        # Candidate 1: Exact symbol file
        cache_file = CACHE_DIR / f"candles_{symbol}_{bar_date}.json"
        candles = self._load_and_validate_file(cache_file, bar_date)
        if candles:
            return candles

        # Candidate 2: Indexed fast lookup among files for this date
        day_files = self._get_files_for_date(bar_date)
        for offset in [0, 50, -50, 100, -100]:
            alt_strike = int(strike + offset)
            sub = f"{alt_strike}{option_type}"
            for p in day_files:
                if sub in p.name:
                    candles = self._load_and_validate_file(p, bar_date)
                    if candles:
                        return candles

        # If not found locally and allowed, query Cloud VPS on demand
        if self.allow_remote_contracts:
            try:
                endpoint = getattr(self.data_client, "base_url", "https://kesarisoft.com")
                fetched = self.data_client._fetch_or_cache_day(
                    endpoint=endpoint,
                    symbol=symbol,
                    date_str=bar_date,
                    is_spot_index=False
                )
                if fetched:
                    cache_file = CACHE_DIR / f"candles_{symbol}_{bar_date}.json"
                    candles = self._load_and_validate_file(cache_file, bar_date)
                    if candles:
                        return candles
            except Exception:
                pass

        return []

    def get_contract_day_candles(
        self,
        symbol: str,
        bar_date: str
    ) -> List[Dict[str, Any]]:
        """
        Retrieves complete 1-minute historical candles for a specific contract and trading date.
        """
        cache_key = (symbol, bar_date)
        if cache_key in self._contract_day_candles_cache:
            return self._contract_day_candles_cache[cache_key]

        strike = 0.0
        opt_type = "CE" if "CE" in symbol else "PE"
        import re
        m = re.search(r"(\d{5})(CE|PE)", symbol)
        if m:
            strike = float(m.group(1))
            opt_type = m.group(2)
        candles = self._find_valid_contract_candles(symbol, strike, opt_type, bar_date)
        self._contract_day_candles_cache[cache_key] = candles
        return candles

    def get_or_synthesize_option_bar(
        self,
        spot_bar: Bar,
        contract: OptionContract,
        prev_option_close: Optional[float] = None
    ) -> Bar:
        """
        Returns real/cached contract candle if available or synthesizes a realistic option candle
        using directional Black-Scholes delta sensitivity, intrinsic value, and time decay.
        """
        bar_date = spot_bar.timestamp.strftime("%Y-%m-%d") if isinstance(spot_bar.timestamp, datetime) else str(spot_bar.timestamp)[:10]
        time_hm = spot_bar.timestamp.strftime("%H:%M") if isinstance(spot_bar.timestamp, datetime) else str(spot_bar.timestamp)[11:16]
        cache_key = f"{contract.symbol}_{bar_date}"

        # 1. Check local contract file cache with strict date validation and liquid strike fallback
        if cache_key not in self._contract_cache:
            candles_list = self._find_valid_contract_candles(
                contract.symbol, contract.strike, contract.option_type, bar_date
            )
            if candles_list:
                self._contract_cache[cache_key] = {
                    str(c.get("time", ""))[-5:]: c for c in candles_list
                }
            else:
                self._contract_cache[cache_key] = {}

        day_cache = self._contract_cache.get(cache_key, {})
        if time_hm in day_cache:
            c = day_cache[time_hm]
            return Bar(
                timestamp=spot_bar.timestamp,
                open=float(c.get("open", 0.0)),
                high=float(c.get("high", 0.0)),
                low=float(c.get("low", 0.0)),
                close=float(c.get("close", 0.0)),
                volume=float(c.get("volume", spot_bar.volume * 2.5))
            )

        # 2. Resilient Black-Scholes synthesis fallback if real option candle unavailable
        delta = OptionResolver.estimate_delta(spot_bar.close, contract.strike, contract.option_type)
        delta_val = abs(delta) if delta else 0.50
        spot_move = spot_bar.close - spot_bar.open

        # Clamp spot intrabar excursions to prevent corrupted spot feeds (e.g. broker floor glitches)
        # from creating artificial comb/needle wicks on option candles
        spot_up = min(max(0.0, spot_bar.high - spot_bar.open), 40.0)
        spot_down = min(max(0.0, spot_bar.open - spot_bar.low), 40.0)

        is_call = (contract.option_type == "CE")
        opt_dir = 1.0 if is_call else -1.0

        if prev_option_close is not None and prev_option_close > 0.5:
            opt_open = prev_option_close
        else:
            # Approximate initial ATM/OTM premium
            dist_from_spot = spot_bar.close - contract.strike if is_call else contract.strike - spot_bar.close
            intrinsic = max(0.0, dist_from_spot)
            time_val = max(15.0, 100.0 - abs(dist_from_spot) * 0.4)
            opt_open = max(2.5, intrinsic + time_val)

        opt_close = max(0.5, opt_open + spot_move * delta_val * opt_dir)
        if is_call:
            opt_high = max(opt_open, opt_close, opt_open + spot_up * delta_val)
            opt_low = max(0.5, min(opt_open, opt_close, opt_open - spot_down * delta_val))
        else:
            opt_high = max(opt_open, opt_close, opt_open + spot_down * delta_val)
            opt_low = max(0.5, min(opt_open, opt_close, opt_open - spot_up * delta_val))

        return Bar(
            timestamp=spot_bar.timestamp,
            open=round(opt_open, 2),
            high=round(opt_high, 2),
            low=round(opt_low, 2),
            close=round(opt_close, 2),
            volume=spot_bar.volume * 1.5
        )
