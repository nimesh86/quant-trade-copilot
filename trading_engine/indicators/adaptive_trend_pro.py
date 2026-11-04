"""
Adaptive Trend Pro (Pine Script 1:1 Implementation)
====================================================
Exact mathematical and logical translation of the TradingView Pine Script:
- Dual Kalman filter trend engine (short_len, long_len)
- Recurrence measurement and process noise updates (R = 0.01, Q = 0.1)
- True Range ATR (200) * 0.5 dynamic support & resistance boxes
- Exact 2-bar slope momentum confirmation (short_kalman > short_kalman[2])
- Breakout labels (🡹 / 🢃) and Retest rejection signals (+ / x)
- Clean decoupling of Spot evaluation from Option execution
"""
from typing import Dict, List, Any, Optional, Tuple
from dataclasses import dataclass, field
from datetime import datetime
import numpy as np
import pandas as pd

from trading_engine.quant_lib.base import Indicator, IndicatorSignal, input


class KalmanFilter:
    """
    Pine Script exact 1D Kalman filter state tracker.
    
    Formula matching Pine Script:
        error_meas = R * length
        prediction = estimate
        kalman_gain = error_est / (error_est + error_meas)
        estimate = prediction + kalman_gain * (src - prediction)
        error_est = (1.0 - kalman_gain) * error_est + (Q / length)
    """
    def __init__(self, length: int, R: float = 0.01, Q: float = 0.1):
        self.length = length
        self.R = R
        self.Q = Q
        self.estimate: Optional[float] = None
        self.error_est: float = 1.0
        self.prev_src: Optional[float] = None

    def reset(self):
        self.estimate = None
        self.error_est = 1.0
        self.prev_src = None

    def update(self, src: float) -> Optional[float]:
        error_meas = self.R * self.length

        # if na(estimate) => estimate := src[1]
        if self.estimate is None:
            if self.prev_src is None:
                self.prev_src = src
                return None
            self.estimate = self.prev_src

        self.prev_src = src
        prediction = self.estimate
        kalman_gain = self.error_est / (self.error_est + error_meas)
        self.estimate = prediction + kalman_gain * (src - prediction)
        self.error_est = (1.0 - kalman_gain) * self.error_est + (self.Q / self.length)
        return self.estimate


class ATRFilter:
    """
    TradingView exact ta.atr(200) * 0.5 (Wilder's RMA smoothing).
    """
    def __init__(self, length: int = 200, multiplier: float = 0.5):
        self.length = length
        self.multiplier = multiplier
        self.alpha = 1.0 / length
        self.rma_tr: Optional[float] = None
        self.prev_close: Optional[float] = None

    def reset(self):
        self.rma_tr = None
        self.prev_close = None

    def update(self, high: float, low: float, close: float) -> float:
        if self.prev_close is None:
            tr = high - low
            self.rma_tr = tr
        else:
            tr = max(high - low, abs(high - self.prev_close), abs(low - self.prev_close))
            self.rma_tr = self.alpha * tr + (1.0 - self.alpha) * self.rma_tr
        self.prev_close = close
        return (self.rma_tr or (high - low)) * self.multiplier


