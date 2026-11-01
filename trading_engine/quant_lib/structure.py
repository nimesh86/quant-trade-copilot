"""
Market Structure & Dynamic Support/Resistance for quant_lib.
Implements swing pivot detection, candle-body clustering (ignoring wick sweeps), and breakout channels.
"""
from dataclasses import dataclass, field
from typing import List, Optional, Tuple, Union
import numpy as np
from .ta import atr


@dataclass
class DynamicSRResult:
    """Output container for dynamic support & resistance levels."""
    resistance_levels: List[float] = field(default_factory=list)
    support_levels: List[float] = field(default_factory=list)
    nearest_resistance: Optional[float] = None
    nearest_support: Optional[float] = None
    resistance_distance_pts: float = 0.0
    support_distance_pts: float = 0.0


@dataclass
class BreakoutChannelResult:
    """Output container for consolidation box breakout channels."""
    top: float = 0.0
    bottom: float = 0.0
    midline: float = 0.0
    in_consolidation: bool = False
    duration_bars: int = 0
    breakout_up: bool = False
    breakout_down: bool = False

    @property
    def upper(self) -> float:
        return self.top

    @property
    def lower(self) -> float:
        return self.bottom


def pivot_high(high: Union[np.ndarray, list], left: int = 4, right: int = 4) -> np.ndarray:
    """
    Identifies fractal swing pivot highs.
    Returns array where peak bars contain High value, others contain NaN.
    """
    h = np.asarray(high, dtype=np.float64)
    n = len(h)
    out = np.full(n, np.nan, dtype=np.float64)
    if n < left + right + 1:
        return out

    for i in range(left, n - right):
        val = h[i]
        is_pivot = True
        for j in range(1, left + 1):
            if h[i - j] >= val:
                is_pivot = False
                break
        if is_pivot:
            for j in range(1, right + 1):
                if h[i + j] > val:
                    is_pivot = False
                    break
        if is_pivot:
            out[i] = val
    return out


def pivot_low(low: Union[np.ndarray, list], left: int = 4, right: int = 4) -> np.ndarray:
    """
    Identifies fractal swing pivot lows.
    Returns array where trough bars contain Low value, others contain NaN.
    """
    l = np.asarray(low, dtype=np.float64)
    n = len(l)
    out = np.full(n, np.nan, dtype=np.float64)
    if n < left + right + 1:
        return out

    for i in range(left, n - right):
        val = l[i]
        is_pivot = True
        for j in range(1, left + 1):
            if l[i - j] <= val:
                is_pivot = False
                break
        if is_pivot:
            for j in range(1, right + 1):
                if l[i + j] < val:
                    is_pivot = False
                    break
        if is_pivot:
            out[i] = val
    return out


