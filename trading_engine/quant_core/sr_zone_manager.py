"""
Quant Core: Support & Resistance Zone Manager
─────────────────────────────────────────────
Implements dynamic Support and Resistance zone detection, multi-tier zone tracking,
and the strict 2-candle confirmation rule for zone breakouts and breakdowns:

2-Candle Rule Definition:
  - Support Broken: Candle 1 closes strictly below the support zone floor (Close < bottom)
    AND Candle 2 opens strictly below the support zone floor (Open < bottom).
  - Resistance Broken: Candle 1 closes strictly above the resistance zone ceiling (Close > top)
    AND Candle 2 opens strictly above the resistance zone ceiling (Open > top).

Multi-Tier Reversal Logic:
  - When price reaches an overhead Resistance zone and supplier pressure becomes active,
    the manager checks the closest support zone below current price.
  - If closest support is an intermediate support (e.g. 100):
      * Hold while testing 100.
      * If 100 breaks under the 2-candle rule:
          - If risk_factor <= 0.3 (conservative): Exit immediately to protect gains.
          - If risk_factor > 0.3 (moderate/aggressive): Hold until the Base Support (e.g. 80).
  - If closest support is the Base Support (e.g. 80 from entry):
      * Hold while testing 80.
      * If 80 breaks under the 2-candle rule: Immediate EXIT (structure invalidated).
"""
from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional, Tuple
import numpy as np
import pandas as pd


@dataclass
class SRZone:
    """Represents a dynamic price band for Support or Resistance."""
    id: str
    zone_type: str                   # "SUPPORT" or "RESISTANCE"
    top: float                       # Upper boundary of the price band
    bottom: float                    # Lower boundary of the price band
    mid: float                       # Midpoint price
    created_time: str
    touches: int = 1
    is_broken: bool = False
    broken_time: Optional[str] = None
    is_base_support: bool = False    # True if anchored to entry price

    def contains(self, price: float) -> bool:
        """Checks if a price is within the zone."""
        return self.bottom <= price <= self.top