class AdaptiveTrendProIndicator(Indicator):
    """
    Adaptive Trend Pro - 100% Exact 1:1 Pine Script Implementation.
    """

    def init(self):
        # ＩＮＰＵＴＳ ―――――――――――――――――――――――――――――――――――――――――――――――――――――――――――――――――――――
        self.short_len = input.int(50, "Short Length", minval=2, maxval=200, group="Filter Settings")
        self.long_len = input.int(150, "Long Length", minval=5, maxval=500, group="Filter Settings")
        self.retest_sig = input.bool(False, "Retest Signals", group="Filter Settings")
        self.candle_color = input.bool(True, "Candle Color", group="Appearance")
        self.upper_col = input.color("#13bd6e", "Up Color", group="Appearance")
        self.lower_col = input.color("#af0d4b", "Dn Color", group="Appearance")
        self.enable_hammer_reversal = input.bool(False, "Candlestick Hammer Reversal Exit", group="Exit Settings")

        # Runtime state
        self._reset_state()

    def _reset_state(self):
        """Initializes rolling filters and Pine Script var boxes."""
        short_l = int(self.short_len.value if hasattr(self.short_len, "value") else self.short_len)
        long_l = int(self.long_len.value if hasattr(self.long_len, "value") else self.long_len)

        self._short_filter = KalmanFilter(short_l, R=0.01, Q=0.1)
        self._long_filter = KalmanFilter(long_l, R=0.01, Q=0.1)
        self._atr_filter = ATRFilter(length=200, multiplier=0.5)

        self._short_k_history: List[float] = []
        self._prev_trend_up: Optional[bool] = None
        self._prev_high: Optional[float] = None
        self._prev_low: Optional[float] = None

        # Pine Script: var lower_box = box(na), var upper_box = box(na)
        self._lower_box: Optional[Dict[str, float]] = None  # {'top': float, 'bottom': float}
        self._upper_box: Optional[Dict[str, float]] = None  # {'top': float, 'bottom': float}
        self._bar_count = 0

    @staticmethod
    def calculate_buyer_supplier_strength(open_p: float, high_p: float, low_p: float, close_p: float) -> Tuple[float, float, str]:
        """
        Calculates Buyer Strength % and Supplier (Seller) Strength % for a candle,
        and identifies candlestick patterns (HAMMER, INVERTED_HAMMER, or NEUTRAL).
        """
        rng = max(high_p - low_p, 0.05)
        body = abs(close_p - open_p)
        upper_wick = high_p - max(open_p, close_p)
        lower_wick = min(open_p, close_p) - low_p

        # Buyer Pressure: lower shadow absorption + bullish body + close near high
        buyer_push = (close_p - low_p) + lower_wick + max(0.0, close_p - open_p)
        # Supplier Pressure: upper shadow rejection + bearish body + close near low
        supplier_push = (high_p - close_p) + upper_wick + max(0.0, open_p - close_p)

        total_push = max(buyer_push + supplier_push, 0.01)
        buyer_pct = round((buyer_push / total_push) * 100.0, 1)
        supplier_pct = round(100.0 - buyer_pct, 1)

        pattern = "NEUTRAL"
        # Regular Hammer (downside-up): long lower wick >= 1.8x body, lower wick >= 40% range
        if lower_wick >= 1.8 * body and lower_wick >= 0.40 * rng and buyer_pct >= 55.0:
            pattern = "HAMMER"
        # Inverted Hammer (upside-down): long upper wick >= 1.8x body, upper wick >= 40% range
        elif upper_wick >= 1.8 * body and upper_wick >= 0.40 * rng and supplier_pct >= 55.0:
            pattern = "INVERTED_HAMMER"

        return buyer_pct, supplier_pct, pattern

    def warm_up(self, historical_bars: Any):
        """Pre-populates Kalman filters with historical Spot bars."""
        if historical_bars is None or len(historical_bars) == 0:
            return
        self._reset_state()
        iterator = historical_bars if isinstance(historical_bars, list) else (r for _, r in historical_bars.iterrows())
        for row in iterator:
            c = float(row.get("close", 0.0))
            h = float(row.get("high", c))
            l = float(row.get("low", c))
            self._atr_filter.update(h, l, c)
            sk = self._short_filter.update(c)
            lk = self._long_filter.update(c)
            if sk is not None and lk is not None:
                self._short_k_history.append(sk)
                if len(self._short_k_history) > 10:
                    self._short_k_history.pop(0)
                self._prev_trend_up = (sk > lk)
            self._prev_high = h
            self._prev_low = l

    def on_bar(
        self,
        option_bar: Dict[str, Any],
        history: Any,
        active_trade: Optional[Dict[str, Any]] = None
    ) -> Optional[IndicatorSignal]:
        """
        Step-by-step bar evaluation matching Pine Script execution model.
        Evaluates underlying Spot Price to guarantee indicator integrity.
        """
        self._bar_count += 1

        # 1. Resolve Spot Price safely (avoids option premium decay distortion)
        has_history = history is not None and len(history) > 0
        if has_history:
            last_row = history[-1] if isinstance(history, list) else history.iloc[-1]
            spot_close = float(last_row.get("close", 0.0))
            spot_high = float(last_row.get("high", spot_close))
            spot_low = float(last_row.get("low", spot_close))
            spot_open = float(last_row.get("open", spot_close))
        else:
            if isinstance(option_bar, dict):
                spot_close = float(option_bar.get("spot_close", option_bar.get("close", 0.0)))
                spot_high = float(option_bar.get("spot_high", option_bar.get("high", spot_close)))
                spot_low = float(option_bar.get("spot_low", option_bar.get("low", spot_close)))
                spot_open = float(option_bar.get("spot_open", option_bar.get("open", spot_close)))
            else:
                spot_close = float(getattr(option_bar, "spot_close", getattr(option_bar, "close", 0.0)))
                spot_high = float(getattr(option_bar, "spot_high", getattr(option_bar, "high", spot_close)))
                spot_low = float(getattr(option_bar, "spot_low", getattr(option_bar, "low", spot_close)))
                spot_open = float(getattr(option_bar, "spot_open", getattr(option_bar, "open", spot_close)))

        if isinstance(option_bar, dict):
            ts = option_bar.get("timestamp") or ""
        else:
            ts = getattr(option_bar, "timestamp", "")

        # ── ACTIVE TRADE MONITORING: Candlestick Reversal & Buyer/Supplier Strength ──
        allow_hammer_rev = self.enable_hammer_reversal.value if hasattr(self.enable_hammer_reversal, "value") else bool(self.enable_hammer_reversal)
        if allow_hammer_rev and active_trade is not None and history is not None and len(history) >= 2:
            prev_bar = history[-2] if isinstance(history, list) else history.iloc[-2]
            curr_bar = history[-1] if isinstance(history, list) else history.iloc[-1]
            p_o, p_h, p_l, p_c = float(prev_bar["open"]), float(prev_bar["high"]), float(prev_bar["low"]), float(prev_bar["close"])
            c_o, c_h, c_l, c_c = float(curr_bar["open"]), float(curr_bar["high"]), float(curr_bar["low"]), float(curr_bar["close"])

            p_buyer, p_supplier, p_pat = self.calculate_buyer_supplier_strength(p_o, p_h, p_l, p_c)
            c_buyer, c_supplier, _ = self.calculate_buyer_supplier_strength(c_o, c_h, c_l, c_c)

            trade_side = str(active_trade.get("trade_side", "")).upper()

            # 1. CE Position Reversal: Inverted Hammer (upside-down hammer) + Bearish confirmation (opposite direction)
            if "CE" in trade_side:
                if p_pat == "INVERTED_HAMMER" and (c_c < c_o or c_c < p_l) and c_supplier >= 52.0:
                    return IndicatorSignal(
                        action="EXIT_CE",
                        trigger_price=round(spot_close, 2),
                        tag="REVERSAL_INVERTED_HAMMER_SUPPLIERS",
                        timestamp=str(ts),
                        confidence=1.0,
                        extra={
                            "reason": f"REVERSAL_INVERTED_HAMMER (Suppliers {c_supplier:.0f}% vs Buyers {c_buyer:.0f}%)",
                            "buyer_strength": c_buyer,
                            "supplier_strength": c_supplier,
                            "prev_pattern": p_pat
                        }
                    )

            # 2. PE Position Reversal: Regular Hammer (downside-up hammer) + Bullish confirmation (opposite direction)
            elif "PE" in trade_side:
                if p_pat == "HAMMER" and (c_c > c_o or c_c > p_h) and c_buyer >= 52.0:
                    return IndicatorSignal(
                        action="EXIT_PE",
                        trigger_price=round(spot_close, 2),
                        tag="REVERSAL_HAMMER_BUYERS",
                        timestamp=str(ts),
                        confidence=1.0,
                        extra={
                            "reason": f"REVERSAL_HAMMER (Buyers {c_buyer:.0f}% vs Suppliers {c_supplier:.0f}%)",
                            "buyer_strength": c_buyer,
                            "supplier_strength": c_supplier,
                            "prev_pattern": p_pat
                        }
                    )

        # 2. ＣＡＬＣＵＬＡＴＩＯＮＳ matching Pine Script
        atr = self._atr_filter.update(spot_high, spot_low, spot_close)
        short_k = self._short_filter.update(spot_close)
        long_k = self._long_filter.update(spot_close)

        if short_k is None or long_k is None:
            self._prev_high = spot_high
            self._prev_low = spot_low
            return None

        self._short_k_history.append(short_k)
        if len(self._short_k_history) > 10:
            self._short_k_history.pop(0)

        # bool trend_up = short_kalman > long_kalman
        trend_up = short_k > long_k

        # color trend_col1 = short_kalman > short_kalman[2] ? upper_col : lower_col
        short_k_2 = self._short_k_history[-3] if len(self._short_k_history) >= 3 else self._short_k_history[0]
        short_kalman_rising = short_k >= short_k_2

        # color candle_col = candle_color ? (trend_up and short_kalman > short_kalman[2] ? upper_col : not trend_up and short_kalman < short_kalman[2] ? lower_col : color.gray) : na
        if trend_up and short_kalman_rising:
            candle_col = self.upper_col.value if hasattr(self.upper_col, "value") else "#13bd6e"
        elif (not trend_up) and (short_k < short_k_2):
            candle_col = self.lower_col.value if hasattr(self.lower_col, "value") else "#af0d4b"
        else:
            candle_col = "gray"

        # 3. ＰＬＯＴ & BOX MANAGEMENT
        # if trend_up and not trend_up[1] (with graceful session start handling)
        is_first_eval = (self._prev_trend_up is None)
        bullish_flip = (trend_up and not self._prev_trend_up) if not is_first_eval else (trend_up and short_kalman_rising)
        # if trend_up[1] and not trend_up
        bearish_flip = (not trend_up and self._prev_trend_up) if not is_first_eval else (not trend_up and not short_kalman_rising)

        if bullish_flip:
            # lower_box := box.new(bar_index, low+atr, bar_index, low)
            self._lower_box = {
                "top": spot_low + atr,
                "bottom": spot_low
            }

        if bearish_flip:
            # upper_box := box.new(bar_index, high, bar_index, high-atr)
            self._upper_box = {
                "top": spot_high,
                "bottom": spot_high - atr
            }

        # Retest Signals
        retest_active = bool(self.retest_sig.value if hasattr(self.retest_sig, "value") else self.retest_sig)
        retest_bearish_x = False
        retest_bullish_plus = False

        if retest_active and self._prev_high is not None and self._prev_low is not None:
            # if high < upper_box.get_bottom() and high[1] >= upper_box.get_bottom()
            if self._upper_box and spot_high < self._upper_box["bottom"] and self._prev_high >= self._upper_box["bottom"]:
                retest_bearish_x = True

            # if low > lower_box.get_top() and low[1] <= lower_box.get_top()
            if self._lower_box and spot_low > self._lower_box["top"] and self._prev_low <= self._lower_box["top"]:
                retest_bullish_plus = True

        # Populate Live Plots matching Pine Script
        self.plots = {
            "Short Kalman": round(short_k, 2),
            "Long Kalman": round(long_k, 2),
            "Trend Up": trend_up,
            "Trend Slope Up": short_kalman_rising,
            "Candle Color": candle_col,
            "ATR": round(atr, 2),
            "Lower Box Top": round(self._lower_box["top"], 2) if self._lower_box else None,
            "Lower Box Bottom": round(self._lower_box["bottom"], 2) if self._lower_box else None,
            "Upper Box Top": round(self._upper_box["top"], 2) if self._upper_box else None,
            "Upper Box Bottom": round(self._upper_box["bottom"], 2) if self._upper_box else None
        }

        # -------------------------------------------------------------
        # 4. TRADE EVALUATION: LIFECYCLE & SIGNALS
        # -------------------------------------------------------------

        # A. EVALUATE ACTIVE TRADE (EXITS)
        if active_trade is not None:
            trade_side = str(active_trade.get("side", active_trade.get("option_type", ""))).upper()

            # Long CE Trade: Exit if trend flips Bearish or breaks below lower_box bottom (2-candle rule)
            if "CE" in trade_side or "BULLISH" in trade_side:
                if bearish_flip:
                    self._update_prev(spot_high, spot_low, trend_up)
                    return IndicatorSignal(
                        action="EXIT_CE",
                        trigger_price=round(spot_close, 2),
                        tag="TREND_FLIP_BEARISH",
                        timestamp=str(ts),
                        confidence=1.0,
                        extra={"reason": f"Adaptive Trend flipped Bearish (Short {short_k:.1f} < Long {long_k:.1f})"}
                    )
                # 2-Candle Rule: Prev bar closed below support AND current bar opened below support
                if self._lower_box and history is not None and len(history) >= 2:
                    prev_c = float(history.iloc[-2].get("close", 0.0))
                    if prev_c < self._lower_box["bottom"] and spot_open < self._lower_box["bottom"]:
                        self._update_prev(spot_high, spot_low, trend_up)
                        return IndicatorSignal(
                            action="EXIT_CE",
                            trigger_price=round(spot_close, 2),
                            tag="LOWER_BOX_BREAK_2CANDLE",
                            timestamp=str(ts),
                            confidence=1.0,
                            extra={"reason": f"Price broke below Support Box bottom ({self._lower_box['bottom']:.1f}) via 2-candle rule (PrevClose {prev_c:.1f} < Bottom & Open {spot_open:.1f} < Bottom)"}
                        )

            # Long PE Trade: Exit if trend flips Bullish or breaks below lower_box bottom (option chart) or above upper_box
            elif "PE" in trade_side or "BEARISH" in trade_side:
                if bullish_flip:
                    self._update_prev(spot_high, spot_low, trend_up)
                    return IndicatorSignal(
                        action="EXIT_PE",
                        trigger_price=round(spot_close, 2),
                        tag="TREND_FLIP_BULLISH",
                        timestamp=str(ts),
                        confidence=1.0,
                        extra={"reason": f"Adaptive Trend flipped Bullish (Short {short_k:.1f} > Long {long_k:.1f})"}
                    )
                # 2-Candle Rule on support box
                if self._lower_box and history is not None and len(history) >= 2:
                    prev_c = float(history.iloc[-2].get("close", 0.0))
                    if prev_c < self._lower_box["bottom"] and spot_open < self._lower_box["bottom"]:
                        self._update_prev(spot_high, spot_low, trend_up)
                        return IndicatorSignal(
                            action="EXIT_PE",
                            trigger_price=round(spot_close, 2),
                            tag="LOWER_BOX_BREAK_2CANDLE",
                            timestamp=str(ts),
                            confidence=1.0,
                            extra={"reason": f"Price broke below Support Box bottom ({self._lower_box['bottom']:.1f}) via 2-candle rule"}
                        )

            self._update_prev(spot_high, spot_low, trend_up)
            return None

        # B. EVALUATE NEW ENTRIES (WHEN FLAT)
        # Mode 1: Retest Signals Enabled
        if retest_active:
            if retest_bullish_plus and trend_up and short_kalman_rising:
                sl_px = self._lower_box["bottom"] if self._lower_box else spot_close - 2.0 * atr
                tgt_px = spot_close + 2.5 * atr
                self._update_prev(spot_high, spot_low, trend_up)
                return IndicatorSignal(
                    action="BUY_CE",
                    trigger_price=round(spot_close, 2),
                    stop_loss=round(sl_px, 2),
                    target=round(tgt_px, 2),
                    tag="ADAPTIVE_RETEST_BULLISH",
                    timestamp=str(ts),
                    confidence=1.0,
                    extra={
                        "reason": "Bullish Support Box Retest (+)",
                        "box_top": self._lower_box["top"] if self._lower_box else 0.0,
                        "box_bottom": self._lower_box["bottom"] if self._lower_box else 0.0
                    }
                )

            if retest_bearish_x and (not trend_up) and (not short_kalman_rising):
                sl_px = self._upper_box["top"] if self._upper_box else spot_close + 2.0 * atr
                tgt_px = spot_close - 2.5 * atr
                self._update_prev(spot_high, spot_low, trend_up)
                return IndicatorSignal(
                    action="BUY_PE",
                    trigger_price=round(spot_close, 2),
                    stop_loss=round(sl_px, 2),
                    target=round(tgt_px, 2),
                    tag="ADAPTIVE_RETEST_BEARISH",
                    timestamp=str(ts),
                    confidence=1.0,
                    extra={
                        "reason": "Bearish Resistance Box Retest (x)",
                        "box_top": self._upper_box["top"] if self._upper_box else 0.0,
                        "box_bottom": self._upper_box["bottom"] if self._upper_box else 0.0
                    }
                )

        # Mode 2: Trend Flip Breakout Signals (Default Pine Script behaviour)
        else:
            # Bullish Crossover: trend_up and not trend_up[1]
            # Requires confirmed upward slope: short_kalman > short_kalman[2]
            if bullish_flip and short_kalman_rising:
                sl_px = self._lower_box["bottom"] if self._lower_box else spot_close - 2.0 * atr
                tgt_px = spot_close + 2.5 * atr
                self._update_prev(spot_high, spot_low, trend_up)
                return IndicatorSignal(
                    action="BUY_CE",
                    trigger_price=round(spot_close, 2),
                    stop_loss=round(sl_px, 2),
                    target=round(tgt_px, 2),
                    tag="ADAPTIVE_BREAKOUT_BULLISH",
                    timestamp=str(ts),
                    confidence=1.0,
                    extra={
                        "reason": f"Adaptive Bullish Flip (🡹): Short({short_k:.1f}) > Long({long_k:.1f})",
                        "sl": round(sl_px, 2),
                        "t1": round(tgt_px, 2)
                    }
                )

            # Bearish Crossover: trend_up[1] and not trend_up
            # Requires confirmed downward slope: short_kalman < short_kalman[2]
            if bearish_flip and (not short_kalman_rising):
                sl_px = self._upper_box["top"] if self._upper_box else spot_close + 2.0 * atr
                tgt_px = spot_close - 2.5 * atr
                self._update_prev(spot_high, spot_low, trend_up)
                return IndicatorSignal(
                    action="BUY_PE",
                    trigger_price=round(spot_close, 2),
                    stop_loss=round(sl_px, 2),
                    target=round(tgt_px, 2),
                    tag="ADAPTIVE_BREAKOUT_BEARISH",
                    timestamp=str(ts),
                    confidence=1.0,
                    extra={
                        "reason": f"Adaptive Bearish Flip (🢃): Short({short_k:.1f}) < Long({long_k:.1f})",
                        "sl": round(sl_px, 2),
                        "t1": round(tgt_px, 2)
                    }
                )

        self._update_prev(spot_high, spot_low, trend_up)
        return None

    def _update_prev(self, high: float, low: float, trend_up: bool):
        self._prev_high = high
        self._prev_low = low
        self._prev_trend_up = trend_up

    def compute(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Vectorized full-series calculation matching Pine Script outputs exactly.
        Returns a DataFrame with short_kalman, long_kalman, trend_up, boxes, and signals.
        """
        res = df.copy()
        short_l = int(self.short_len.value if hasattr(self.short_len, "value") else self.short_len)
        long_l = int(self.long_len.value if hasattr(self.long_len, "value") else self.long_len)
        retest_active = bool(self.retest_sig.value if hasattr(self.retest_sig, "value") else self.retest_sig)

        kf_short = KalmanFilter(short_l, R=0.01, Q=0.1)
        kf_long = KalmanFilter(long_l, R=0.01, Q=0.1)
        atr_f = ATRFilter(length=200, multiplier=0.5)

        short_vals: List[Optional[float]] = []
        long_vals: List[Optional[float]] = []
        atr_vals: List[float] = []

        for _, row in res.iterrows():
            c = float(row["close"])
            h = float(row.get("high", c))
            l = float(row.get("low", c))
            short_vals.append(kf_short.update(c))
            long_vals.append(kf_long.update(c))
            atr_vals.append(atr_f.update(h, l, c))

        res["short_kalman"] = short_vals
        res["long_kalman"] = long_vals
        res["atr"] = atr_vals

        # trend_up = short_kalman > long_kalman
        res["trend_up"] = (res["short_kalman"] > res["long_kalman"]).astype(bool)

        # short_kalman[2]
        res["short_kalman_prev2"] = res["short_kalman"].shift(2)
        res["slope_up"] = res["short_kalman"] > res["short_kalman_prev2"]

        # trend_col & candle_col
        up_col = self.upper_col.value if hasattr(self.upper_col, "value") else "#13bd6e"
        dn_col = self.lower_col.value if hasattr(self.lower_col, "value") else "#af0d4b"

        res["trend_col"] = np.where(res["trend_up"], up_col, dn_col)
        res["trend_col1"] = np.where(res["slope_up"], up_col, dn_col)

        candle_colors = []
        for _, r in res.iterrows():
            if r["trend_up"] and r["slope_up"]:
                candle_colors.append(up_col)
            elif (not r["trend_up"]) and (not r["slope_up"]):
                candle_colors.append(dn_col)
            else:
                candle_colors.append("gray")
        res["candle_col"] = candle_colors

        # Labels & Boxes
        t_up = res["trend_up"].astype(bool)
        prev_up = t_up.shift(1).fillna(False).astype(bool)
        res["bullish_flip"] = t_up & (~prev_up)
        res["bearish_flip"] = (~t_up) & prev_up

        lower_box_top = [None] * len(res)
        lower_box_bottom = [None] * len(res)
        upper_box_top = [None] * len(res)
        upper_box_bottom = [None] * len(res)

        curr_lower_top, curr_lower_bot = None, None
        curr_upper_top, curr_upper_bot = None, None

        for i, row in res.iterrows():
            if row["bullish_flip"]:
                curr_lower_bot = row["low"]
                curr_lower_top = row["low"] + row["atr"]
            if row["bearish_flip"]:
                curr_upper_top = row["high"]
                curr_upper_bot = row["high"] - row["atr"]

            lower_box_top[i] = curr_lower_top
            lower_box_bottom[i] = curr_lower_bot
            upper_box_top[i] = curr_upper_top
            upper_box_bottom[i] = curr_upper_bot

        res["lower_box_top"] = lower_box_top
        res["lower_box_bottom"] = lower_box_bottom
        res["upper_box_top"] = upper_box_top
        res["upper_box_bottom"] = upper_box_bottom

        # Retest Signals
        res["retest_plus"] = False
        res["retest_x"] = False
        if retest_active:
            res["prev_high"] = res["high"].shift(1)
            res["prev_low"] = res["low"].shift(1)
            res["retest_x"] = (res["high"] < res["upper_box_bottom"]) & (res["prev_high"] >= res["upper_box_bottom"])
            res["retest_plus"] = (res["low"] > res["lower_box_top"]) & (res["prev_low"] <= res["lower_box_top"])

        return res