def dynamic_sr(
    open_: Union[np.ndarray, list],
    high: Union[np.ndarray, list],
    low: Union[np.ndarray, list],
    close: Union[np.ndarray, list],
    left: int = 4,
    right: int = 4,
    max_levels: int = 5,
    cluster_threshold_atr: float = 0.3
) -> DynamicSRResult:
    """
    Calculates dynamic support and resistance using candle-body pivot clustering.
    Uses max(Open, Close) for resistance pivots and min(Open, Close) for support pivots,
    effectively eliminating false wick sweeps and noise.
    Clusters adjacent levels within cluster_threshold_atr * ATR into unified price barriers.
    """
    op = np.asarray(open_, dtype=np.float64)
    hi = np.asarray(high, dtype=np.float64)
    lo = np.asarray(low, dtype=np.float64)
    cl = np.asarray(close, dtype=np.float64)
    n = len(cl)

    if n < left + right + 1:
        return DynamicSRResult()

    body_top = np.maximum(op, cl)
    body_bottom = np.minimum(op, cl)
    cur_price = cl[-1]

    # Calculate ATR for adaptive clustering threshold
    atr_vals = atr(hi, lo, cl, 14)
    cur_atr = atr_vals[-1] if len(atr_vals) > 0 and not np.isnan(atr_vals[-1]) else 10.0
    thresh = max(1.0, float(cur_atr) * cluster_threshold_atr)

    # 1. Detect Raw Body Pivots
    raw_res = []
    raw_sup = []

    for i in range(left, n - right):
        top_val = body_top[i]
        bot_val = body_bottom[i]

        # Check resistance pivot on candle body top
        is_res = True
        for j in range(1, left + 1):
            if body_top[i - j] >= top_val:
                is_res = False
                break
        if is_res:
            for j in range(1, right + 1):
                if body_top[i + j] > top_val:
                    is_res = False
                    break
        if is_res:
            raw_res.append(top_val)

        # Check support pivot on candle body bottom
        is_sup = True
        for j in range(1, left + 1):
            if body_bottom[i - j] <= bot_val:
                is_sup = False
                break
        if is_sup:
            for j in range(1, right + 1):
                if body_bottom[i + j] < bot_val:
                    is_sup = False
                    break
        if is_sup:
            raw_sup.append(bot_val)

    # 2. Cluster Levels within Threshold
    def cluster_levels(levels: List[float]) -> List[float]:
        if not levels:
            return []
        sorted_lvls = sorted(levels)
        clusters: List[List[float]] = [[sorted_lvls[0]]]
        for lvl in sorted_lvls[1:]:
            if lvl - clusters[-1][-1] <= thresh:
                clusters[-1].append(lvl)
            else:
                clusters.append([lvl])
        # Average each cluster
        return [round(float(np.mean(c)), 2) for c in clusters]

    clustered_res = cluster_levels(raw_res)[-max_levels:]
    clustered_sup = cluster_levels(raw_sup)[-max_levels:]

    # 3. Find Nearest Resistance above Current Price
    res_above = [r for r in clustered_res if r > cur_price]
    nearest_res = min(res_above) if res_above else (max(clustered_res) if clustered_res else None)

    # 4. Find Nearest Support below Current Price
    sup_below = [s for s in clustered_sup if s < cur_price]
    nearest_sup = max(sup_below) if sup_below else (min(clustered_sup) if clustered_sup else None)

    res_dist = round(nearest_res - cur_price, 2) if nearest_res is not None else 0.0
    sup_dist = round(cur_price - nearest_sup, 2) if nearest_sup is not None else 0.0

    return DynamicSRResult(
        resistance_levels=clustered_res,
        support_levels=clustered_sup,
        nearest_resistance=nearest_res,
        nearest_support=nearest_sup,
        resistance_distance_pts=res_dist,
        support_distance_pts=sup_dist
    )


def breakout_channels(
    open_: Union[np.ndarray, list],
    high: Union[np.ndarray, list],
    low: Union[np.ndarray, list],
    close: Union[np.ndarray, list],
    volume: Optional[Union[np.ndarray, list]] = None,
    norm_len: int = 100,
    box_len: int = 14,
    min_duration: int = 5
) -> BreakoutChannelResult:
    """
    Detects consolidation channels and strong breakouts.
    Breakout occurs when > 50% of the candle body closes outside channel boundaries.
    """
    op = np.asarray(open_, dtype=np.float64)
    hi = np.asarray(high, dtype=np.float64)
    lo = np.asarray(low, dtype=np.float64)
    cl = np.asarray(close, dtype=np.float64)
    n = len(cl)

    if n < max(box_len, min_duration):
        return BreakoutChannelResult()

    # Channel boundary over preceding box_len bars (excluding the breakout bar itself)
    eval_slice_hi = hi[-box_len-1:-1] if n > box_len else hi[:-1]
    eval_slice_lo = lo[-box_len-1:-1] if n > box_len else lo[:-1]
    recent_hi = np.max(eval_slice_hi) if len(eval_slice_hi) > 0 else hi[-1]
    recent_lo = np.min(eval_slice_lo) if len(eval_slice_lo) > 0 else lo[-1]
    midline = (recent_hi + recent_lo) * 0.5

    # Check 50% body breakout on latest bar
    cur_open = op[-1]
    cur_close = cl[-1]
    body_high = max(cur_open, cur_close)
    body_low = min(cur_open, cur_close)
    body_size = max(0.1, body_high - body_low)

    # Breakout Up: Body mostly above channel top
    breakout_up = False
    if cur_close > recent_hi:
        outside_span = cur_close - max(cur_open, recent_hi)
        if outside_span / body_size >= 0.5:
            breakout_up = True

    # Breakout Down: Body mostly below channel bottom
    breakout_down = False
    if cur_close < recent_lo:
        outside_span = min(cur_open, recent_lo) - cur_close
        if outside_span / body_size >= 0.5:
            breakout_down = True

    return BreakoutChannelResult(
        top=round(float(recent_hi), 2),
        bottom=round(float(recent_lo), 2),
        midline=round(float(midline), 2),
        in_consolidation=(recent_hi - recent_lo) > 0,
        duration_bars=box_len,
        breakout_up=breakout_up,
        breakout_down=breakout_down
    )
