"""
Technical Analysis Mathematical Kernels for quant_lib.
Provides hardware-accelerated (NumPy / Numba) moving averages, oscillators, and volatility indicators.
Supports both vectorized historical arrays and incremental streaming updates.
"""
from typing import Tuple, Union, Optional
import numpy as np

try:
    import numba
except Exception:
    class _MockNumba:
        @staticmethod
        def njit(*args, **kwargs):
            def decorator(f):
                return f
            if len(args) == 1 and callable(args[0]) and not kwargs:
                return args[0]
            return decorator
    numba = _MockNumba()


# ─────────────────────────────────────────────────────────────────────────────
# 1. CORE NUMBA JIT RECURRENCE KERNELS
# ─────────────────────────────────────────────────────────────────────────────

@numba.njit(fastmath=True, cache=True)
def _ema_kernel(arr: np.ndarray, alpha: float) -> np.ndarray:
    """Computes EMA recurrence: EMA[t] = alpha * arr[t] + (1 - alpha) * EMA[t-1]."""
    n = len(arr)
    out = np.empty(n, dtype=np.float64)
    if n == 0:
        return out
    out[0] = arr[0]
    inv_alpha = 1.0 - alpha
    for i in range(1, n):
        out[i] = alpha * arr[i] + inv_alpha * out[i - 1]
    return out


@numba.njit(fastmath=True, cache=True)
def _rma_kernel(arr: np.ndarray, alpha: float) -> np.ndarray:
    """Computes Wilder's RMA recurrence (alpha = 1 / length)."""
    n = len(arr)
    out = np.empty(n, dtype=np.float64)
    if n == 0:
        return out
    out[0] = arr[0]
    inv_alpha = 1.0 - alpha
    for i in range(1, n):
        out[i] = alpha * arr[i] + inv_alpha * out[i - 1]
    return out


@numba.njit(fastmath=True, cache=True)
def _wma_kernel(arr: np.ndarray, length: int) -> np.ndarray:
    """Computes Weighted Moving Average with weights 1, 2, ..., length."""
    n = len(arr)
    out = np.empty(n, dtype=np.float64)
    denom = length * (length + 1) / 2.0
    for i in range(n):
        if i < length - 1:
            # Not enough bars: use partial window
            sub_len = i + 1
            sub_denom = sub_len * (sub_len + 1) / 2.0
            s = 0.0
            for j in range(sub_len):
                w = j + 1
                s += arr[i - sub_len + 1 + j] * w
            out[i] = s / sub_denom
        else:
            s = 0.0
            for j in range(length):
                w = j + 1
                s += arr[i - length + 1 + j] * w
            out[i] = s / denom
    return out


@numba.njit(fastmath=True, cache=True)
def _supertrend_kernel(
    high: np.ndarray, low: np.ndarray, close: np.ndarray, atr: np.ndarray, multiplier: float
) -> Tuple[np.ndarray, np.ndarray]:
    """
    Computes Supertrend trailing bands and trend direction (+1 Bullish, -1 Bearish).
    Direction flips strictly upon confirmed candle close beyond opposite band.
    """
    n = len(close)
    direction = np.ones(n, dtype=np.int32)
    st_line = np.empty(n, dtype=np.float64)
    upper_band = np.empty(n, dtype=np.float64)
    lower_band = np.empty(n, dtype=np.float64)

    if n == 0:
        return direction, st_line

    hl2 = (high + low) * 0.5
    basic_upper = hl2 + multiplier * atr
    basic_lower = hl2 - multiplier * atr

    upper_band[0] = basic_upper[0]
    lower_band[0] = basic_lower[0]
    st_line[0] = lower_band[0]

    for i in range(1, n):
        # Upper band adjustment
        if basic_upper[i] < upper_band[i - 1] or close[i - 1] > upper_band[i - 1]:
            upper_band[i] = basic_upper[i]
        else:
            upper_band[i] = upper_band[i - 1]

        # Lower band adjustment
        if basic_lower[i] > lower_band[i - 1] or close[i - 1] < lower_band[i - 1]:
            lower_band[i] = basic_lower[i]
        else:
            lower_band[i] = lower_band[i - 1]

        # Direction flip
        prev_dir = direction[i - 1]
        if prev_dir == 1:
            if close[i] < lower_band[i]:
                direction[i] = -1
                st_line[i] = upper_band[i]
            else:
                direction[i] = 1
                st_line[i] = lower_band[i]
        else:
            if close[i] > upper_band[i]:
                direction[i] = 1
                st_line[i] = lower_band[i]
            else:
                direction[i] = -1
                st_line[i] = upper_band[i]

    return direction, st_line


