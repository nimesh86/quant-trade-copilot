"""
Feature Extraction Engine for NIFTY 1-Minute Bars
─────────────────────────────────────────────────
Extracts the exact 54 technical and macro indicators required by
the trained Dual XGBoost Macro Model (macro_model.pkl).
Pure pandas/numpy implementation for sub-10ms evaluation.
"""

from datetime import datetime, time as dt_time, timedelta
from typing import List, Optional
import numpy as np
import pandas as pd


NIFTY_FEATURE_COLUMNS = [
    'rsi', 'macd', 'macd_signal', 'macd_hist', 'ema9', 'ema20', 'ema50', 'sma200',
    'vwap_dist', 'bollinger_upper', 'bollinger_lower', 'bollinger_width', 'atr',
    'volume_ratio', 'volume_sma20', 'stoch_rsi_k', 'stoch_rsi_d', 'williams_r',
    'roc_10', 'roc_20', 'adx', 'di_plus', 'di_minus', 'cci', 'ema9_20_cross',
    'ema20_50_cross', 'close_above_sma200', 'close_vs_ema50_pct', 'weekly_trend_slope',
    'pullback_in_uptrend', 'atr_pct', 'bollinger_pct', 'returns_1m', 'volatility_20',
    'volatility_60', 'vol_regime', 'candle_body_pct', 'upper_shadow_pct',
    'lower_shadow_pct', 'rsi_5m', 'rsi_15m', 'ema20_5m', 'atr_5m',
    'minutes_since_open', 'session_progress', 'day_of_week', 'is_first_hour',
    'is_last_hour', 'volume_change', 'cum_volume_delta_20', 'obv_slope', 'mfi',
    'relative_strike', 'days_to_expiry'
]


def _compute_rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()
    rs = avg_gain / (avg_loss + 1e-9)
    return 100.0 - (100.0 / (1.0 + rs))


def _compute_atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    prev_close = close.shift(1)
    tr1 = high - low
    tr2 = (high - prev_close).abs()
    tr3 = (low - prev_close).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    return tr.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()


def _compute_adx(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14):
    up_move = high.diff()
    down_move = -low.diff()
    
    pos_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
    neg_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)
    
    atr = _compute_atr(high, low, close, period)
    pos_dm_s = pd.Series(pos_dm, index=close.index).ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()
    neg_dm_s = pd.Series(neg_dm, index=close.index).ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()
    
    di_plus = 100.0 * (pos_dm_s / (atr + 1e-9))
    di_minus = 100.0 * (neg_dm_s / (atr + 1e-9))
    
    dx = 100.0 * ((di_plus - di_minus).abs() / (di_plus + di_minus + 1e-9))
    adx = dx.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()
    return adx, di_plus, di_minus


