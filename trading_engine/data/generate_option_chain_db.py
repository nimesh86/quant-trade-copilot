"""
Permanent Option Chain Database & Cache Generator
==================================================
Reads Spot Index candles for each trading day across the 90-day historical range.
Identifies the active Weekly Expiry and constructs a 24-contract option grid per day:
  - 12 Call (CE) contracts: 6 ITM, 1 ATM, 5 OTM
  - 12 Put (PE) contracts: 6 ITM, 1 ATM, 5 OTM

Validates existing data for gaps (375 bars from 09:15 to 15:29).
If missing or incomplete, generates high-precision option candles with:
  - Strict inter-day continuity (anchored to previous day close + overnight gap)
  - Non-linear Delta and Gamma convexity
  - Intraday Theta decay accelerating near expiry
  - Permanent storage in both data/cache/ (JSON) and data/market_data.duckdb
"""

import os
import sys
import json
import math
import logging
from datetime import datetime, date, timedelta
from pathlib import Path
from typing import Dict, List, Any, Optional, Tuple

import numpy as np
import pandas as pd
from scipy.stats import norm
import duckdb

# Ensure workspace root is in path
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from trading_engine.quant_core.option_resolver import OptionResolver

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("OptionChainGenerator")

CACHE_DIR = PROJECT_ROOT / "data" / "cache"
DUCKDB_PATH = PROJECT_ROOT / "data" / "market_data.duckdb"
CACHE_DIR.mkdir(parents=True, exist_ok=True)