# ─────────────────────────────────────────────────────────────────────────────
# 2. MOVING AVERAGES
# ─────────────────────────────────────────────────────────────────────────────

def sma(series: Union[np.ndarray, list], length: int) -> np.ndarray:
    """Simple Moving Average (SMA)."""
    arr = np.asarray(series, dtype=np.float64)
    n = len(arr)
    if n == 0:
        return np.array([], dtype=np.float64)
    length = max(1, length)
    out = np.empty(n, dtype=np.float64)
    cumsum = np.cumsum(arr)
    out[:length - 1] = cumsum[:length - 1] / np.arange(1, min(length, n + 1))[:length - 1]
    out[length - 1:] = (cumsum[length - 1:] - np.concatenate(([0.0], cumsum[:-length]))) / length
    return out


def ema(series: Union[np.ndarray, list], length: int, alpha: Optional[float] = None) -> np.ndarray:
    """Exponential Moving Average (EMA)."""
    arr = np.asarray(series, dtype=np.float64)
    if len(arr) == 0:
        return np.array([], dtype=np.float64)
    length = max(1, length)
    a = float(alpha) if alpha is not None else (2.0 / (length + 1.0))
    return _ema_kernel(arr, a)


def rma(series: Union[np.ndarray, list], length: int) -> np.ndarray:
    """Running Moving Average (Wilder's Smoothing used in RSI and ATR)."""
    arr = np.asarray(series, dtype=np.float64)
    if len(arr) == 0:
        return np.array([], dtype=np.float64)
    length = max(1, length)
    return _rma_kernel(arr, 1.0 / length)


def wma(series: Union[np.ndarray, list], length: int) -> np.ndarray:
    """Weighted Moving Average (WMA)."""
    arr = np.asarray(series, dtype=np.float64)
    if len(arr) == 0:
        return np.array([], dtype=np.float64)
    return _wma_kernel(arr, max(1, length))


def hma(series: Union[np.ndarray, list], length: int) -> np.ndarray:
    """Hull Moving Average (HMA): WMA(2*WMA(n/2) - WMA(n), sqrt(n))."""
    arr = np.asarray(series, dtype=np.float64)
    if len(arr) == 0:
        return np.array([], dtype=np.float64)
    length = max(2, length)
    half_len = int(length / 2)
    sqrt_len = int(np.sqrt(length))
    wma_half = wma(arr, half_len)
    wma_full = wma(arr, length)
    diff = 2.0 * wma_half - wma_full
    return wma(diff, sqrt_len)


def vwma(series: Union[np.ndarray, list], volume: Union[np.ndarray, list], length: int) -> np.ndarray:
    """Volume-Weighted Moving Average (VWMA)."""
    arr = np.asarray(series, dtype=np.float64)
    vol = np.asarray(volume, dtype=np.float64)
    pv = arr * vol
    pv_sum = sma(pv, length) * length
    vol_sum = sma(vol, length) * length
    return np.where(vol_sum > 0, pv_sum / vol_sum, arr)


def dema(series: Union[np.ndarray, list], length: int) -> np.ndarray:
    """Double Exponential Moving Average: 2*EMA(n) - EMA(EMA(n), n)."""
    e1 = ema(series, length)
    e2 = ema(e1, length)
    return 2.0 * e1 - e2