def compute_nifty_features(
    df: pd.DataFrame,
    fut_df: Optional[pd.DataFrame] = None,
    relative_strike: int = 0
) -> pd.DataFrame:
    """
    Computes all 54 features required for the NIFTY Dual XGBoost macro model.
    Accepts 1-minute OHLCV DataFrame (must have: open, high, low, close).
    Optionally accepts NIFTY-I futures DataFrame for accurate volume & VWAP.
    """
    data = df.copy()
    data.columns = [c.lower() for c in data.columns]
    
    # Ensure datetime index / column
    if "timestamp" in data.columns:
        data["dt"] = pd.to_datetime(data["timestamp"])
    elif "datetime" in data.columns:
        data["dt"] = pd.to_datetime(data["datetime"])
    else:
        data["dt"] = pd.date_range(end=datetime.now(), periods=len(data), freq="1min")
        
    close = data["close"]
    high = data["high"]
    low = data["low"]
    open_p = data["open"]
    
    # Volume: if cash spot volume is 0 and futures volume is provided, use futures volume
    vol = data.get("volume", pd.Series(0.0, index=data.index)).astype(float)
    if vol.max() == 0 and fut_df is not None and not fut_df.empty:
        fut_vol = fut_df.get("volume", fut_df.get("Volume", pd.Series(1000.0, index=fut_df.index)))
        if len(fut_vol) >= len(vol):
            vol = fut_vol.iloc[-len(vol):].reset_index(drop=True)
            vol.index = data.index
    vol = vol.replace(0, 100.0)

    # 1. Momentum & Oscillators
    data["rsi"] = _compute_rsi(close, 14).fillna(50.0)
    
    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    data["macd"] = (ema12 - ema26).fillna(0.0)
    data["macd_signal"] = data["macd"].ewm(span=9, adjust=False).mean().fillna(0.0)
    data["macd_hist"] = (data["macd"] - data["macd_signal"]).fillna(0.0)
    
    # Moving Averages
    data["ema9"] = close.ewm(span=9, adjust=False).mean().fillna(close)
    data["ema20"] = close.ewm(span=20, adjust=False).mean().fillna(close)
    data["ema50"] = close.ewm(span=50, adjust=False).mean().fillna(close)
    data["sma200"] = close.rolling(min(len(close), 200), min_periods=10).mean().fillna(data["ema50"])
    
    # VWAP & VWAP Distance
    hlc3 = (high + low + close) / 3.0
    cum_vol = vol.cumsum().replace(0, 1.0)
    cum_pv = (hlc3 * vol).cumsum()
    vwap = cum_pv / cum_vol
    data["vwap_dist"] = ((close - vwap) / vwap).fillna(0.0)
    
    # Bollinger Bands
    sma20 = close.rolling(20, min_periods=5).mean().fillna(close)
    std20 = close.rolling(20, min_periods=5).std().fillna(10.0)
    data["bollinger_upper"] = sma20 + (2.0 * std20)
    data["bollinger_lower"] = sma20 - (2.0 * std20)
    bb_range = (data["bollinger_upper"] - data["bollinger_lower"]).replace(0, 1.0)
    data["bollinger_width"] = (bb_range / sma20).fillna(0.01)
    data["bollinger_pct"] = ((close - data["bollinger_lower"]) / bb_range).clip(0.0, 1.0).fillna(0.5)
    
    # ATR
    data["atr"] = _compute_atr(high, low, close, 14).fillna(15.0)
    data["atr_pct"] = (data["atr"] / close).fillna(0.005)
    
    # Volume signals
    data["volume_sma20"] = vol.rolling(20, min_periods=1).mean().fillna(vol)
    data["volume_ratio"] = (vol / data["volume_sma20"]).fillna(1.0)
    data["volume_change"] = vol.pct_change(1).fillna(0.0)
    data["cum_volume_delta_20"] = data["volume_change"].rolling(20, min_periods=1).sum().fillna(0.0)
    
    # Stochastic RSI
    rsi_series = data["rsi"]
    min_rsi = rsi_series.rolling(14, min_periods=3).min()
    max_rsi = rsi_series.rolling(14, min_periods=3).max()
    stoch_k = 100.0 * (rsi_series - min_rsi) / (max_rsi - min_rsi + 1e-9)
    data["stoch_rsi_k"] = stoch_k.fillna(50.0)
    data["stoch_rsi_d"] = data["stoch_rsi_k"].rolling(3, min_periods=1).mean().fillna(50.0)
    
    # Williams %R
    h14 = high.rolling(14, min_periods=3).max()
    l14 = low.rolling(14, min_periods=3).min()
    data["williams_r"] = (-100.0 * (h14 - close) / (h14 - l14 + 1e-9)).fillna(-50.0)
    
    # Rate of Change
    data["roc_10"] = (close.pct_change(10) * 100.0).fillna(0.0)
    data["roc_20"] = (close.pct_change(20) * 100.0).fillna(0.0)
    
    # ADX, DI+, DI-
    adx_s, di_p, di_m = _compute_adx(high, low, close, 14)
    data["adx"] = adx_s.fillna(20.0)
    data["di_plus"] = di_p.fillna(20.0)
    data["di_minus"] = di_m.fillna(20.0)
    
    # CCI
    mad = (hlc3 - sma20).abs().rolling(20, min_periods=5).mean().replace(0, 1.0)
    data["cci"] = ((hlc3 - sma20) / (0.015 * mad)).fillna(0.0)
    
    # Moving Average Crosses & Context
    data["ema9_20_cross"] = ((data["ema9"] - data["ema20"]) / close).fillna(0.0)
    data["ema20_50_cross"] = ((data["ema20"] - data["ema50"]) / close).fillna(0.0)
    data["close_above_sma200"] = (close > data["sma200"]).astype(int)
    data["close_vs_ema50_pct"] = ((close - data["ema50"]) / data["ema50"].replace(0, 1.0)).fillna(0.0)
    
    shift_300 = min(len(close) - 1, 300)
    data["weekly_trend_slope"] = (close.pct_change(shift_300)).fillna(0.0) if shift_300 > 0 else 0.0
    
    uptrend = (close > data["ema50"]) & (data["ema20"] > data["ema50"])
    pullback = close < open_p
    data["pullback_in_uptrend"] = (uptrend & pullback).astype(int)
    
    # Returns & Volatility
    data["returns_1m"] = close.pct_change(1).fillna(0.0)
    data["volatility_20"] = data["returns_1m"].rolling(20, min_periods=3).std().fillna(0.001)
    data["volatility_60"] = data["returns_1m"].rolling(60, min_periods=5).std().fillna(data["volatility_20"])
    data["vol_regime"] = (data["volatility_20"] / data["volatility_60"].replace(0, 1.0)).fillna(1.0)
    
    # Candle Structure
    candle_range = (high - low).replace(0, 1.0)
    data["candle_body_pct"] = ((close - open_p).abs() / candle_range).clip(0.0, 1.0)
    data["upper_shadow_pct"] = ((high - np.maximum(open_p, close)) / candle_range).clip(0.0, 1.0)
    data["lower_shadow_pct"] = ((np.minimum(open_p, close) - low) / candle_range).clip(0.0, 1.0)
    
    # Multi-timeframe Resampling (5m & 15m)
    try:
        df_dt = data.set_index("dt")
        df_5m = df_dt.resample("5min").agg({"open": "first", "high": "max", "low": "min", "close": "last"}).dropna()
        if len(df_5m) >= 3:
            rsi_5m = _compute_rsi(df_5m["close"], 14).iloc[-1]
            ema20_5m = df_5m["close"].ewm(span=20, adjust=False).mean().iloc[-1]
            atr_5m = _compute_atr(df_5m["high"], df_5m["low"], df_5m["close"], 14).iloc[-1]
        else:
            rsi_5m, ema20_5m, atr_5m = data["rsi"].iloc[-1], data["ema20"].iloc[-1], data["atr"].iloc[-1]
            
        df_15m = df_dt.resample("15min").agg({"close": "last"}).dropna()
        rsi_15m = _compute_rsi(df_15m["close"], 14).iloc[-1] if len(df_15m) >= 3 else rsi_5m
    except Exception:
        rsi_5m = data["rsi"].iloc[-1]
        rsi_15m = rsi_5m
        ema20_5m = data["ema20"].iloc[-1]
        atr_5m = data["atr"].iloc[-1]
        
    data["rsi_5m"] = float(rsi_5m if not np.isnan(rsi_5m) else 50.0)
    data["rsi_15m"] = float(rsi_15m if not np.isnan(rsi_15m) else 50.0)
    data["ema20_5m"] = float(ema20_5m if not np.isnan(ema20_5m) else close.iloc[-1])
    data["atr_5m"] = float(atr_5m if not np.isnan(atr_5m) else 15.0)
    
    # Session Times & Days to Expiry
    last_dt = pd.to_datetime(data["dt"].iloc[-1])
    m_since_open = max(0, (last_dt.hour - 9) * 60 + (last_dt.minute - 15))
    data["minutes_since_open"] = float(m_since_open)
    data["session_progress"] = float(min(1.0, m_since_open / 375.0))
    data["day_of_week"] = int(last_dt.weekday())
    data["is_first_hour"] = int(m_since_open < 60)
    data["is_last_hour"] = int(m_since_open >= 315)
    
    # OBV & MFI
    obv = (np.sign(close.diff().fillna(0)) * vol).cumsum()
    data["obv_slope"] = (obv.diff(5) / 5.0).fillna(0.0)
    typical = hlc3
    raw_money = typical * vol
    pos_flow = pd.Series(np.where(typical > typical.shift(1), raw_money, 0.0), index=data.index).rolling(14, min_periods=1).sum()
    neg_flow = pd.Series(np.where(typical < typical.shift(1), raw_money, 0.0), index=data.index).rolling(14, min_periods=1).sum()
    m_ratio = pos_flow / (neg_flow + 1e-9)
    data["mfi"] = (100.0 - (100.0 / (1.0 + m_ratio))).fillna(50.0)
    
    # Options Structure
    data["relative_strike"] = relative_strike
    # Thursday weekly expiry calculation (weekday 3 is Thursday)
    days_to_thu = (3 - last_dt.weekday()) % 7
    data["days_to_expiry"] = int(days_to_thu)
    
    return data[NIFTY_FEATURE_COLUMNS]