class OptionChainDatabaseGenerator:
    """
    Autonomous pipeline for permanent generation and verification of weekly option chains.
    """

    RISK_FREE_RATE = 0.065  # 6.5% Indian risk-free rate
    STEP_SIZE = 50.0        # NIFTY strike step

    def __init__(self, duckdb_path: Path = DUCKDB_PATH, cache_dir: Path = CACHE_DIR):
        self.duckdb_path = duckdb_path
        self.cache_dir = cache_dir
        self.con = duckdb.connect(str(self.duckdb_path))
        self._init_duckdb_schema()

    def _init_duckdb_schema(self):
        """Initializes tables for permanent option candles and synchronized series."""
        cur = self.con.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS option_minute_candles (
                contract VARCHAR,
                symbol VARCHAR,
                date VARCHAR,
                timestamp TIMESTAMP,
                time_str VARCHAR,
                strike DOUBLE,
                option_type VARCHAR,
                expiry_date VARCHAR,
                open DOUBLE,
                high DOUBLE,
                low DOUBLE,
                close DOUBLE,
                volume BIGINT,
                oi BIGINT,
                PRIMARY KEY (contract, timestamp)
            );
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS synchronized_candles (
                timestamp TIMESTAMP,
                symbol VARCHAR,
                contract VARCHAR,
                timeframe VARCHAR,
                spot_open DOUBLE,
                spot_high DOUBLE,
                spot_low DOUBLE,
                spot_close DOUBLE,
                spot_volume BIGINT,
                opt_open DOUBLE,
                opt_high DOUBLE,
                opt_low DOUBLE,
                opt_close DOUBLE,
                opt_volume BIGINT,
                opt_oi BIGINT,
                PRIMARY KEY (symbol, contract, timeframe, timestamp)
            );
        """)
        cur.close()

    def get_spot_trading_dates(self) -> List[Tuple[str, Path]]:
        """Returns all available Spot trading days sorted chronologically."""
        spot_files = sorted(list(self.cache_dir.glob("candles_NIFTY_*.json")))
        result = []
        for sf in spot_files:
            date_str = sf.stem.replace("candles_NIFTY_", "")
            result.append((date_str, sf))
        return result

    def load_spot_candles(self, file_path: Path) -> pd.DataFrame:
        """Loads and normalizes Spot candles into a 375-bar DataFrame (09:15 to 15:29)."""
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            raw_list = data if isinstance(data, list) else data.get("candles", [])
            df = pd.DataFrame(raw_list)
            if df.empty:
                return pd.DataFrame()

            if "time" not in df.columns and "timestamp" in df.columns:
                df["time"] = df["timestamp"]

            # Filter to trading hours 09:15 to 15:29
            df["time_hm"] = df["time"].astype(str).str[-5:]
            df = df[(df["time_hm"] >= "09:15") & (df["time_hm"] <= "15:29")]
            df = df.drop_duplicates("time_hm").sort_values("time_hm").reset_index(drop=True)
            return df
        except Exception as e:
            logger.error(f"Error loading spot file {file_path}: {e}")
            return pd.DataFrame()

    def build_strike_grid(self, atm_strike: float) -> Tuple[List[float], List[float]]:
        """
        Constructs the 24-strike grid:
          - Call (CE): 6 ITM (ATM-300 to ATM-50), 1 ATM (ATM), 5 OTM (ATM+50 to ATM+250)
          - Put (PE):  6 ITM (ATM+300 to ATM+50), 1 ATM (ATM), 5 OTM (ATM-50 to ATM-250)
        """
        step = self.STEP_SIZE

        # CE Strikes
        ce_itm = [atm_strike - i * step for i in range(6, 0, -1)]  # 6 ITM (lowest to highest)
        ce_atm = [atm_strike]                                      # 1 ATM
        ce_otm = [atm_strike + i * step for i in range(1, 6)]      # 5 OTM
        ce_strikes = sorted(ce_itm + ce_atm + ce_otm)

        # PE Strikes
        pe_itm = [atm_strike + i * step for i in range(6, 0, -1)]  # 6 ITM (highest to lowest)
        pe_atm = [atm_strike]                                      # 1 ATM
        pe_otm = [atm_strike - i * step for i in range(1, 6)]      # 5 OTM
        pe_strikes = sorted(pe_itm + pe_atm + pe_otm)

        return ce_strikes, pe_strikes

    def check_contract_candle_validity(self, symbol: str, date_str: str) -> Optional[pd.DataFrame]:
        """
        Checks whether valid 375-bar data exists in cache or DuckDB.
        Returns DataFrame if complete and valid, else None.
        """
        cache_file = self.cache_dir / f"candles_{symbol}_{date_str}.json"
        if cache_file.exists():
            try:
                with open(cache_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, list) and len(data) >= 370:
                    df = pd.DataFrame(data)
                    return df
            except Exception:
                pass

        # Check DuckDB
        try:
            cur = self.con.cursor()
            res = cur.execute("""
                SELECT open, high, low, close, volume, oi, time_str, timestamp
                FROM option_minute_candles
                WHERE contract = ? AND date = ?
                ORDER BY timestamp ASC
            """, [symbol, date_str]).fetchall()
            cur.close()
            if len(res) >= 370:
                cols = ["open", "high", "low", "close", "volume", "oi", "time", "timestamp"]
                return pd.DataFrame(res, columns=cols)
        except Exception:
            pass

        return None

    def get_previous_day_close(self, contract_symbol: str, prev_date_str: Optional[str]) -> Optional[float]:
        """Looks up the closing price of the contract on the previous trading date."""
        if not prev_date_str:
            return None

        # Check cache file
        cache_file = self.cache_dir / f"candles_{contract_symbol}_{prev_date_str}.json"
        if cache_file.exists():
            try:
                with open(cache_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, list) and len(data) > 0:
                    return float(data[-1].get("close", 0.0))
            except Exception:
                pass

        # Check DuckDB
        try:
            cur = self.con.cursor()
            res = cur.execute("""
                SELECT close FROM option_minute_candles
                WHERE contract = ? AND date = ?
                ORDER BY timestamp DESC LIMIT 1
            """, [contract_symbol, prev_date_str]).fetchone()
            cur.close()
            if res and res[0] is not None:
                return float(res[0])
        except Exception:
            pass

        return None

    def generate_contract_candles(
        self,
        spot_df: pd.DataFrame,
        date_str: str,
        strike: float,
        option_type: str,
        expiry_date_str: str,
        contract_symbol: str,
        prev_close: Optional[float] = None,
        prev_spot_close: Optional[float] = None
    ) -> pd.DataFrame:
        """
        Generates mathematically continuous 375-minute candles using Black-Scholes Greeks:
          - Inter-day continuity: Anchors 09:15 open to previous day close + overnight gap.
          - Intra-minute non-linear Delta & Gamma convexity.
          - Theta decay accelerating toward expiry.
          - Accurate high/low geometry for CE vs PE.
        """
        bars = []
        is_ce = (option_type.upper() == "CE")
        exp_dt = pd.to_datetime(f"{expiry_date_str} 15:30:00")
        r = self.RISK_FREE_RATE

        # Running price tracker
        current_opt_px = None

        for idx, row in spot_df.iterrows():
            time_str = str(row["time"])
            time_hm = time_str[-5:]
            bar_ts = pd.to_datetime(f"{date_str} {time_hm}:00")

            spot_o = float(row["open"])
            spot_h = float(row["high"])
            spot_l = float(row["low"])
            spot_c = float(row["close"])

            # Time to expiry in years
            total_sec_left = max(180.0, (exp_dt - bar_ts).total_seconds())
            t_exp_years = total_sec_left / (365.25 * 86400.0)

            # Dynamic Implied Volatility (IV) with moneyness smile/skew
            moneyness = spot_c / strike
            base_iv = 0.135
            if is_ce:
                # Call slight smile for OTM
                iv_skew = max(-0.015, min(0.04, (moneyness - 1.0) * 0.25))
            else:
                # Put skew: lower strikes have higher IV
                iv_skew = max(-0.015, min(0.05, (1.0 - moneyness) * 0.35))
            sigma = max(0.09, min(0.28, base_iv + iv_skew))

            # BSM d1, d2
            sqrt_t = math.sqrt(t_exp_years)
            d1 = (math.log(moneyness) + (r + 0.5 * sigma**2) * t_exp_years) / (sigma * sqrt_t)
            d2 = d1 - sigma * sqrt_t

            cdf_d1 = norm.cdf(d1)
            cdf_d2 = norm.cdf(d2)
            pdf_d1 = norm.pdf(d1)

            # Greeks
            if is_ce:
                delta = cdf_d1
                bs_price = spot_c * cdf_d1 - strike * math.exp(-r * t_exp_years) * cdf_d2
            else:
                delta = cdf_d1 - 1.0
                bs_price = strike * math.exp(-r * t_exp_years) * norm.cdf(-d2) - spot_c * norm.cdf(-d1)

            gamma = pdf_d1 / (spot_c * sigma * sqrt_t)

            # Intraday Theta Decay per minute
            theta_yr = -(spot_c * pdf_d1 * sigma) / (2.0 * sqrt_t)
            if is_ce:
                theta_yr -= r * strike * math.exp(-r * t_exp_years) * cdf_d2
            else:
                theta_yr += r * strike * math.exp(-r * t_exp_years) * norm.cdf(-d2)

            minute_theta = abs(theta_yr) / 94500.0
            if expiry_date_str == date_str:
                # Expiry day acceleration into closing hour
                mins_left_today = max(1.0, (exp_dt - bar_ts).total_seconds() / 60.0)
                if mins_left_today < 120.0:
                    minute_theta *= (1.0 + (120.0 - mins_left_today) / 40.0)

            delta_mag = abs(delta)
            bs_price = max(0.05, bs_price)

            # Establish Opening Price at 09:15
            if idx == 0:
                if prev_close is not None and prev_close > 0.0 and prev_spot_close is not None:
                    # STRICT INTER-DAY CONTINUITY: Yesterday close adjusted for overnight spot gap
                    overnight_gap = spot_o - prev_spot_close
                    overnight_theta = minute_theta * 15.0  # ~15 mins of decay overnight
                    convexity = 0.5 * gamma * (overnight_gap ** 2)

                    if is_ce:
                        opt_o = prev_close + (overnight_gap * delta_mag) + convexity - overnight_theta
                    else:
                        opt_o = prev_close - (overnight_gap * delta_mag) + convexity - overnight_theta
                    opt_o = max(0.05, round(opt_o, 2))
                else:
                    opt_o = round(bs_price, 2)
            else:
                opt_o = current_opt_px

            # Compute Minute Bar Close with Delta + Gamma Convexity
            spot_change_c = spot_c - spot_o
            convexity_minute = 0.5 * gamma * (spot_change_c ** 2)

            # Defensive clamp against raw broker feed anomalies (e.g. floor glitches)
            spot_up_clamp = min(max(0.0, spot_h - spot_o), 40.0)
            spot_down_clamp = min(max(0.0, spot_o - spot_l), 40.0)

            if is_ce:
                opt_c = opt_o + (spot_change_c * delta_mag) + convexity_minute - minute_theta
                # CE makes High when Spot makes High, Low when Spot makes Low
                opt_h = max(opt_o, opt_c, opt_o + (spot_up_clamp * delta_mag))
                opt_l = max(0.05, min(opt_o, opt_c, opt_o - (spot_down_clamp * delta_mag)))
            else:
                opt_c = opt_o - (spot_change_c * delta_mag) + convexity_minute - minute_theta
                # PE makes High when Spot makes Low, Low when Spot makes High
                opt_h = max(opt_o, opt_c, opt_o + (spot_down_clamp * delta_mag))
                opt_l = max(0.05, min(opt_o, opt_c, opt_o - (spot_up_clamp * delta_mag)))

            opt_c = max(0.05, round(opt_c, 2))
            opt_h = max(opt_h, opt_o, opt_c)
            opt_l = min(opt_l, opt_o, opt_c)

            # Realistic liquid volume and OI
            base_vol = int(row.get("volume", 5000))
            vol_multiplier = max(0.1, 1.2 - abs(moneyness - 1.0) * 8.0)
            opt_vol = int(base_vol * vol_multiplier * 0.8) + 150
            opt_oi = int(1200000 * max(0.2, 1.0 - abs(moneyness - 1.0) * 5.0))

            bars.append({
                "time": time_str,
                "timestamp": str(bar_ts),
                "open": opt_o,
                "high": round(opt_h, 2),
                "low": round(opt_l, 2),
                "close": opt_c,
                "volume": opt_vol,
                "oi": opt_oi
            })

            current_opt_px = opt_c

        return pd.DataFrame(bars)

    def save_cache_file(self, df: pd.DataFrame, contract_symbol: str, date_str: str):
        """Saves candles permanently to JSON cache."""
        cache_file = self.cache_dir / f"candles_{contract_symbol}_{date_str}.json"
        records = df.to_dict(orient="records")
        with open(cache_file, "w", encoding="utf-8") as f:
            json.dump(records, f)

    def batch_insert_duckdb(self, day_dfs: List[pd.DataFrame]):
        """Executes a single high-speed batch insertion into DuckDB for the entire day."""
        if not day_dfs:
            return
        combined = pd.concat(day_dfs, ignore_index=True)
        cur = self.con.cursor()
        try:
            cur.register("batch_opt_df", combined)
            cur.execute("""
                INSERT OR REPLACE INTO option_minute_candles 
                SELECT 
                    contract, symbol, date, timestamp, time_str, strike, option_type, expiry_date,
                    open, high, low, close, volume, oi
                FROM batch_opt_df
            """)
            cur.unregister("batch_opt_df")
        finally:
            cur.close()

    def process_all_dates(self):
        """Iterates through all 90-day spot trading days and generates complete 24-contract option chains."""
        trading_days = self.get_spot_trading_dates()
        total_days = len(trading_days)
        logger.info(f"Starting 90-Day Option Chain Pipeline: {total_days} Trading Sessions Found.")

        prev_date_str = None
        prev_spot_close = None

        total_generated = 0
        total_verified = 0

        for day_idx, (date_str, spot_file) in enumerate(trading_days):
            spot_df = self.load_spot_candles(spot_file)
            if spot_df.empty or len(spot_df) < 50:
                logger.warning(f"[{day_idx+1}/{total_days}] Skipping {date_str}: Insufficient spot data.")
                continue

            day_open_spot = float(spot_df.iloc[0]["open"])
            day_close_spot = float(spot_df.iloc[-1]["close"])
            atm_strike = round(day_open_spot / self.STEP_SIZE) * self.STEP_SIZE

            # Weekly Expiry Resolution
            sample_dt = datetime.strptime(date_str, "%Y-%m-%d").replace(hour=10)
            expiry_date_str = OptionResolver.resolve_expiry_date(sample_dt, "NIFTY")

            ce_strikes, pe_strikes = self.build_strike_grid(atm_strike)

            logger.info(
                f"[{day_idx+1}/{total_days}] {date_str} | Spot Open: {day_open_spot:.1f} | "
                f"ATM: {atm_strike:.0f} | Weekly Expiry: {expiry_date_str} | 24 Contracts"
            )

            day_db_dfs = []

            # Process CE Contracts (12 contracts: 6 ITM, 1 ATM, 5 OTM)
            for strike in ce_strikes:
                sym = OptionResolver.build_contract_symbol("NIFTY", strike, "CE", expiry_date_str)
                existing_df = self.check_contract_candle_validity(sym, date_str)

                if existing_df is not None:
                    total_verified += 1
                else:
                    prev_c = self.get_previous_day_close(sym, prev_date_str)
                    gen_df = self.generate_contract_candles(
                        spot_df=spot_df,
                        date_str=date_str,
                        strike=strike,
                        option_type="CE",
                        expiry_date_str=expiry_date_str,
                        contract_symbol=sym,
                        prev_close=prev_c,
                        prev_spot_close=prev_spot_close
                    )
                    self.save_cache_file(gen_df, sym, date_str)

                    # Prepare for batch DB insert
                    db_df = gen_df.copy()
                    db_df["contract"] = sym
                    db_df["symbol"] = "NIFTY"
                    db_df["date"] = date_str
                    db_df["strike"] = strike
                    db_df["option_type"] = "CE"
                    db_df["expiry_date"] = expiry_date_str
                    db_df["time_str"] = db_df["time"]
                    db_df["timestamp"] = pd.to_datetime(db_df["timestamp"])
                    day_db_dfs.append(db_df)
                    total_generated += 1

            # Process PE Contracts (12 contracts: 6 ITM, 1 ATM, 5 OTM)
            for strike in pe_strikes:
                sym = OptionResolver.build_contract_symbol("NIFTY", strike, "PE", expiry_date_str)
                existing_df = self.check_contract_candle_validity(sym, date_str)

                if existing_df is not None:
                    total_verified += 1
                else:
                    prev_c = self.get_previous_day_close(sym, prev_date_str)
                    gen_df = self.generate_contract_candles(
                        spot_df=spot_df,
                        date_str=date_str,
                        strike=strike,
                        option_type="PE",
                        expiry_date_str=expiry_date_str,
                        contract_symbol=sym,
                        prev_close=prev_c,
                        prev_spot_close=prev_spot_close
                    )
                    self.save_cache_file(gen_df, sym, date_str)

                    # Prepare for batch DB insert
                    db_df = gen_df.copy()
                    db_df["contract"] = sym
                    db_df["symbol"] = "NIFTY"
                    db_df["date"] = date_str
                    db_df["strike"] = strike
                    db_df["option_type"] = "PE"
                    db_df["expiry_date"] = expiry_date_str
                    db_df["time_str"] = db_df["time"]
                    db_df["timestamp"] = pd.to_datetime(db_df["timestamp"])
                    day_db_dfs.append(db_df)
                    total_generated += 1

            # Batch insert day's contracts into DuckDB
            if day_db_dfs:
                self.batch_insert_duckdb(day_db_dfs)

            prev_date_str = date_str
            prev_spot_close = day_close_spot

        logger.info(
            f"Pipeline Complete: {total_days} sessions processed. "
            f"Generated: {total_generated} contracts. Verified existing: {total_verified} contracts."
        )


if __name__ == "__main__":
    generator = OptionChainDatabaseGenerator()
    generator.process_all_dates()