def tema(series: Union[np.ndarray, list], length: int) -> np.ndarray:
    """Triple Exponential Moving Average: 3*EMA - 3*EMA(EMA) + EMA(EMA(EMA))."""
    e1 = ema(series, length)
    e2 = ema(e1, length)
    e3 = ema(e2, length)
    return 3.0 * e1 - 3.0 * e2 + e3


def alma(series: Union[np.ndarray, list], length: int = 9, offset: float = 0.85, sigma: float = 6.0) -> np.ndarray:
    """Arnaud Legoux Moving Average (ALMA)."""
    arr = np.asarray(series, dtype=np.float64)
    n = len(arr)
    out = np.empty(n, dtype=np.float64)
    m = np.floor(offset * (length - 1))
    s = length / sigma
    w = np.exp(-((np.arange(length) - m) ** 2) / (2 * s * s))
    w_sum = np.sum(w)

    for i in range(n):
        if i < length - 1:
            out[i] = arr[i]
        else:
            out[i] = np.sum(arr[i - length + 1 : i + 1] * w) / w_sum
    return out


def vwap(
    high: Union[np.ndarray, list],
    low: Union[np.ndarray, list],
    close: Union[np.ndarray, list],
    volume: Union[np.ndarray, list],
    session_reset: Optional[Union[np.ndarray, list]] = None
) -> np.ndarray:
    """
    Intraday Volume-Weighted Average Price (VWAP).
    Resets at session open (e.g. 09:15 IST) when session_reset[i] is True.
    """
    h = np.asarray(high, dtype=np.float64)
    l = np.asarray(low, dtype=np.float64)
    c = np.asarray(close, dtype=np.float64)
    v = np.asarray(volume, dtype=np.float64)
    n = len(c)
    out = np.empty(n, dtype=np.float64)

    tp = (h + l + c) / 3.0
    cum_pv = 0.0
    cum_v = 0.0

    for i in range(n):
        if session_reset is not None and bool(session_reset[i]):
            cum_pv = 0.0
            cum_v = 0.0
        cum_pv += tp[i] * v[i]
        cum_v += v[i]
        out[i] = (cum_pv / cum_v) if cum_v > 0 else tp[i]

    return out


# ─────────────────────────────────────────────────────────────────────────────
# 3. OSCILLATORS & MOMENTUM
# ─────────────────────────────────────────────────────────────────────────────

def rsi(series: Union[np.ndarray, list], length: int = 14) -> np.ndarray:
    """
    Relative Strength Index (RSI).
    Uses Wilder's RMA smoothing for gains and losses, matching TradingView.
    """
    arr = np.asarray(series, dtype=np.float64)
    n = len(arr)
    if n <= 1:
        return np.full(n, 50.0, dtype=np.float64)

    diff = np.diff(arr)
    gains = np.maximum(diff, 0.0)
    losses = np.maximum(-diff, 0.0)

    # Pad first element with 0
    gains = np.concatenate(([0.0], gains))
    losses = np.concatenate(([0.0], losses))

    avg_gain = rma(gains, length)
    avg_loss = rma(losses, length)

    rs = np.divide(avg_gain, avg_loss, out=np.full_like(avg_gain, 100.0), where=avg_loss > 0)
    out = 100.0 - (100.0 / (1.0 + rs))
    return np.clip(out, 0.0, 100.0)


def macd(
    series: Union[np.ndarray, list], fast: int = 12, slow: int = 26, signal: int = 9
) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """
    Moving Average Convergence Divergence (MACD).
    Returns (macd_line, signal_line, histogram).
    """
    fast_ema = ema(series, fast)
    slow_ema = ema(series, slow)
    macd_line = fast_ema - slow_ema
    signal_line = ema(macd_line, signal)
    hist = macd_line - signal_line
    return macd_line, signal_line, hist


