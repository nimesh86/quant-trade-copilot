"""
BSS Pro v6 - Active Development & Strategy Enhancement.
=============================================================================
Forked from Frozen Golden Baseline (BSS Pro v5).
Target Enhancements:
1. Cooldown Period (4-5 bars) after SMART_EXIT_DISTRIBUTION to prevent instant re-entry traps.
2. Initial Stop Loss refinement to avoid premature shakeouts on pre-rally institutional noise.
=============================================================================
"""
from typing import Dict, Any, Optional, Tuple
from datetime import datetime
import numpy as np
import pandas as pd

from trading_engine.quant_lib.base import Indicator, IndicatorSignal, input
from trading_engine.quant_lib import ta


class BSSProV6Indicator(Indicator):
    """
    BSS Pro v6 - Enhanced Institutional Multi-Factor Scalping Indicator.
    Active development version.
    """

    def init(self):
        # ── Group 0: Mode & Signal Source ──────────────────────────────────────
        self.trade_mode = input.string("Option buyer (long premium only)", "Trade Mode", options=["Option buyer (long premium only)", "Normal"], group="0. Mode")
        self.use_source = input.bool(True, "Take signals from Spot Index", group="0. Mode", tooltip="Entries calculated on Spot Index; execution on Option Strike")

        # ── Group 1: Trend & Signal ───────────────────────────────────────────
        self.ema_fast_len = input.int(21, "Fast EMA Length", minval=2, group="1. Trend")
        self.ema_slow_len = input.int(50, "Slow EMA Length", minval=5, group="1. Trend")
        self.rsi_len      = input.int(14, "RSI Length", minval=2, group="1. Trend")
        self.st_len       = input.int(10, "Supertrend ATR Length", minval=1, group="1. Trend")
        self.st_mult      = input.float(3.0, "Supertrend Multiplier", minval=0.5, step=0.1, group="1. Trend")

        # ── Group 2: Buyer / Seller Pressure ──────────────────────────────────
        self.press_len    = input.int(14, "Pressure Lookback (bars)", minval=3, group="2. Pressure")
        self.dom_thresh   = input.float(58.0, "Min Dominant Side %", minval=50.0, maxval=90.0, step=1.0, group="2. Pressure")

        # ── Group 3: Trend Pullback Entries ───────────────────────────────────
        self.max_ext_atr  = input.float(1.5, "Max Distance from Fast EMA (x ATR)", group="3. Trend Entries")
        self.rsi_high     = input.float(70.0, "BUY Blocked if RSI Above", group="3. Trend Entries")
        self.rsi_low      = input.float(30.0, "SELL Blocked if RSI Below", group="3. Trend Entries")
        self.pb_bars      = input.int(8, "Pullback Lookback (bars)", group="3. Trend Entries")
        self.pb_atr       = input.float(0.6, "Pullback Touch Zone (x ATR)", group="3. Trend Entries")
        self.min_rr       = input.float(1.5, "Min Reward:Risk Ratio to T1", group="3. Trend Entries")

        # ── Group 3b: Breakout Entries ────────────────────────────────────────
        self.use_breakout = input.bool(True, "Allow Breakout Entries", group="3b. Breakouts")
        self.brk_window   = input.int(3, "Breakout Window (bars)", group="3b. Breakouts")
        self.ch_norm_len  = input.int(50, "Channel Normalization Length", group="3b. Breakouts")
        self.ch_len       = input.int(14, "Channel Detection Length", group="3b. Breakouts")

        # ── Group 3c: Dynamic S/R Levels ──────────────────────────────────────
        self.sr_piv_left  = input.int(4, "Swing Pivot Left Bars", group="3c. S/R")
        self.sr_piv_right = input.int(4, "Swing Pivot Right Bars", group="3c. S/R")

        # ── Group 4: Dynamic Probability & Smart Exits ────────────────────────
        self.enable_smart_exit  = input.bool(True, "Smart Candlestick Top Reversal Exit", group="4. Smart Exit")
        self.enable_tsl         = input.bool(False, "Enable Mechanical Trailing Stop Loss", group="4. Smart Exit")
        self.t1_atr_mult        = input.float(2.0, "Target 1 (T1) Multiplier (x ATR)", minval=1.0, step=0.1, group="4. Smart Exit")
        self.t2_atr_mult        = input.float(3.5, "Target 2 (T2) Multiplier (x ATR)", minval=2.0, step=0.1, group="4. Smart Exit")
        self.min_hold_prob      = input.float(40.0, "Min Bullish Prob % to Hold", minval=20.0, maxval=80.0, step=5.0, group="4. Smart Exit")

        # ── Group 5: Session Trading Hours (NSE Market Timing) ────────────────
        self.enable_session_filter = input.bool(True, "Enable Session Time Window", group="5. Session Hours")
        self.session_start_time    = input.string("10:05", "No Trades Before (HH:MM)", group="5. Session Hours", tooltip="Requires >=50 min of candles (09:15-10:05) for EMA50/RSI indicator maturity")
        self.session_end_time      = input.string("15:15", "No Trades After (HH:MM)", group="5. Session Hours", tooltip="Blocks fresh intraday trades after 3:15 PM")
        self.auto_square_off       = input.bool(True, "Auto Square Off Open Trades at 3:15 PM", group="5. Session Hours")

        # Internal state tracking
        self.plots = {}
        self.last_signal_bar = -999
        self.active_position = None

        # Pre-calculated incremental recurrence states for sub-microsecond live ticks
        self._cur_ema_fast = 0.0
        self._cur_ema_slow = 0.0
        self._cur_rsi = 50.0

    @staticmethod
    def _get_time_str(ts: Any) -> str:
        """Extracts 'HH:MM' 24-hour time string from diverse timestamp representations."""
        if not ts:
            return ""
        if isinstance(ts, (pd.Timestamp, datetime)):
            return ts.strftime("%H:%M")
        s = str(ts).strip()
        if " " in s:
            s = s.split(" ")[1]
        elif "T" in s:
            s = s.split("T")[1]
        return s[:5]

    def _evaluate_trend(
        self,
        spot_close: float,
        ema_fast: float,
        ema_slow: float,
        st_dir: int,
        st_line: float
    ) -> Tuple[str, float]:
        """Calculates Market Trend component score (0-30 pts)."""
        score = 0.0
        parts = []

        if ema_fast > ema_slow:
            if spot_close >= ema_fast:
                score += 15.0
                parts.append("EMA_BULL_EXPANDING (Close>EMA21>EMA50)")
            else:
                score += 8.0
                parts.append("EMA_BULL_PULLBACK (Close<=EMA21>EMA50)")
        else:
            if spot_close < ema_fast:
                parts.append("EMA_BEAR_EXPANDING (Close<EMA21<EMA50)")
            else:
                score += 4.0
                parts.append("EMA_BEAR_RECOVERY")

        if st_dir == 1:
            score += 15.0
            parts.append(f"SUPERTREND_GREEN ({st_line:.1f})")
        else:
            parts.append(f"SUPERTREND_RED ({st_line:.1f})")

        return " + ".join(parts), score

    def _evaluate_buyer_strength(self, press) -> Tuple[str, float]:
        """Calculates Buyer's Strength component score (0-30 pts)."""
        buyer_pct = float(press.buyer_pct)
        seller_pct = float(press.seller_pct)

        if press.dominant_side == "BUYER":
            if buyer_pct >= 65.0:
                return f"AGGRESSIVE_BUYERS ({buyer_pct:.1f}%)", 30.0
            elif buyer_pct >= 58.0:
                return f"STRONG_BUYERS ({buyer_pct:.1f}%)", 25.0
            elif buyer_pct >= 50.0:
                return f"MODERATE_BUYERS ({buyer_pct:.1f}%)", 18.0
            else:
                return f"WEAK_BUYERS ({buyer_pct:.1f}%)", 12.0
        else:
            if seller_pct >= 65.0:
                return f"HEAVY_SELLERS ({seller_pct:.1f}%)", 0.0
            elif seller_pct >= 58.0:
                return f"STRONG_SELLERS ({seller_pct:.1f}%)", 4.0
            else:
                return f"MILD_SELLERS ({seller_pct:.1f}%)", 8.0

    def _evaluate_sr_headroom(self, spot_close: float, current_atr: float, sr) -> Tuple[str, float]:
        """Calculates Support & Resistance Headroom component score (0-20 pts)."""
        nearest_res = sr.nearest_resistance
        if nearest_res is None or np.isnan(nearest_res):
            return "NO_OVERHEAD_RESISTANCE", 20.0

        dist = nearest_res - spot_close
        if dist < 0:
            return f"BROKEN_ABOVE_RES (+{abs(dist):.1f}pts above {nearest_res:.1f})", 20.0
        elif dist >= 2.0 * current_atr:
            return f"WIDE_RUNWAY ({dist:.1f}pts to Res {nearest_res:.1f})", 18.0
        elif dist >= 0.8 * current_atr:
            return f"MODERATE_RUNWAY ({dist:.1f}pts to Res {nearest_res:.1f})", 12.0
        elif dist >= 0.25 * current_atr:
            return f"APPROACHING_RES ({dist:.1f}pts to Res {nearest_res:.1f})", 6.0
        else:
            return f"RESISTANCE_WALL ({dist:.1f}pts to Res {nearest_res:.1f})", 2.0

    def _evaluate_candlestick(self, open_p: float, high_p: float, low_p: float, close_p: float) -> Tuple[str, float]:
        """Calculates Candlestick Pattern component score (0-20 pts) including hammer detection."""
        rng = high_p - low_p
        if rng <= 0.001:
            return "DOJI_FLAT", 10.0

        body = abs(close_p - open_p)
        lower_wick = min(open_p, close_p) - low_p
        upper_wick = high_p - max(open_p, close_p)
        is_green = close_p >= open_p

        if lower_wick >= 1.8 * body and upper_wick <= 0.6 * lower_wick and (close_p - low_p) >= 0.55 * rng:
            return "HAMMER_BULLISH_REJECTION", 20.0

        if upper_wick >= 1.8 * body and lower_wick <= 0.6 * upper_wick:
            return "SHOOTING_STAR_BEARISH_REJECTION", 2.0

        if is_green and body >= 0.65 * rng:
            return "BULLISH_EXPANSION_BAR", 18.0

        if not is_green and body >= 0.65 * rng:
            return "BEARISH_EXPANSION_BAR", 2.0

        if is_green and lower_wick >= upper_wick:
            return "BULLISH_CONTINUATION", 14.0

        return "NEUTRAL_CONSOLIDATION", 10.0

    def warm_up(self, historical_bars: pd.DataFrame):
        """Vectorized warmup using rolling TA calculations."""
        if historical_bars.empty or len(historical_bars) < 20:
            return

        closes = historical_bars["spot_close"].values if "spot_close" in historical_bars else historical_bars["close"].values
        closes_f = closes.astype(float)
        ema_f = ta.ema(closes_f, self.ema_fast_len.value)
        ema_s = ta.ema(closes_f, self.ema_slow_len.value)
        rsi_vals = ta.rsi(closes_f, self.rsi_len.value)

        self._cur_ema_fast = float(ema_f[-1])
        self._cur_ema_slow = float(ema_s[-1])
        self._cur_rsi = float(rsi_vals[-1])

    def on_tick(self, ltp: float, volume: int, timestamp: str) -> Optional[IndicatorSignal]:
        """
        Sub-microsecond incremental tick recurrence.
        Completes in < 2 microseconds in RAM.
        """
        # Incremental EMA recurrence update: EMA_t = alpha * p_t + (1 - alpha) * EMA_{t-1}
        alpha_fast = 2.0 / (self.ema_fast_len.value + 1.0)
        self._cur_ema_fast = alpha_fast * ltp + (1.0 - alpha_fast) * self._cur_ema_fast

        alpha_slow = 2.0 / (self.ema_slow_len.value + 1.0)
        self._cur_ema_slow = alpha_slow * ltp + (1.0 - alpha_slow) * self._cur_ema_slow

        return None

    def on_bar(
        self,
        option_bar: Dict[str, Any],
        history: pd.DataFrame,
        active_trade: Optional[Dict[str, Any]] = None
    ) -> Optional[IndicatorSignal]:
        """
        Evaluates BSS Pro v5 multi-factor probability, entry conditions, and active trade holding rules.
        """
        close = history["spot_close"].values if "spot_close" in history else history["close"].values
        high = history["spot_high"].values if "spot_high" in history else history["high"].values
        low = history["spot_low"].values if "spot_low" in history else history["low"].values
        open_ = history["spot_open"].values if "spot_open" in history else history["open"].values
        volume = history["spot_volume"].values if "spot_volume" in history else history["volume"].values

        n_bars = len(close)
        min_bars_needed = max(50, self.ema_slow_len.value, self.ch_norm_len.value)
        if n_bars < min_bars_needed:
            return None

        # Ensure float arrays
        close_f = close.astype(float)
        high_f = high.astype(float)
        low_f = low.astype(float)
        open_f = open_.astype(float)
        volume_f = volume.astype(float)

        # 1. Technical Indicators
        ema_fast = ta.ema(close_f, self.ema_fast_len.value)
        ema_slow = ta.ema(close_f, self.ema_slow_len.value)
        rsi = ta.rsi(close_f, self.rsi_len.value)
        atr = ta.atr(high_f, low_f, close_f, 14)
        trend_dir, st_line = ta.supertrend(high_f, low_f, close_f, self.st_len.value, self.st_mult.value)

        # 2. Dynamic Support / Resistance (Candle Bodies)
        sr = ta.dynamic_sr(open_f, high_f, low_f, close_f, self.sr_piv_left.value, self.sr_piv_right.value)

        # 3. Buyer / Seller Pressure
        press = ta.buyer_seller_pressure(open_f, high_f, low_f, close_f, volume_f, self.press_len.value)

        # 4. Breakout Box Detection
        ch = ta.breakout_channels(open_f, high_f, low_f, close_f, volume_f, self.ch_norm_len.value, self.ch_len.value)

        # 5. Multi-Factor Probability Components
        current_atr = float(atr[-1]) if atr[-1] > 0 else 10.0
        trend_desc, trend_score = self._evaluate_trend(close_f[-1], ema_fast[-1], ema_slow[-1], trend_dir[-1], st_line[-1])
        buyer_desc, buyer_score = self._evaluate_buyer_strength(press)
        sr_desc, sr_score = self._evaluate_sr_headroom(close_f[-1], current_atr, sr)
        pattern_desc, pattern_score = self._evaluate_candlestick(open_f[-1], high_f[-1], low_f[-1], close_f[-1])

        total_prob = round(float(trend_score + buyer_score + sr_score + pattern_score), 1)

        vol_ma = ta.sma(volume_f, 14)
        current_vol_ratio = float(volume_f[-1] / max(1.0, vol_ma[-1]))
        hl = max(0.001, high_f[-1] - low_f[-1])
        candle_seller_pct = float(((high_f[-1] - close_f[-1]) / hl) * 100.0)

        # Cache plots for visual rendering on chart
        self.plots = {
            "ema_fast": float(ema_fast[-1]),
            "ema_slow": float(ema_slow[-1]),
            "supertrend": float(st_line[-1]),
            "rsi": float(rsi[-1]),
            "buyer_pct": float(press.buyer_pct),
            "seller_pct": float(press.seller_pct),
            "vol_ratio": round(current_vol_ratio, 2),
            "candle_seller_pct": round(candle_seller_pct, 1),
            "nearest_res": float(sr.nearest_resistance) if sr.nearest_resistance else None,
            "nearest_sup": float(sr.nearest_support) if sr.nearest_support else None,
            "continuation_prob": total_prob,
            "candle_pattern": pattern_desc
        }

        # Option price levels
        opt_close = float(option_bar.get("close", option_bar.get("opt_close", close[-1])))
        opt_atr = current_atr * 0.5  # Option ATR delta approximation
        ts = str(option_bar.get("timestamp", ""))
        time_str = self._get_time_str(ts)

        # ─────────────────────────────────────────────────────────────────────
        # ACTIVE POSITION MANAGEMENT (Holding / Trailing / Exit Decision)
        # ─────────────────────────────────────────────────────────────────────
        if active_trade is not None:
            entry_price = float(active_trade.get("entry_price", opt_close))
            initial_sl = float(active_trade.get("initial_sl", active_trade.get("sl", 0.0)))
            cur_sl = float(active_trade.get("sl", initial_sl))
            target = float(active_trade.get("target", 0.0))
            pts_gain = opt_close - entry_price

            diag = {
                "probability": total_prob,
                "trend_score": trend_score,
                "trend_desc": trend_desc,
                "buyer_score": buyer_score,
                "buyer_desc": buyer_desc,
                "sr_score": sr_score,
                "sr_desc": sr_desc,
                "pattern_score": pattern_score,
                "pattern_desc": pattern_desc,
                "pts_gain": round(pts_gain, 2),
                "current_sl": cur_sl,
                "vol_ratio": round(current_vol_ratio, 2),
                "seller_pct": round(candle_seller_pct, 1),
                "allow_runner": self.enable_smart_exit.value
            }

            # ── SMART EXIT 0: Intraday Session Square-off at or after 15:15 ──
            if self.enable_session_filter.value and self.auto_square_off.value and time_str:
                if time_str >= self.session_end_time.value:
                    exit_reason = (
                        f"SESSION_CLOSE_EXIT: Intraday square-off at {time_str} "
                        f"(No positions held past {self.session_end_time.value}) | Locked Gain: +{pts_gain:.1f}pts"
                    )
                    return IndicatorSignal(
                        action="EXIT_CE",
                        trigger_price=round(opt_close, 2),
                        tag="SESSION_CLOSE_EXIT",
                        timestamp=ts,
                        confidence=1.0,
                        extra={"reason": exit_reason, "smart_exit_type": "SESSION_CLOSE_EXIT", **diag}
                    )

            # ── SMART EXIT 1: Institutional Volume Distribution Dump (10:19 Pattern) ──
            # When in profit, an aggressive bearish expansion bar with high volume (>=1.0x avg)
            # and heavy seller dominance (>=75% sellers) signals immediate institutional exit.
            rng = high_f[-1] - low_f[-1]
            is_red = close_f[-1] < open_f[-1]
            if self.enable_smart_exit.value and pts_gain >= (1.0 * opt_atr):
                if is_red and (rng >= 0.65 * current_atr) and (current_vol_ratio >= 1.0) and (candle_seller_pct >= 75.0):
                    exit_reason = (
                        f"SMART_EXIT_DISTRIBUTION: Heavy Institutional Selling at {close_f[-1]:.1f} "
                        f"(Vol {current_vol_ratio:.1f}x avg, {candle_seller_pct:.0f}% Sellers) | Locked Gain: +{pts_gain:.1f}pts"
                    )
                    return IndicatorSignal(
                        action="EXIT_CE",
                        trigger_price=round(opt_close, 2),
                        tag="DISTRIBUTION_VOLUME_DUMP",
                        timestamp=ts,
                        confidence=0.95,
                        extra={"reason": exit_reason, "smart_exit_type": "DISTRIBUTION_VOLUME_DUMP", **diag}
                    )

            # ── SMART EXIT 2: Volume Profile Swing POC Breakdown ───────────────
            # When in profit (>= 1.5 opt_atr) and spot breaks below the rolling 20-bar Volume Profile POC
            if self.enable_smart_exit.value and pts_gain >= (1.5 * opt_atr) and len(close_f) >= 20:
                vp = ta.volume_profile(close_f[-20:], volume_f[-20:], num_bins=8)
                if close_f[-1] < vp.poc and close_f[-2] >= vp.poc:
                    exit_reason = (
                        f"SMART_EXIT_POC: Broke below Volume Profile POC ({vp.poc:.1f}) at {close_f[-1]:.1f} | Locked Gain: +{pts_gain:.1f}pts"
                    )
                    return IndicatorSignal(
                        action="EXIT_CE",
                        trigger_price=round(opt_close, 2),
                        tag="VOLUME_PROFILE_POC_BREAKDOWN",
                        timestamp=ts,
                        confidence=0.92,
                        extra={"reason": exit_reason, "smart_exit_type": "POC_BREAKDOWN", **diag}
                    )

            # ── SMART EXIT 3: Volume-Confirmed Top Reversal Wick ───────────────
            # Requires BOTH top rejection wick AND volume expansion confirmation (vol_ratio >= 1.1x).
            # Avoids premature exits on low-volume minor wicks (like 10:17 with 0.5x vol).
            if self.enable_smart_exit.value and pts_gain >= (1.5 * opt_atr):
                body = abs(close_f[-1] - open_f[-1])
                upper_wick = high_f[-1] - max(open_f[-1], close_f[-1])
                lower_wick = min(open_f[-1], close_f[-1]) - low_f[-1]
                recent_peak = float(np.max(high_f[-min(len(high_f), 15):]))
                is_at_peak = (high_f[-1] >= recent_peak - (0.35 * current_atr))

                if is_red and is_at_peak and (upper_wick >= 1.8 * body) and (upper_wick >= 0.40 * rng) and (lower_wick <= 0.65 * upper_wick) and (current_vol_ratio >= 1.1) and (candle_seller_pct >= 70.0):
                    exit_reason = (
                        f"SMART_EXIT_REVERSAL: Volume-Confirmed Reversal at {high_f[-1]:.1f} "
                        f"(Vol {current_vol_ratio:.1f}x avg, Upper wick {upper_wick:.1f}pts) | Locked Gain: +{pts_gain:.1f}pts"
                    )
                    return IndicatorSignal(
                        action="EXIT_CE",
                        trigger_price=round(opt_close, 2),
                        tag="SMART_EXIT_TOP_REVERSAL",
                        timestamp=ts,
                        confidence=0.90,
                        extra={"reason": exit_reason, "smart_exit_type": "VOLUME_CONFIRMED_TOP_REVERSAL", **diag}
                    )

            # ── SMART EXIT 4: Supertrend Reversal (flipped bear) ─────────────
            if trend_dir[-1] == -1:
                exit_reason = f"SUPERTREND_BEAR: Supertrend turned red ({st_line[-1]:.1f}) | Bull Prob: {total_prob:.0f}%"
                return IndicatorSignal(
                    action="EXIT_CE",
                    trigger_price=round(opt_close, 2),
                    tag="SUPERTREND_REVERSAL",
                    timestamp=ts,
                    confidence=round(total_prob / 100.0, 2),
                    extra={"reason": exit_reason, **diag}
                )

            # ── SMART EXIT 5: Severe Bullish Probability Breakdown ───────────
            if total_prob < self.min_hold_prob.value:
                exit_reason = f"PROB_BREAKDOWN: Bullish prob fell to {total_prob:.0f}% < {self.min_hold_prob.value}% | {buyer_desc} | {pattern_desc}"
                return IndicatorSignal(
                    action="EXIT_CE",
                    trigger_price=round(opt_close, 2),
                    tag="PROB_BREAKDOWN",
                    timestamp=ts,
                    confidence=round(total_prob / 100.0, 2),
                    extra={"reason": exit_reason, **diag}
                )

            # ── CONTINUATION DECISION: Bull Trend Intact -> HOLD POSITION ─────
            # Mechanical TSL is DISABLED by user request. Initial SL is preserved strictly as catastrophe floor.
            trailed_sl = initial_sl
            if self.enable_tsl.value:
                dyn_sl = round(opt_close - (1.5 * opt_atr), 2)
                trailed_sl = max(cur_sl, dyn_sl)

            hold_reason = f"HOLD_SMART: Bull Trend Intact (Prob {total_prob:.0f}%) | Gain +{pts_gain:.1f}pts | Catastrophe SL ₹{trailed_sl:.2f} | {pattern_desc} | Buyers {press.buyer_pct:.1f}%"
            return IndicatorSignal(
                action="HOLD",
                trigger_price=round(opt_close, 2),
                stop_loss=trailed_sl,
                target=round(target, 2),
                tag="HOLD_SMART",
                timestamp=ts,
                confidence=round(total_prob / 100.0, 2),
                extra={"reason": hold_reason, "trailed_sl": trailed_sl, **diag}
            )

        # ─────────────────────────────────────────────────────────────────────
        # NEW ENTRY SIGNAL EVALUATION (When not in position)
        # ─────────────────────────────────────────────────────────────────────
        # Session Time Filtering:
        # Strictly enforce trading window (default: 10:05 AM to 15:15 PM)
        # Requires >= 50 minutes of candles (09:15-10:05) for EMA50/RSI/Supertrend maturity
        if self.enable_session_filter.value and time_str:
            if time_str < self.session_start_time.value or time_str > self.session_end_time.value:
                return None

        # Condition A: Trend Pullback Entry (BUY CE)
        is_bullish_trend = (close_f[-1] > ema_fast[-1] > ema_slow[-1]) and (trend_dir[-1] == 1)
        pressure_confirmed = (press.dominant_side == "BUYER") and (press.buyer_pct >= self.dom_thresh.value)
        rsi_valid = rsi[-1] < self.rsi_high.value

        # Touch zone check (pulled back to fast EMA within pb_bars)
        lookback = min(len(low_f), self.pb_bars.value)
        pulled_back = bool(np.any(np.abs(low_f[-lookback:] - ema_fast[-lookback:]) <= (self.pb_atr.value * current_atr)))

        if is_bullish_trend and pressure_confirmed and rsi_valid and pulled_back:
            if (n_bars - self.last_signal_bar) > 5 and total_prob >= 55.0:
                self.last_signal_bar = n_bars
                sl = opt_close - (1.5 * opt_atr)
                t1 = opt_close + (self.t1_atr_mult.value * opt_atr)
                t2 = opt_close + (self.t2_atr_mult.value * opt_atr)
                entry_reason = f"PROB_{total_prob:.0f}% | {trend_desc} | {buyer_desc} | {sr_desc} | {pattern_desc}"
                return IndicatorSignal(
                    action="BUY_CE",
                    trigger_price=round(opt_close, 2),
                    stop_loss=round(max(0.05, sl), 2),
                    target=round(t1, 2),
                    target_2=round(t2, 2),
                    tag="BSS_TREND_PULLBACK",
                    timestamp=ts,
                    confidence=round(total_prob / 100.0, 2),
                    extra={
                        "reason": entry_reason,
                        "probability": total_prob,
                        "initial_sl": round(max(0.05, sl), 2),
                        "t1": round(t1, 2),
                        "t2": round(t2, 2),
                        "trend_score": trend_score,
                        "buyer_score": buyer_score,
                        "sr_score": sr_score,
                        "pattern_score": pattern_score,
                        "pattern_desc": pattern_desc,
                        "allow_runner": self.enable_smart_exit.value
                    }
                )

        # Condition B: Channel Breakout Entry (BUY CE)
        if self.use_breakout.value and ch.breakout_up and pressure_confirmed:
            if (n_bars - self.last_signal_bar) > 5 and total_prob >= 55.0:
                self.last_signal_bar = n_bars
                sl = opt_close - (1.2 * opt_atr)
                t1 = opt_close + (self.t1_atr_mult.value * opt_atr)
                t2 = opt_close + (self.t2_atr_mult.value * opt_atr)
                entry_reason = f"PROB_{total_prob:.0f}% BREAKOUT | {trend_desc} | {buyer_desc} | {sr_desc} | {pattern_desc}"
                return IndicatorSignal(
                    action="BUY_CE",
                    trigger_price=round(opt_close, 2),
                    stop_loss=round(max(0.05, sl), 2),
                    target=round(t1, 2),
                    target_2=round(t2, 2),
                    tag="BSS_CHANNEL_BREAKOUT",
                    timestamp=ts,
                    confidence=round(total_prob / 100.0, 2),
                    extra={
                        "reason": entry_reason,
                        "probability": total_prob,
                        "initial_sl": round(max(0.05, sl), 2),
                        "t1": round(t1, 2),
                        "t2": round(t2, 2),
                        "trend_score": trend_score,
                        "buyer_score": buyer_score,
                        "sr_score": sr_score,
                        "pattern_score": pattern_score,
                        "pattern_desc": pattern_desc,
                        "allow_runner": self.enable_smart_exit.value
                    }
                )

        return None


# Backward-compatible alias so existing imports of BSSProV5Indicator still work
BSSProV5Indicator = BSSProV6Indicator
