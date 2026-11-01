"""
Order Flow & Volume Pressure Analytics for quant_lib.
Calculates intrabar Buyer/Seller volume pressure, Volume Profile (POC, VAH, VAL), and Cumulative Volume Delta.
"""
from dataclasses import dataclass, field
from typing import List, Dict, Any, Union, Optional
import numpy as np


@dataclass
class BuyerSellerPressureResult:
    """Output container for buyer vs. seller volume pressure."""
    buyer_pct: float
    seller_pct: float
    dominant_side: str            # "BUYER", "SELLER", "NEUTRAL"
    buyer_volume: float
    seller_volume: float
    buyer_volume_series: np.ndarray = field(default_factory=lambda: np.array([]))
    seller_volume_series: np.ndarray = field(default_factory=lambda: np.array([]))


@dataclass
class VolumeProfileResult:
    """Output container for volume profile horizontal distribution."""
    poc: float                    # Point of Control (Highest volume node)
    vah: float                    # Value Area High (70% volume bracket)
    val: float                    # Value Area Low (70% volume bracket)
    total_volume: float
    price_levels: np.ndarray = field(default_factory=lambda: np.array([]))
    volume_nodes: np.ndarray = field(default_factory=lambda: np.array([]))


def buyer_seller_pressure(
    open_: Union[np.ndarray, list],
    high: Union[np.ndarray, list],
    low: Union[np.ndarray, list],
    close: Union[np.ndarray, list],
    volume: Union[np.ndarray, list],
    lookback: int = 14
) -> BuyerSellerPressureResult:
    """
    Computes Buyer vs. Seller volume pressure based on intrabar close position within high-low range.
    Buyer Ratio = (Close - Low) / (High - Low)
    Seller Ratio = (High - Close) / (High - Low)
    Sum of Buyer Volume and Seller Volume equals Total Volume on every bar.
    """
    op = np.asarray(open_, dtype=np.float64)
    hi = np.asarray(high, dtype=np.float64)
    lo = np.asarray(low, dtype=np.float64)
    cl = np.asarray(close, dtype=np.float64)
    vol = np.asarray(volume, dtype=np.float64)
    n = len(cl)

    if n == 0:
        return BuyerSellerPressureResult(50.0, 50.0, "NEUTRAL", 0.0, 0.0)

    hl = hi - lo
    # Where high == low (flat candle), split 50/50
    buyer_ratio = np.where(hl > 0, (cl - lo) / hl, 0.5)
    seller_ratio = 1.0 - buyer_ratio

    buyer_vol_series = vol * buyer_ratio
    seller_vol_series = vol * seller_ratio

    # Rolling window sum over lookback bars
    window_len = min(n, max(1, lookback))
    buyer_vol_sum = float(np.sum(buyer_vol_series[-window_len:]))
    seller_vol_sum = float(np.sum(seller_vol_series[-window_len:]))
    total_vol_sum = buyer_vol_sum + seller_vol_sum

    if total_vol_sum > 0:
        buyer_pct = round((buyer_vol_sum / total_vol_sum) * 100.0, 2)
        seller_pct = round(100.0 - buyer_pct, 2)
    else:
        buyer_pct = 50.0
        seller_pct = 50.0

    if buyer_pct > 50.0:
        dominant = "BUYER"
    elif seller_pct > 50.0:
        dominant = "SELLER"
    else:
        dominant = "NEUTRAL"

    return BuyerSellerPressureResult(
        buyer_pct=buyer_pct,
        seller_pct=seller_pct,
        dominant_side=dominant,
        buyer_volume=round(buyer_vol_sum, 2),
        seller_volume=round(seller_vol_sum, 2),
        buyer_volume_series=buyer_vol_series,
        seller_volume_series=seller_vol_series
    )


def volume_profile(
    close: Union[np.ndarray, list],
    volume: Union[np.ndarray, list],
    num_bins: int = 24,
    value_area_pct: float = 0.70
) -> VolumeProfileResult:
    """
    Computes horizontal Volume Profile:
    - Point of Control (POC): Price level with maximum traded volume.
    - Value Area High (VAH): Upper boundary encompassing 70% of total volume around POC.
    - Value Area Low (VAL): Lower boundary encompassing 70% of total volume around POC.
    """
    c = np.asarray(close, dtype=np.float64)
    v = np.asarray(volume, dtype=np.float64)
    if len(c) == 0:
        return VolumeProfileResult(0.0, 0.0, 0.0, 0.0)

    min_price = np.min(c)
    max_price = np.max(c)
    if min_price == max_price:
        return VolumeProfileResult(float(min_price), float(min_price), float(min_price), float(np.sum(v)))

    # Compute histogram bins
    vol_hist, bin_edges = np.histogram(c, bins=num_bins, weights=v)
    bin_centers = (bin_edges[:-1] + bin_edges[1:]) * 0.5

    # POC is index of maximum volume node
    poc_idx = int(np.argmax(vol_hist))
    poc_price = float(bin_centers[poc_idx])
    total_vol = float(np.sum(vol_hist))

    # Calculate Value Area (70% volume expanding outwards from POC)
    target_vol = total_vol * value_area_pct
    current_vol = vol_hist[poc_idx]
    upper_idx = poc_idx
    lower_idx = poc_idx

    while current_vol < target_vol and (upper_idx < num_bins - 1 or lower_idx > 0):
        next_upper_vol = vol_hist[upper_idx + 1] if upper_idx < num_bins - 1 else 0.0
        next_lower_vol = vol_hist[lower_idx - 1] if lower_idx > 0 else 0.0

        if next_upper_vol >= next_lower_vol and upper_idx < num_bins - 1:
            upper_idx += 1
            current_vol += next_upper_vol
        elif lower_idx > 0:
            lower_idx -= 1
            current_vol += next_lower_vol
        else:
            if upper_idx < num_bins - 1:
                upper_idx += 1
                current_vol += next_upper_vol
            else:
                break

    vah_price = float(bin_centers[upper_idx])
    val_price = float(bin_centers[lower_idx])

    return VolumeProfileResult(
        poc=round(poc_price, 2),
        vah=round(vah_price, 2),
        val=round(val_price, 2),
        total_volume=round(total_vol, 2),
        price_levels=bin_centers,
        volume_nodes=vol_hist
    )


def cumulative_volume_delta(
    close: Union[np.ndarray, list],
    volume: Union[np.ndarray, list]
) -> np.ndarray:
    """
    Cumulative Volume Delta (CVD).
    Positive delta on bars closing higher, negative delta on bars closing lower.
    """
    c = np.asarray(close, dtype=np.float64)
    v = np.asarray(volume, dtype=np.float64)
    n = len(c)
    if n <= 1:
        return np.zeros(n, dtype=np.float64)

    diff = np.diff(c)
    deltas = np.where(diff > 0, v[1:], np.where(diff < 0, -v[1:], 0.0))
    cvd = np.cumsum(np.concatenate(([0.0], deltas)))
    return cvd