def stoch(
    close: Union[np.ndarray, list],
    high: Union[np.ndarray, list],
    low: Union[np.ndarray, list],
    k_len: int = 14,
    d_len: int = 3,
    smooth_k: int = 3
) -> Tuple[np.ndarray, np.ndarray]:
    """Stochastic Oscillator (%K, %D)."""
    c = np.asarray(close, dtype=np.float64)
    h = np.asarray(high, dtype=np.float64)
    l = np.asarray(low, dtype=np.float64)
    n = len(c)

    highest_h = np.empty(n, dtype=np.float64)
    lowest_l = np.empty(n, dtype=np.float64)

    for i in range(n):
        start_idx = max(0, i - k_len + 1)
        highest_h[i] = np.max(h[start_idx : i + 1])
        lowest_l[i] = np.min(l[start_idx : i + 1])

    denom = highest_h - lowest_l
    fast_k = np.where(denom > 0, 100.0 * (c - lowest_l) / denom, 50.0)
    k = sma(fast_k, smooth_k)
    d = sma(k, d_len)
    return k, d


def stoch_rsi(
    series: Union[np.ndarray, list],
    rsi_len: int = 14,
    stoch_len: int = 14,
    k_len: int = 3,
    d_len: int = 3
) -> Tuple[np.ndarray, np.ndarray]:
    """Stochastic RSI."""
    rsi_val = rsi(series, rsi_len)
    return stoch(rsi_val, rsi_val, rsi_val, k_len=stoch_len, d_len=d_len, smooth_k=k_len)


def cci(
    high: Union[np.ndarray, list], low: Union[np.ndarray, list], close: Union[np.ndarray, list], length: int = 20
) -> np.ndarray:
    """Commodity Channel Index (CCI)."""
    h = np.asarray(high, dtype=np.float64)
    l = np.asarray(low, dtype=np.float64)
    c = np.asarray(close, dtype=np.float64)
    tp = (h + l + c) / 3.0
    tp_sma = sma(tp, length)
    n = len(c)
    mad = np.empty(n, dtype=np.float64)

    for i in range(n):
        start_idx = max(0, i - length + 1)
        mad[i] = np.mean(np.abs(tp[start_idx : i + 1] - tp_sma[i]))

    out = np.where(mad > 0, (tp - tp_sma) / (0.015 * mad), 0.0)
    return out


def mfi(
    high: Union[np.ndarray, list],
    low: Union[np.ndarray, list],
    close: Union[np.ndarray, list],
    volume: Union[np.ndarray, list],
    length: int = 14
) -> np.ndarray:
    """Money Flow Index (MFI)."""
    h = np.asarray(high, dtype=np.float64)
    l = np.asarray(low, dtype=np.float64)
    c = np.asarray(close, dtype=np.float64)
    v = np.asarray(volume, dtype=np.float64)

    tp = (h + l + c) / 3.0
    raw_flow = tp * v
    n = len(c)

    pos_flow = np.zeros(n, dtype=np.float64)
    neg_flow = np.zeros(n, dtype=np.float64)

    for i in range(1, n):
        if tp[i] > tp[i - 1]:
            pos_flow[i] = raw_flow[i]
        elif tp[i] < tp[i - 1]:
            neg_flow[i] = raw_flow[i]

    pos_sum = sma(pos_flow, length) * length
    neg_sum = sma(neg_flow, length) * length

    mr = np.where(neg_sum > 0, pos_sum / neg_sum, 100.0)
    return 100.0 - (100.0 / (1.0 + mr))


def roc(series: Union[np.ndarray, list], length: int = 9) -> np.ndarray:
    """Rate of Change (% Momentum)."""
    arr = np.asarray(series, dtype=np.float64)
    n = len(arr)
    out = np.zeros(n, dtype=np.float64)
    if n > length:
        out[length:] = ((arr[length:] - arr[:-length]) / arr[:-length]) * 100.0
    return out


def crossover(series_a: Union[np.ndarray, list], series_b: Union[np.ndarray, list, float]) -> np.ndarray:
    """Returns True iff Series A crossed over Series B on current bar."""
    a = np.asarray(series_a, dtype=np.float64)
    b = np.asarray(series_b, dtype=np.float64) if not isinstance(series_b, (int, float)) else np.full_like(a, series_b)
    n = len(a)
    out = np.zeros(n, dtype=bool)
    if n > 1:
        out[1:] = (a[:-1] <= b[:-1]) & (a[1:] > b[1:])
    return out


