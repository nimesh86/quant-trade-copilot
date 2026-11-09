"""
Feature Extraction Engine for MCX CRUDEOIL 1-Minute Futures
───────────────────────────────────────────────────────────
Vectorised, high-performance technical & commodity market structure features.
Calculates indicators in pure pandas/numpy with zero external C-dependencies.
"""

from typing import List, Tuple
import numpy as np
import pandas as pd


CRUDE_FEATURE_COLUMNS = [
    # Momentum / Oscillators
    "rsi",
    "rsi_slope_3",
    "macd",
    "macd_signal",
    "macd_hist",
    "macd_hist_slope_3",
    "stoch_k",
    "stoch_d",
    "williams_r",
    "roc_10",
    "roc_20",
    # Moving Averages & Trend
    "ema9",
    "ema20",
    "ema50",
    "sma200",
    "ema9_20_cross",
    "ema20_50_cross",
    "close_above_sma200",
    "close_vs_ema20_pct",
    "close_vs_ema50_pct",
    # Volatility & Bands
    "atr",
    "atr_pct",
    "bollinger_width",
    "bollinger_pct",
    "volatility_20",
    "volatility_60",
    # Volume & Price Action
    "volume_ratio",
    "volume_burst",
    "vwap_dist",
    "returns_1m",
    "returns_5m",
    "returns_15m",
    # Multi-Timeframe & Commodity Session Dynamics
    "rsi_5m",
    "atr_5m",
    "minutes_since_open",
    "session_progress",
    "is_us_open",
    "is_us_active",
    "is_wednesday_eia",
    "day_of_week",
]


def compute_rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()
    rs = avg_gain / (avg_loss + 1e-9)
    rsi = 100.0 - (100.0 / (1.0 + rs))
    return rsi


def compute_atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    prev_close = close.shift(1)
    tr1 = high - low
    tr2 = (high - prev_close).abs()
    tr3 = (low - prev_close).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    return tr.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()


def compute_crude_features(df: pd.DataFrame) -> pd.DataFrame:
    """
    Computes all quantitative features for MCX CRUDEOIL 1-minute bars.
    Input must contain: ['datetime', 'open', 'high', 'low', 'close', 'volume']
    """
    df = df.copy()
    if not pd.api.types.is_datetime64_any_dtype(df["datetime"]):
        df["dt"] = pd.to_datetime(df["datetime"])
    else:
        df["dt"] = df["datetime"]

    close = df["close"]
    high = df["high"]
    low = df["low"]
    volume = df["volume"]

    # 1. Momentum / Oscillators
    df["rsi"] = compute_rsi(close, 14)
    df["rsi_slope_3"] = (df["rsi"] - df["rsi"].shift(3)) / 3.0

    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    df["macd"] = ema12 - ema26
    df["macd_signal"] = df["macd"].ewm(span=9, adjust=False).mean()
    df["macd_hist"] = df["macd"] - df["macd_signal"]
    df["macd_hist_slope_3"] = (df["macd_hist"] - df["macd_hist"].shift(3)) / 3.0

    # Stochastic (14, 3)
    low14 = low.rolling(14).min()
    high14 = high.rolling(14).max()
    df["stoch_k"] = ((close - low14) / (high14 - low14 + 1e-9) * 100.0).rolling(3).mean()
    df["stoch_d"] = df["stoch_k"].rolling(3).mean()

    # Williams %R (14)
    df["williams_r"] = (high14 - close) / (high14 - low14 + 1e-9) * -100.0

    # Rate of Change
    df["roc_10"] = (close - close.shift(10)) / (close.shift(10) + 1e-9) * 100.0
    df["roc_20"] = (close - close.shift(20)) / (close.shift(20) + 1e-9) * 100.0

    # 2. Moving Averages & Trend Structure
    df["ema9"] = close.ewm(span=9, adjust=False).mean()
    df["ema20"] = close.ewm(span=20, adjust=False).mean()
    df["ema50"] = close.ewm(span=50, adjust=False).mean()
    df["sma200"] = close.rolling(200, min_periods=50).mean()

    df["ema9_20_cross"] = (df["ema9"] - df["ema20"]) / (close + 1e-9)
    df["ema20_50_cross"] = (df["ema20"] - df["ema50"]) / (close + 1e-9)
    df["close_above_sma200"] = (close > df["sma200"]).astype(int)
    df["close_vs_ema20_pct"] = (close - df["ema20"]) / (df["ema20"] + 1e-9)
    df["close_vs_ema50_pct"] = (close - df["ema50"]) / (df["ema50"] + 1e-9)

    # 3. Volatility & Bands
    df["atr"] = compute_atr(high, low, close, 14)
    df["atr_pct"] = df["atr"] / (close + 1e-9)

    sma20 = close.rolling(20).mean()
    std20 = close.rolling(20).std()
    boll_upper = sma20 + 2.0 * std20
    boll_lower = sma20 - 2.0 * std20
    df["bollinger_width"] = (boll_upper - boll_lower) / (sma20 + 1e-9)
    df["bollinger_pct"] = (close - boll_lower) / (boll_upper - boll_lower + 1e-9)

    ret1 = close.pct_change()
    df["returns_1m"] = ret1
    df["volatility_20"] = ret1.rolling(20).std()
    df["volatility_60"] = ret1.rolling(60).std()

    # 4. Volume & Flow Dynamics
    vol_sma20 = volume.rolling(20, min_periods=5).mean()
    df["volume_ratio"] = volume / (vol_sma20 + 1e-9)
    df["volume_burst"] = (volume > 1.8 * vol_sma20).astype(int)

    # Intraday VWAP (resets daily)
    date_grp = df["dt"].dt.date
    pv = close * volume
    cum_pv = pv.groupby(date_grp).cumsum()
    cum_vol = volume.groupby(date_grp).cumsum()
    vwap = cum_pv / (cum_vol + 1e-9)
    df["vwap_dist"] = (close - vwap) / (vwap + 1e-9)

    # Multi-period returns
    df["returns_5m"] = close.pct_change(5)
    df["returns_15m"] = close.pct_change(15)

    # 5. Multi-Timeframe Proxies
    # Approximate 5-minute RSI and ATR
    close_5m_proxy = close.rolling(5).mean()
    df["rsi_5m"] = compute_rsi(close_5m_proxy, 14)
    df["atr_5m"] = df["atr"].rolling(5).mean() * np.sqrt(5.0)

    # 6. Commodity Session Dynamics (MCX Hours: 09:00 to 23:30 IST)
    hours = df["dt"].dt.hour
    minutes = df["dt"].dt.minute
    total_mins = hours * 60 + minutes
    market_open_mins = 9 * 60  # 09:00 = 540
    df["minutes_since_open"] = (total_mins - market_open_mins).clip(lower=0)
    df["session_progress"] = (df["minutes_since_open"] / 870.0).clip(0.0, 1.0)
    df["is_us_open"] = (hours >= 17).astype(int)
    df["is_us_active"] = ((hours >= 19) & (hours <= 22)).astype(int)
    df["day_of_week"] = df["dt"].dt.dayofweek
    df["is_wednesday_eia"] = ((df["day_of_week"] == 2) & (hours >= 19) & (hours <= 21)).astype(int)

    # Fill sporadic NaNs from warm-up rolling windows
    df[CRUDE_FEATURE_COLUMNS] = df[CRUDE_FEATURE_COLUMNS].replace([np.inf, -np.inf], np.nan)
    df[CRUDE_FEATURE_COLUMNS] = df[CRUDE_FEATURE_COLUMNS].bfill().ffill().fillna(0.0)

    return df