class SRZoneManager:
    """
    Manages active Support and Resistance zones, polarity flips,
    and 2-candle breakdown confirmation.
    """

    def __init__(
        self,
        pivot_left: int = 2,
        pivot_right: int = 2,
        zone_atr_factor: float = 0.35,
        default_risk_factor: float = 0.5
    ):
        self.pivot_left = pivot_left
        self.pivot_right = pivot_right
        self.zone_atr_factor = zone_atr_factor
        self.default_risk_factor = default_risk_factor

        self.support_zones: List[SRZone] = []
        self.resistance_zones: List[SRZone] = []
        self._zone_counter = 0

        # State tracking for active trade
        self.active_base_support: Optional[SRZone] = None
        self.reversal_triggered_at_resistance: bool = False
        self.active_resistance_hit: Optional[SRZone] = None

    def reset(self):
        """Resets zones and active trade state."""
        self.support_zones.clear()
        self.resistance_zones.clear()
        self._zone_counter = 0
        self.active_base_support = None
        self.reversal_triggered_at_resistance = False
        self.active_resistance_hit = None

    def _next_id(self, prefix: str) -> str:
        self._zone_counter += 1
        return f"{prefix}_{self._zone_counter:03d}"

    @staticmethod
    def calculate_supplier_buyer_strength(open_p: float, high_p: float, low_p: float, close_p: float) -> Tuple[float, float]:
        """Calculates Buyer Strength % and Supplier (Seller) Strength %."""
        rng = max(high_p - low_p, 0.05)
        body = abs(close_p - open_p)
        upper_wick = high_p - max(open_p, close_p)
        lower_wick = min(open_p, close_p) - low_p

        buyer_push = (close_p - low_p) + lower_wick + max(0.0, close_p - open_p)
        supplier_push = (high_p - close_p) + upper_wick + max(0.0, open_p - close_p)

        total_push = max(buyer_push + supplier_push, 0.01)
        buyer_pct = round((buyer_push / total_push) * 100.0, 1)
        supplier_pct = round(100.0 - buyer_pct, 1)
        return buyer_pct, supplier_pct

    def check_support_broken_2candle(self, zone: SRZone, prev_bar: Dict[str, Any], curr_bar: Dict[str, Any]) -> bool:
        """
        2-Candle Rule: Support is broken if:
          1. Candle 1 closes strictly below the support floor (Close < bottom).
          2. Candle 2 opens strictly below the support floor (Open < bottom).
        """
        p_c = float(prev_bar.get("close", 0.0))
        c_o = float(curr_bar.get("open", 0.0))
        return (p_c < zone.bottom) and (c_o < zone.bottom)

    def check_resistance_broken_2candle(self, zone: SRZone, prev_bar: Dict[str, Any], curr_bar: Dict[str, Any]) -> bool:
        """
        2-Candle Rule: Resistance is broken if:
          1. Candle 1 closes strictly above the resistance ceiling (Close > top).
          2. Candle 2 opens strictly above the resistance ceiling (Open > top).
        """
        p_c = float(prev_bar.get("close", 0.0))
        c_o = float(curr_bar.get("open", 0.0))
        return (p_c > zone.top) and (c_o > zone.top)

    def update_from_bar(self, history: Any, curr_bar: Dict[str, Any], atr: float = 2.0):
        """
        Detects swing pivots, updates active zones, and handles polarity flips (broken resistance becomes support).
        Accepts either pd.DataFrame or List[Dict[str, Any]] for zero-overhead performance.
        """
        if history is None or len(history) < (self.pivot_left + self.pivot_right + 1):
            return

        c_ts = str(curr_bar.get("timestamp") or curr_bar.get("time") or "")
        p_idx = len(history) - 1 - self.pivot_right
        if p_idx < self.pivot_left:
            return

        if isinstance(history, list):
            p_row = history[p_idx]
            p_high = float(p_row["high"])
            p_low = float(p_row["low"])
            p_ts = str(p_row.get("timestamp") or p_row.get("time") or "")
            left_highs = [float(c["high"]) for c in history[p_idx - self.pivot_left: p_idx]]
            right_highs = [float(c["high"]) for c in history[p_idx + 1: p_idx + 1 + self.pivot_right]]
            left_lows = [float(c["low"]) for c in history[p_idx - self.pivot_left: p_idx]]
            right_lows = [float(c["low"]) for c in history[p_idx + 1: p_idx + 1 + self.pivot_right]]
        else:
            p_row = history.iloc[p_idx]
            p_high = float(p_row["high"])
            p_low = float(p_row["low"])
            p_ts = str(p_row.get("timestamp") or p_row.get("time") or "")
            left_highs = history["high"].iloc[p_idx - self.pivot_left: p_idx].values
            right_highs = history["high"].iloc[p_idx + 1: p_idx + 1 + self.pivot_right].values
            left_lows = history["low"].iloc[p_idx - self.pivot_left: p_idx].values
            right_lows = history["low"].iloc[p_idx + 1: p_idx + 1 + self.pivot_right].values

        band_width = max(atr * self.zone_atr_factor, 0.35)

        # 1. Detect Pivot High -> Resistance Zone
        if (len(left_highs) > 0 and p_high > np.max(left_highs)) and (len(right_highs) > 0 and p_high >= np.max(right_highs)):
            r_top = round(p_high, 2)
            r_bot = round(max(0.05, p_high - band_width), 2)
            # Check if redundant with existing active resistance zone
            overlap = any(rz.contains(p_high) or abs(rz.mid - p_high) < band_width for rz in self.resistance_zones if not rz.is_broken)
            if not overlap:
                self.resistance_zones.append(SRZone(
                    id=self._next_id("RES"),
                    zone_type="RESISTANCE",
                    top=r_top,
                    bottom=r_bot,
                    mid=round((r_top + r_bot) / 2.0, 2),
                    created_time=p_ts
                ))

        # 2. Detect Pivot Low -> Support Zone
        if (len(left_lows) > 0 and p_low < np.min(left_lows)) and (len(right_lows) > 0 and p_low <= np.min(right_lows)):
            s_bot = round(p_low, 2)
            s_top = round(p_low + band_width, 2)
            overlap = any(sz.contains(p_low) or abs(sz.mid - p_low) < band_width for sz in self.support_zones if not sz.is_broken)
            if not overlap:
                self.support_zones.append(SRZone(
                    id=self._next_id("SUP"),
                    zone_type="SUPPORT",
                    top=s_top,
                    bottom=s_bot,
                    mid=round((s_top + s_bot) / 2.0, 2),
                    created_time=p_ts
                ))

        # 3. Check for Resistance Breakout (2-candle confirmation) -> Polarity Flip to Support
        if len(history) >= 2:
            prev_bar = history[-2] if isinstance(history, list) else history.iloc[-2].to_dict()
            for rz in self.resistance_zones:
                if not rz.is_broken and self.check_resistance_broken_2candle(rz, prev_bar, curr_bar):
                    rz.is_broken = True
                    rz.broken_time = c_ts
                    # Polarity Flip: Broken resistance becomes a Support Zone
                    flipped_sup = SRZone(
                        id=self._next_id("SUP_FLIP"),
                        zone_type="SUPPORT",
                        top=rz.top,
                        bottom=rz.bottom,
                        mid=rz.mid,
                        created_time=c_ts
                    )
                    self.support_zones.append(flipped_sup)

            # Check for Support Breakdown (2-candle confirmation)
            for sz in self.support_zones:
                if not sz.is_broken and self.check_support_broken_2candle(sz, prev_bar, curr_bar):
                    sz.is_broken = True
                    sz.broken_time = c_ts

    def on_trade_entry(self, entry_price: float, timestamp: str, atr: float = 2.0, history: Optional[pd.DataFrame] = None):
        """
        Initializes Base Support anchored to the entry setup swing low or nearest active support.
        """
        band = max(atr * self.zone_atr_factor, 0.5)

        # 1. Look for active unbroken support zones below entry price
        active_sups = [sz for sz in self.support_zones if not sz.is_broken and sz.top <= entry_price + 0.5]
        if active_sups:
            active_sups.sort(key=lambda s: abs(entry_price - s.mid))
            self.active_base_support = active_sups[0]
            self.active_base_support.is_base_support = True
        else:
            # 2. Derive Base Support from the setup swing low in recent bars
            if history is not None and len(history) >= 3:
                if isinstance(history, list):
                    recent_low = float(min(float(c.get("low", 0.0)) for c in history[-5:]))
                else:
                    recent_low = float(history["low"].iloc[-5:].min())
                if recent_low < entry_price:
                    base_bot = max(0.05, round(recent_low, 2))
                    base_top = round(min(entry_price, recent_low + band), 2)
                else:
                    buffer = max(atr * 1.5, entry_price * 0.08, 1.0)
                    base_bot = max(0.05, round(entry_price - buffer, 2))
                    base_top = round(entry_price, 2)
            else:
                buffer = max(atr * 1.5, entry_price * 0.08, 1.0)
                base_bot = max(0.05, round(entry_price - buffer, 2))
                base_top = round(entry_price, 2)

            self.active_base_support = SRZone(
                id=self._next_id("BASE_SUP"),
                zone_type="SUPPORT",
                top=base_top,
                bottom=base_bot,
                mid=round((base_top + base_bot) / 2.0, 2),
                created_time=timestamp,
                is_base_support=True
            )
            self.support_zones.append(self.active_base_support)

        self.reversal_triggered_at_resistance = False
        self.active_resistance_hit = None

    def evaluate_active_trade(
        self,
        active_trade: Dict[str, Any],
        history: Any,
        curr_bar: Dict[str, Any],
        risk_factor: float = 0.5,
        atr: float = 2.0
    ) -> Tuple[bool, str, Dict[str, Any]]:
        """
        Evaluates active position against S/R levels and the 2-candle breakdown rule.
        Returns: (should_exit: bool, exit_reason: str, diagnostics: dict)
        """
        if history is None or len(history) < 2:
            return False, "", {}

        prev_bar = history[-2] if isinstance(history, list) else history.iloc[-2].to_dict()
        c_o = float(curr_bar.get("open", 0.0))
        c_h = float(curr_bar.get("high", 0.0))
        c_l = float(curr_bar.get("low", 0.0))
        c_c = float(curr_bar.get("close", 0.0))

        entry_px = float(active_trade.get("entry_price", c_c))

        # Ensure base support is initialized
        if self.active_base_support is None:
            self.on_trade_entry(entry_px, str(curr_bar.get("timestamp") or curr_bar.get("time") or ""), atr)

        buyer_pct, supplier_pct = self.calculate_supplier_buyer_strength(c_o, c_h, c_l, c_c)

        # ── Step 1: Resistance Interaction & Supplier Activation ──
        # Check active resistance zones above entry
        active_res = [rz for rz in self.resistance_zones if not rz.is_broken and rz.top >= entry_px]
        for rz in active_res:
            # Did current or previous bar hit or test resistance?
            if c_h >= rz.bottom or float(prev_bar.get("high", 0.0)) >= rz.bottom:
                # Did price stall / reject at resistance with active suppliers?
                stalled_at_res = (c_c <= rz.top)
                suppliers_active = (supplier_pct >= 50.0) or (c_h - max(c_o, c_c) >= 0.35 * (c_h - c_l))
                if stalled_at_res and suppliers_active:
                    self.reversal_triggered_at_resistance = True
                    self.active_resistance_hit = rz
                    break

        # ── Step 2: Closest Support Zone Navigation ──
        # Find all active, unbroken support zones below current price / resistance hit
        available_sups = [sz for sz in self.support_zones if not sz.is_broken and sz.top <= max(c_c, c_o, entry_px) + 0.5]
        # Sort descending by mid-price (closest support first)
        available_sups.sort(key=lambda s: s.mid, reverse=True)

        closest_support = available_sups[0] if available_sups else self.active_base_support

        diagnostics = {
            "supplier_pct": supplier_pct,
            "buyer_pct": buyer_pct,
            "reversal_active": self.reversal_triggered_at_resistance,
            "resistance_hit": self.active_resistance_hit.mid if self.active_resistance_hit else None,
            "closest_support": closest_support.mid if closest_support else None,
            "base_support": self.active_base_support.mid if self.active_base_support else None
        }

        # ── Step 3: Support Defense & 2-Candle Breakdown Check ──
        # Check if closest support is broken under 2-candle rule
        if closest_support is not None:
            is_closest_broken = self.check_support_broken_2candle(closest_support, prev_bar, curr_bar)

            # Case A: Closest support is the BASE SUPPORT (Entry Support)
            if closest_support.id == self.active_base_support.id or closest_support.is_base_support:
                if is_closest_broken:
                    reason = (
                        f"SR_BASE_SUPPORT_BROKEN (Base Support {closest_support.bottom:.2f} broken via 2-candle rule: "
                        f"PrevClose {prev_bar.get('close'):.2f} < {closest_support.bottom:.2f} & CurrOpen {c_o:.2f} < {closest_support.bottom:.2f})"
                    )
                    return True, reason, diagnostics
                else:
                    # Still inside or testing Base Support -> HOLD!
                    return False, "", diagnostics

            # Case B: Closest support is an INTERMEDIATE SUPPORT (e.g. 100 while Base Support is 80)
            else:
                if is_closest_broken:
                    closest_support.is_broken = True
                    # Check user's Risk Factor (0.0 to 1.0)
                    if risk_factor <= 0.3:
                        reason = (
                            f"SR_INTERMEDIATE_SUPPORT_BROKEN (Intermediate Support {closest_support.bottom:.2f} broken, "
                            f"Risk Factor {risk_factor:.1f} <= 0.3 conservative exit)"
                        )
                        return True, reason, diagnostics
                    else:
                        # Risk factor is higher (wait and hold until Base Support)
                        # Now check if Base Support is ALSO broken
                        if self.check_support_broken_2candle(self.active_base_support, prev_bar, curr_bar):
                            reason = (
                                f"SR_BASE_SUPPORT_BROKEN (After intermediate breakdown, Base Support "
                                f"{self.active_base_support.bottom:.2f} broken via 2-candle rule)"
                            )
                            return True, reason, diagnostics
                        else:
                            # Hold while testing Base Support
                            return False, "", diagnostics
                else:
                    # Intermediate support holds -> HOLD!
                    return False, "", diagnostics

        # Always check base support as the ultimate fallback line of defense
        if self.active_base_support and self.check_support_broken_2candle(self.active_base_support, prev_bar, curr_bar):
            reason = f"SR_BASE_SUPPORT_BROKEN ({self.active_base_support.bottom:.2f} broken via 2-candle rule)"
            return True, reason, diagnostics

        return False, "", diagnostics

    def evaluate_sr_entry_eligibility(
        self,
        current_price: float,
        history: pd.DataFrame,
        curr_bar: Dict[str, Any],
        allow_breakout: bool = True
    ) -> Tuple[bool, float, Dict[str, Any]]:
        """
        Pre-Entry S/R Proximity Center Zone (f <= 0) Rule:
        ─────────────────────────────────────────────────
        Evaluates whether price is in the Support/Discount half of the S/R channel.
        - Nearest Resistance: bottom of nearest unbroken resistance zone above current_price.
        - Nearest Support: top of nearest unbroken support zone below current_price.
        - Center Zone M = (R_floor + S_ceil) / 2.0.
        - Half-width W = (R_floor - S_ceil) / 2.0.
        - f = (current_price - M) / W.

        Execution Rules:
          - If no overhead resistance -> True (blue sky trend continuation).
          - If f <= 0 -> True (favorable R:R in support half).
          - If f > 0 -> False (too close to resistance) unless 2-candle breakout confirmed.
        """
        curr_px = float(current_price)
        if len(history) >= 2:
            prev_row = history[-2] if isinstance(history, list) else history.iloc[-2]
            prev_px = float(prev_row["close"])
        else:
            prev_px = curr_px

        # Unbroken resistance zones strictly above current price
        overhead_res = [rz for rz in self.resistance_zones if not rz.is_broken and rz.bottom > curr_px]
        # Unbroken support zones strictly below current price
        active_sup = [sz for sz in self.support_zones if not sz.is_broken and sz.top < curr_px]

        # If no overhead resistance zone exists -> Blue sky trend -> ALLOW
        if not overhead_res:
            return True, -1.0, {"rule": "BLUE_SKY_NO_RESISTANCE", "f_val": -1.0}

        overhead_res.sort(key=lambda r: r.bottom)
        nearest_res = overhead_res[0]
        r_floor = nearest_res.bottom

        # If no support zone exists -> ALLOW
        if not active_sup:
            return True, -1.0, {"rule": "NO_ESTABLISHED_SUPPORT", "f_val": -1.0}

        active_sup.sort(key=lambda s: s.top, reverse=True)
        nearest_sup = active_sup[0]
        s_ceil = nearest_sup.top

        if r_floor <= s_ceil:
            return True, 0.0, {"rule": "OVERLAPPING", "f_val": 0.0}

        half_w = (r_floor - s_ceil) / 2.0
        center = s_ceil + half_w
        f_val = (curr_px - center) / half_w if half_w > 0 else 0.0

        if f_val <= 0.0:
            return True, f_val, {
                "rule": "ALLOWED_SUPPORT_HALF",
                "f_val": round(f_val, 2),
                "r_floor": r_floor,
                "s_ceil": s_ceil,
                "center": round(center, 2)
            }
        else:
            # If f > 0, check if 2-candle breakout confirmed above resistance
            if allow_breakout and curr_px > nearest_res.top and prev_px > nearest_res.top:
                return True, f_val, {
                    "rule": "ALLOWED_BREAKOUT",
                    "f_val": round(f_val, 2),
                    "r_top": nearest_res.top
                }
            return False, f_val, {
                "rule": "BLOCKED_NEAR_RESISTANCE",
                "f_val": round(f_val, 2),
                "r_floor": r_floor,
                "s_ceil": s_ceil,
                "center": round(center, 2)
            }