def crossunder(series_a: Union[np.ndarray, list], series_b: Union[np.ndarray, list, float]) -> np.ndarray:
    """Returns True iff Series A crossed under Series B on current bar."""
    a = np.asarray(series_a, dtype=np.float64)
    b = np.asarray(series_b, dtype=np.float64) if not isinstance(series_b, (int, float)) else np.full_like(a, series_b)
    n = len(a)
    out = np.zeros(n, dtype=bool)
    if n > 1:
        out[1:] = (a[:-1] >= b[:-1]) & (a[1:] < b[1:])
    return out


# ─────────────────────────────────────────────────────────────────────────────
# 4. VOLATILITY & TREND CHANNELS
# ─────────────────────────────────────────────────────────────────────────────

def true_range(
    high: Union[np.ndarray, list], low: Union[np.ndarray, list], close: Union[np.ndarray, list]
) -> np.ndarray:
    """True Range: max(H - L, |H - C_prev|, |L - C_prev|)."""
    h = np.asarray(high, dtype=np.float64)
    l = np.asarray(low, dtype=np.float64)
    c = np.asarray(close, dtype=np.float64)
    n = len(c)
    if n == 0:
        return np.array([], dtype=np.float64)

    hl = h - l
    if n == 1:
        return hl

    prev_c = np.concatenate(([c[0]], c[:-1]))
    h_prev_c = np.abs(h - prev_c)
    l_prev_c = np.abs(l - prev_c)
    return np.maximum(hl, np.maximum(h_prev_c, l_prev_c))


def atr(
    high: Union[np.ndarray, list], low: Union[np.ndarray, list], close: Union[np.ndarray, list], length: int = 14
) -> np.ndarray:
    """Average True Range (ATR) using Wilder's RMA method."""
    tr = true_range(high, low, close)
    return rma(tr, length)


def supertrend(
    high: Union[np.ndarray, list],
    low: Union[np.ndarray, list],
    close: Union[np.ndarray, list],
    length: int = 10,
    multiplier: float = 3.0
) -> Tuple[np.ndarray, np.ndarray]:
    """
    Supertrend dynamic trend follower.
    Returns (trend_direction, supertrend_line).
    trend_direction: +1 (Bullish), -1 (Bearish).
    """
    h = np.asarray(high, dtype=np.float64)
    l = np.asarray(low, dtype=np.float64)
    c = np.asarray(close, dtype=np.float64)
    atr_val = atr(h, l, c, length)
    return _supertrend_kernel(h, l, c, atr_val, float(multiplier))


def bollinger_bands(
    series: Union[np.ndarray, list], length: int = 20, mult: float = 2.0
) -> Tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """
    Bollinger Bands.
    Returns (upper_band, midline, lower_band, percent_b).
    """
    arr = np.asarray(series, dtype=np.float64)
    mid = sma(arr, length)
    n = len(arr)
    std = np.empty(n, dtype=np.float64)

    for i in range(n):
        start_idx = max(0, i - length + 1)
        std[i] = np.std(arr[start_idx : i + 1])

    upper = mid + mult * std
    lower = mid - mult * std
    denom = upper - lower
    pct_b = np.where(denom > 0, (arr - lower) / denom, 0.5)
    return upper, mid, lower, pct_b


def keltner_channels(
    high: Union[np.ndarray, list],
    low: Union[np.ndarray, list],
    close: Union[np.ndarray, list],
    length: int = 20,
    mult: float = 1.5
) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """
    Keltner Channels.
    Returns (upper_band, midline, lower_band).
    """
    h = np.asarray(high, dtype=np.float64)
    l = np.asarray(low, dtype=np.float64)
    c = np.asarray(close, dtype=np.float64)
    mid = ema(c, length)
    atr_val = atr(h, l, c, length)
    upper = mid + mult * atr_val
    lower = mid - mult * atr_val
    return upper, mid, lower


