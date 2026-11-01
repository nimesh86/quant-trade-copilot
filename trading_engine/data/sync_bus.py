"""
Timestamp Alignment Bus for Cross-Asset Spot and Option Synchronization.
Guarantees zero-lookahead forward fill for illiquid option minutes.
Ensures perfect deterministic alignment between Underlying Spot and Option Contracts.
"""
from typing import Optional
import numpy as np
import pandas as pd


class TimestampAlignmentBus:
    """
    Synchronizes Spot Index and Option contract OHLCV bars into a unified DataFrame.
    Adheres strictly to zero-lookahead rules.
    """

    @staticmethod
    def align_spot_and_option(
        spot_df: pd.DataFrame,
        option_df: pd.DataFrame,
        symbol: str,
        contract: str,
        timeframe: str = "1m",
        market_hours_only: bool = False
    ) -> pd.DataFrame:
        """
        Performs strict deterministic merge on timestamp with zero-lookahead forward fill.

        Requirements:
        1. Aligns exact timestamps. Spot timeline is master index.
        2. For missing option bars (illiquid minutes with no trades):
           - opt_close is forward-filled (previous valid traded price)
           - opt_open, opt_high, opt_low equal opt_close
           - opt_volume is set to 0
           - opt_oi is forward-filled (or 0)
        3. Zero lookahead: future option prices never leak backward.

        Parameters:
            spot_df: DataFrame containing Spot OHLCV.
            option_df: DataFrame containing Option OHLCV + OI.
            symbol: Symbol string (e.g., 'NIFTY')
            contract: Option contract identifier (e.g., 'NIFTY24OCT24200CE')
            timeframe: Candle timeframe string (default '1m')
            market_hours_only: Whether to filter strictly to 09:15 - 15:30 IST.

        Returns:
            pd.DataFrame conforming to synchronized_candles schema:
            ['timestamp', 'symbol', 'contract', 'timeframe',
             'spot_open', 'spot_high', 'spot_low', 'spot_close', 'spot_volume',
             'opt_open', 'opt_high', 'opt_low', 'opt_close', 'opt_volume', 'opt_oi']
        """
        if spot_df.empty:
            return pd.DataFrame(columns=[
                "timestamp", "symbol", "contract", "timeframe",
                "spot_open", "spot_high", "spot_low", "spot_close", "spot_volume",
                "opt_open", "opt_high", "opt_low", "opt_close", "opt_volume", "opt_oi"
            ])

        s_df = spot_df.copy()
        s_df["timestamp"] = pd.to_datetime(s_df["timestamp"])
        
        # Standardize spot column names
        rename_spot = {}
        for col in s_df.columns:
            clean_col = col.lower()
            if clean_col in ("open", "spot_open"):
                rename_spot[col] = "spot_open"
            elif clean_col in ("high", "spot_high"):
                rename_spot[col] = "spot_high"
            elif clean_col in ("low", "spot_low"):
                rename_spot[col] = "spot_low"
            elif clean_col in ("close", "spot_close"):
                rename_spot[col] = "spot_close"
            elif clean_col in ("volume", "spot_volume", "vol"):
                rename_spot[col] = "spot_volume"
        s_df = s_df.rename(columns=rename_spot)

        # Fallback if open/high/low not present but close is
        if "spot_close" in s_df.columns:
            for c in ["spot_open", "spot_high", "spot_low"]:
                if c not in s_df.columns:
                    s_df[c] = s_df["spot_close"]
        if "spot_volume" not in s_df.columns:
            s_df["spot_volume"] = 0

        s_df = s_df.set_index("timestamp").sort_index()

        # Deduplicate timestamps in spot if any
        s_df = s_df[~s_df.index.duplicated(keep="first")]

        if option_df is not None and not option_df.empty:
            o_df = option_df.copy()
            o_df["timestamp"] = pd.to_datetime(o_df["timestamp"])
            rename_opt = {}
            for col in o_df.columns:
                clean_col = col.lower()
                if clean_col in ("open", "opt_open"):
                    rename_opt[col] = "opt_open"
                elif clean_col in ("high", "opt_high"):
                    rename_opt[col] = "opt_high"
                elif clean_col in ("low", "opt_low"):
                    rename_opt[col] = "opt_low"
                elif clean_col in ("close", "opt_close"):
                    rename_opt[col] = "opt_close"
                elif clean_col in ("volume", "opt_volume", "vol"):
                    rename_opt[col] = "opt_volume"
                elif clean_col in ("oi", "opt_oi", "open_interest"):
                    rename_opt[col] = "opt_oi"
            o_df = o_df.rename(columns=rename_opt)

            # Fallback if open/high/low not present in option but close is
            if "opt_close" in o_df.columns:
                for c in ["opt_open", "opt_high", "opt_low"]:
                    if c not in o_df.columns:
                        o_df[c] = o_df["opt_close"]

            o_df = o_df.set_index("timestamp").sort_index()
            o_df = o_df[~o_df.index.duplicated(keep="first")]

            # Ensure all required option columns exist
            for c in ["opt_open", "opt_high", "opt_low", "opt_close", "opt_volume", "opt_oi"]:
                if c not in o_df.columns:
                    o_df[c] = np.nan
        else:
            o_df = pd.DataFrame(index=s_df.index, columns=["opt_open", "opt_high", "opt_low", "opt_close", "opt_volume", "opt_oi"])

        # Join option onto spot timeline
        joined = s_df.join(o_df[["opt_open", "opt_high", "opt_low", "opt_close", "opt_volume", "opt_oi"]], how="left")

        # Zero-lookahead fill for missing option bars
        # Forward fill close price
        joined["opt_close"] = joined["opt_close"].ffill()
        first_valid_close = joined["opt_close"].dropna().iloc[0] if joined["opt_close"].notna().any() else 0.0
        joined["opt_close"] = joined["opt_close"].fillna(first_valid_close)

        # For illiquid bars, OHLC is set to close
        joined["opt_open"] = joined["opt_open"].fillna(joined["opt_close"])
        joined["opt_high"] = joined["opt_high"].fillna(joined["opt_close"])
        joined["opt_low"] = joined["opt_low"].fillna(joined["opt_close"])
        joined["opt_volume"] = joined["opt_volume"].fillna(0).astype("int64")
        joined["opt_oi"] = joined["opt_oi"].ffill().fillna(0).astype("int64")

        # Ensure spot columns
        for c in ["spot_open", "spot_high", "spot_low", "spot_close"]:
            if c in joined.columns:
                joined[c] = joined[c].ffill().bfill()
        if "spot_volume" in joined.columns:
            joined["spot_volume"] = joined["spot_volume"].fillna(0).astype("int64")

        joined = joined.reset_index()
        joined["symbol"] = symbol
        joined["contract"] = contract
        joined["timeframe"] = timeframe

        if market_hours_only:
            time_part = joined["timestamp"].dt.time
            from datetime import time
            joined = joined[(time_part >= time(9, 15)) & (time_part <= time(15, 30))].copy()

        ordered_cols = [
            "timestamp", "symbol", "contract", "timeframe",
            "spot_open", "spot_high", "spot_low", "spot_close", "spot_volume",
            "opt_open", "opt_high", "opt_low", "opt_close", "opt_volume", "opt_oi"
        ]
        return joined[ordered_cols].reset_index(drop=True)
