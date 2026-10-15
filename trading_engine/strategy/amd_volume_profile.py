"""
Accumulation - Manipulation - Distribution (AMD) + Volume Profile Strategy.
Implements institutional liquidity sweeps (Wyckoff Spring & Upthrust)
anchored to Time-Price-Opportunity (TPO) / Volume Profile Value Areas (VAL, POC, VAH).
"""
from typing import Dict, Any, Optional, List, Tuple
from datetime import datetime, time
import numpy as np

try:
    from .base import BaseStrategy, StrategySignal
    from ..config import EngineConfig
except ImportError:
    from strategy.base import BaseStrategy, StrategySignal
    from config import EngineConfig

class AMDVolumeProfileStrategy(BaseStrategy):
    def __init__(self, cfg: EngineConfig, strategy_cfg: Optional[Dict[str, Any]] = None):
        super().__init__("AMD_VOLUME_PROFILE", cfg, strategy_cfg)

        # Configurable Parameters
        self.acc_start_str = self.strategy_cfg.get("accumulation_start", "09:15")
        self.acc_end_str = self.strategy_cfg.get("accumulation_end", "10:15")
        self.min_range_pts = self.strategy_cfg.get("min_range_pts", 20.0)
        self.max_range_pts = self.strategy_cfg.get("max_range_pts", 220.0)
        self.min_sweep_pts = self.strategy_cfg.get("min_sweep_pts", 8.0)
        self.max_sweep_pts = self.strategy_cfg.get("max_sweep_pts", 45.0)
        self.max_reclaim_candles = self.strategy_cfg.get("max_reclaim_candles", 15)
        self.sl_buffer_pts = self.strategy_cfg.get("sl_buffer_pts", 2.0)
        self.target_multiplier_r = self.strategy_cfg.get("target_multiplier_r", 3.0)

        # Parse times
        h1, m1 = [int(x) for x in self.acc_start_str.split(":")]
        h2, m2 = [int(x) for x in self.acc_end_str.split(":")]
        self.acc_start_time = time(h1, m1)
        self.acc_end_time = time(h2, m2)

        # Daily State
        self.current_date = None
        self.acc_candles: List[Dict[str, Any]] = []
        self.acc_finalized = False
        self.acc_high = 0.0
        self.acc_low = 0.0
        self.val = 0.0
        self.poc = 0.0
        self.vah = 0.0

        # Sweep tracking state
        self.spring_swept = False
        self.spring_sweep_low = float("inf")
        self.spring_sweep_bar_idx = -1

        self.upthrust_swept = False
        self.upthrust_sweep_high = float("-inf")
        self.upthrust_sweep_bar_idx = -1

        self.setup_triggered_today = False

    def reset_session(self):
        self.current_date = None
        self.acc_candles = []
        self.acc_finalized = False
        self.acc_high = 0.0
        self.acc_low = 0.0
        self.val = 0.0
        self.poc = 0.0
        self.vah = 0.0
        self.spring_swept = False
        self.spring_sweep_low = float("inf")
        self.spring_sweep_bar_idx = -1
        self.upthrust_swept = False
        self.upthrust_sweep_high = float("-inf")
        self.upthrust_sweep_bar_idx = -1
        self.setup_triggered_today = False

    def compute_tpo_profile(self, candles: List[Dict[str, Any]], bin_size: float = 5.0) -> Tuple[float, float, float]:
        """Calculates TPO Value Area Low (VAL), Point of Control (POC), and Value Area High (VAH)."""
        lows = [c["low"] for c in candles]
        highs = [c["high"] for c in candles]
        p_min = np.floor(min(lows) / bin_size) * bin_size
        p_max = np.ceil(max(highs) / bin_size) * bin_size
        bins = np.arange(p_min, p_max + bin_size, bin_size)
        counts = np.zeros(len(bins))

        for c in candles:
            b_idx = np.where((bins >= c["low"]) & (bins <= c["high"]))[0]
            counts[b_idx] += 1

        poc_idx = int(np.argmax(counts))
        poc = float(bins[poc_idx])
        total_tpo = np.sum(counts)

        if total_tpo == 0:
            return float(min(lows)), poc, float(max(highs))

        target_tpo = 0.70 * total_tpo
        current_tpo = counts[poc_idx]
        va_indices = {poc_idx}
        up_idx, down_idx = poc_idx + 1, poc_idx - 1

        while current_tpo < target_tpo:
            c_up = counts[up_idx] if up_idx < len(bins) else -1
            c_dn = counts[down_idx] if down_idx >= 0 else -1
            if c_up == -1 and c_dn == -1:
                break
            if c_up >= c_dn:
                current_tpo += c_up
                va_indices.add(up_idx)
                up_idx += 1
            else:
                current_tpo += c_dn
                va_indices.add(down_idx)
                down_idx -= 1

        val = float(bins[min(va_indices)])
        vah = float(bins[max(va_indices)])
        return val, poc, vah

    def on_candle_close(self, bar: Dict[str, Any], session_context: Dict[str, Any]) -> Optional[StrategySignal]:
        ts = bar["timestamp"]
        bar_date = ts.date()
        bar_time = ts.time()

        if self.current_date != bar_date:
            self.reset_session()
            self.current_date = bar_date

        if self.setup_triggered_today:
            return None

        # Phase 1: Accumulation (09:15 to 10:15 IST)
        if self.acc_start_time <= bar_time <= self.acc_end_time:
            self.acc_candles.append(bar)
            return None

        # Finalize Accumulation profile on first bar after accumulation window
        if not self.acc_finalized:
            if len(self.acc_candles) < 35:
                return None  # Insufficient candles
            self.acc_high = max(c["high"] for c in self.acc_candles)
            self.acc_low = min(c["low"] for c in self.acc_candles)
            acc_range = self.acc_high - self.acc_low

            if not (self.min_range_pts <= acc_range <= self.max_range_pts):
                # Unhealthy / abnormal opening range
                self.setup_triggered_today = True
                return None

            self.val, self.poc, self.vah = self.compute_tpo_profile(self.acc_candles)
            self.acc_finalized = True

        if not self.acc_finalized or bar_time > time(15, 10):
            return None

        c_now = bar["close"]
        h_now = bar["high"]
        l_now = bar["low"]
        bar_idx = bar.get("idx", 0)

        # Phase 2 & 3: BULLISH SPRING (Manipulation sweep below VAL/Low + Reclaim)
        sweep_reference_low = min(self.val, self.acc_low)
        if not self.spring_swept:
            if l_now < sweep_reference_low - self.min_sweep_pts:
                # Sweep detected
                if (sweep_reference_low - l_now) <= self.max_sweep_pts:
                    self.spring_swept = True
                    self.spring_sweep_low = l_now
                    self.spring_sweep_bar_idx = bar_idx
        else:
            self.spring_sweep_low = min(self.spring_sweep_low, l_now)
            bars_since_sweep = bar_idx - self.spring_sweep_bar_idx

            if bars_since_sweep <= self.max_reclaim_candles:
                # Check for Reclaim (Candle closes back above sweep reference level)
                if c_now > sweep_reference_low:
                    entry_px = c_now
                    sl_px = self.spring_sweep_low - self.sl_buffer_pts
                    risk_pts = entry_px - sl_px

                    if risk_pts > 4.0:
                        # High-Target: Opposite Value Area + Range Expansion
                        acc_range = self.acc_high - self.acc_low
                        expansion_target = self.vah + (acc_range * 0.50)
                        r_target = entry_px + (self.target_multiplier_r * risk_pts)
                        target_px = max(expansion_target, r_target)
                        reward_pts = target_px - entry_px
                        rr = reward_pts / risk_pts

                        self.setup_triggered_today = True

                        details = {
                            "setup_type": "BULLISH_SPRING",
                            "acc_high": round(self.acc_high, 2),
                            "acc_low": round(self.acc_low, 2),
                            "val": round(self.val, 2),
                            "poc": round(self.poc, 2),
                            "vah": round(self.vah, 2),
                            "sweep_low": round(self.spring_sweep_low, 2),
                            "sweep_depth_pts": round(sweep_reference_low - self.spring_sweep_low, 2),
                            "bars_to_reclaim": bars_since_sweep
                        }

                        signal = StrategySignal(
                            strategy_name=self.name,
                            timestamp=ts,
                            symbol=self.cfg.symbol,
                            side="CE",
                            action="BUY",
                            trigger_price=round(entry_px, 2),
                            stop_loss=round(sl_px, 2),
                            target=round(target_px, 2),
                            risk_points=round(risk_pts, 2),
                            reward_points=round(reward_pts, 2),
                            rr_ratio=round(rr, 2),
                            confidence_score=0.85 if rr >= 2.5 else 0.70,
                            risk_rating="LOW" if rr >= 3.0 else "MEDIUM",
                            setup_details=details
                        )
                        return signal
            else:
                # Sweep expired without prompt reclaim
                self.spring_swept = False

        # Phase 2 & 3: BEARISH UPTHRUST (Manipulation sweep above VAH/High + Reclaim)
        sweep_reference_high = max(self.vah, self.acc_high)
        if not self.upthrust_swept:
            if h_now > sweep_reference_high + self.min_sweep_pts:
                if (h_now - sweep_reference_high) <= self.max_sweep_pts:
                    self.upthrust_swept = True
                    self.upthrust_sweep_high = h_now
                    self.upthrust_sweep_bar_idx = bar_idx
        else:
            self.upthrust_sweep_high = max(self.upthrust_sweep_high, h_now)
            bars_since_sweep = bar_idx - self.upthrust_sweep_bar_idx

            if bars_since_sweep <= self.max_reclaim_candles:
                # Check for Reclaim (Candle closes back below sweep reference level)
                if c_now < sweep_reference_high:
                    entry_px = c_now
                    sl_px = self.upthrust_sweep_high + self.sl_buffer_pts
                    risk_pts = sl_px - entry_px

                    if risk_pts > 4.0:
                        acc_range = self.acc_high - self.acc_low
                        expansion_target = self.val - (acc_range * 0.50)
                        r_target = entry_px - (self.target_multiplier_r * risk_pts)
                        target_px = min(expansion_target, r_target)
                        reward_pts = entry_px - target_px
                        rr = reward_pts / risk_pts

                        self.setup_triggered_today = True

                        details = {
                            "setup_type": "BEARISH_UPTHRUST",
                            "acc_high": round(self.acc_high, 2),
                            "acc_low": round(self.acc_low, 2),
                            "val": round(self.val, 2),
                            "poc": round(self.poc, 2),
                            "vah": round(self.vah, 2),
                            "sweep_high": round(self.upthrust_sweep_high, 2),
                            "sweep_depth_pts": round(self.upthrust_sweep_high - sweep_reference_high, 2),
                            "bars_to_reclaim": bars_since_sweep
                        }

                        signal = StrategySignal(
                            strategy_name=self.name,
                            timestamp=ts,
                            symbol=self.cfg.symbol,
                            side="PE",
                            action="BUY",
                            trigger_price=round(entry_px, 2),
                            stop_loss=round(sl_px, 2),
                            target=round(target_px, 2),
                            risk_points=round(risk_pts, 2),
                            reward_points=round(reward_pts, 2),
                            rr_ratio=round(rr, 2),
                            confidence_score=0.85 if rr >= 2.5 else 0.70,
                            risk_rating="LOW" if rr >= 3.0 else "MEDIUM",
                            setup_details=details
                        )
                        return signal
            else:
                self.upthrust_swept = False

        return None