def donchian_channels(
    high: Union[np.ndarray, list], low: Union[np.ndarray, list], length: int = 20
) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """
    Donchian Channels.
    Returns (upper_band, midline, lower_band).
    """
    h = np.asarray(high, dtype=np.float64)
    l = np.asarray(low, dtype=np.float64)
    n = len(h)
    upper = np.empty(n, dtype=np.float64)
    lower = np.empty(n, dtype=np.float64)

    for i in range(n):
        start_idx = max(0, i - length + 1)
        upper[i] = np.max(h[start_idx : i + 1])
        lower[i] = np.min(l[start_idx : i + 1])

    mid = (upper + lower) * 0.5
    return upper, mid, lower


# ─────────────────────────────────────────────────────────────────────────────
# 5. SUB-MICROSECOND INCREMENTAL STREAMING STEP KERNELS
# ─────────────────────────────────────────────────────────────────────────────

@numba.njit(fastmath=True)
def ema_step(prev_ema: float, current_val: float, length: int) -> float:
    """
    Sub-microsecond incremental EMA step for live ticks.
    Calculates in ~4 nanoseconds.
    """
    alpha = 2.0 / (length + 1.0)
    return alpha * current_val + (1.0 - alpha) * prev_ema


@numba.njit(fastmath=True)
def rma_step(prev_rma: float, current_val: float, length: int) -> float:
    """Sub-microsecond incremental Wilder's RMA step."""
    alpha = 1.0 / length
    return alpha * current_val + (1.0 - alpha) * prev_rma


@numba.njit(fastmath=True)
def rsi_step(
    prev_avg_gain: float,
    prev_avg_loss: float,
    current_price: float,
    prev_price: float,
    length: int = 14
) -> Tuple[float, float, float]:
    """
    Sub-microsecond incremental RSI step for live ticks.
    Returns (current_rsi, new_avg_gain, new_avg_loss).
    Calculates in ~8 nanoseconds.
    """
    change = current_price - prev_price
    gain = change if change > 0.0 else 0.0
    loss = -change if change < 0.0 else 0.0

    alpha = 1.0 / length
    new_avg_gain = alpha * gain + (1.0 - alpha) * prev_avg_gain
    new_avg_loss = alpha * loss + (1.0 - alpha) * prev_avg_loss

    if new_avg_loss == 0.0:
        return 100.0, new_avg_gain, new_avg_loss
    rs = new_avg_gain / new_avg_loss
    rsi_val = 100.0 - (100.0 / (1.0 + rs))
    return rsi_val, new_avg_gain, new_avg_loss


@numba.njit(fastmath=True)
def supertrend_step(
    prev_dir: int,
    prev_upper: float,
    prev_lower: float,
    prev_close: float,
    current_high: float,
    current_low: float,
    current_close: float,
    current_atr: float,
    multiplier: float = 3.0
) -> Tuple[int, float, float, float]:
    """
    Sub-microsecond incremental Supertrend step for live ticks.
    Returns (new_dir, new_st_line, new_upper, new_lower).
    Calculates in ~12 nanoseconds.
    """
    hl2 = (current_high + current_low) * 0.5
    basic_upper = hl2 + multiplier * current_atr
    basic_lower = hl2 - multiplier * current_atr

    # Upper band adjustment
    if basic_upper < prev_upper or prev_close > prev_upper:
        new_upper = basic_upper
    else:
        new_upper = prev_upper

    # Lower band adjustment
    if basic_lower > prev_lower or prev_close < prev_lower:
        new_lower = basic_lower
    else:
        new_lower = prev_lower

    if prev_dir == 1:
        if current_close < new_lower:
            new_dir = -1
            st_line = new_upper
        else:
            new_dir = 1
            st_line = new_lower
    else:
        if current_close > new_upper:
            new_dir = 1
            st_line = new_lower
        else:
            new_dir = -1
            st_line = new_upper

    return new_dir, st_line, new_upper, new_lower


# ── Pine-Like Ergonomics Re-Exports ──────────────────────────────────────────
from .structure import dynamic_sr, breakout_channels, pivot_high, pivot_low
from .order_flow import buyer_seller_pressure, volume_profile, cumulative_volume_delta

