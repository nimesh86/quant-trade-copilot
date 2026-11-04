"""
Smart Money Concepts (SMC): Market Structure Engine
────────────────────────────────────────────────────
Implements institutional market structure identification:
  - Swing High / Low Pivot Detection (Fractal Pivots)
  - Break of Structure (BOS): Trend continuation signals
  - Change of Character (CHoCH): Trend reversal / invalidation signals
  - Fair Value Gap (FVG) & Order Block (OB) Retest Entry Zones
  - Structural Invalidation Levels (Tight stop placement)
"""
from typing import Dict, Any, List, Optional, Tuple
from dataclasses import dataclass, field
import numpy as np


@dataclass
class SwingPoint:
    index: int
    timestamp: str
    price: float
    point_type: str  # "HIGH" or "LOW"
    broken: bool = False


@dataclass
class StructureEvent:
    event_type: str  # "BOS_BULL", "BOS_BEAR", "CHOCH_BULL", "CHOCH_BEAR"
    timestamp: str
    price: float
    level: float
    order_block_high: float
    order_block_low: float
    invalidation_level: float


class MarketStructureEngine:
    """
    Detects BOS, CHoCH, Order Blocks, and Pullback/Retest levels
    on 1-minute historical OHLCV data.
    """

    def __init__(self, pivot_lookback: int = 3, retest_depth_pct: float = 0.50):
        self.pivot_lookback = pivot_lookback
        self.retest_depth_pct = retest_depth_pct
        self.reset()

    def reset(self):
        self.swing_highs: List[SwingPoint] = []
        self.swing_lows: List[SwingPoint] = []
        self.market_trend: str = "NEUTRAL"  # "BULLISH", "BEARISH", "NEUTRAL"
        self.last_bos: Optional[StructureEvent] = None
        self.last_choch: Optional[StructureEvent] = None
        self.active_order_blocks: List[Dict[str, Any]] = []
        self.last_swing_high: Optional[SwingPoint] = None
        self.last_swing_low: Optional[SwingPoint] = None

    def update(self, bar: Dict[str, Any], history: List[Dict[str, Any]]) -> Optional[StructureEvent]:
        """
        Evaluates the current bar in context of recent history to detect
        Fractal Pivots, BOS, CHoCH, and Order Blocks.
        """
        n = len(history)
        if n < self.pivot_lookback * 2 + 1:
            return None

        bar_idx = n - 1
        ts = str(bar.get("time") or bar.get("timestamp") or "")
        curr_c = float(bar["close"])
        curr_h = float(bar["high"])
        curr_l = float(bar["low"])

        # 1. Detect Pivot High at bar (n - 1 - lookback)
        check_idx = n - 1 - self.pivot_lookback
        check_bar = history[check_idx]
        check_h = float(check_bar["high"])
        check_l = float(check_bar["low"])

        # Pivot High Check
        is_pivot_high = True
        for i in range(check_idx - self.pivot_lookback, check_idx + self.pivot_lookback + 1):
            if i != check_idx and float(history[i]["high"]) >= check_h:
                is_pivot_high = False
                break

        if is_pivot_high:
            sp = SwingPoint(
                index=check_idx,
                timestamp=str(check_bar.get("time") or check_bar.get("timestamp") or ""),
                price=check_h,
                point_type="HIGH"
            )
            self.swing_highs.append(sp)
            self.last_swing_high = sp

        # Pivot Low Check
        is_pivot_low = True
        for i in range(check_idx - self.pivot_lookback, check_idx + self.pivot_lookback + 1):
            if i != check_idx and float(history[i]["low"]) <= check_l:
                is_pivot_low = False
                break

        if is_pivot_low:
            sp = SwingPoint(
                index=check_idx,
                timestamp=str(check_bar.get("time") or check_bar.get("timestamp") or ""),
                price=check_l,
                point_type="LOW"
            )
            self.swing_lows.append(sp)
            self.last_swing_low = sp

        # 2. Check Break of Structure (BOS) vs Change of Character (CHoCH)
        event = None

        # Bullish Breakout: Close crosses above unbroken Swing High
        if self.last_swing_high and not self.last_swing_high.broken:
            if curr_c > self.last_swing_high.price:
                self.last_swing_high.broken = True
                
                # If market was already Bullish -> Trend Continuation (BOS)
                # If market was Bearish/Neutral -> Trend Reversal (CHoCH)
                is_reversal = (self.market_trend != "BULLISH")
                event_type = "CHOCH_BULL" if is_reversal else "BOS_BULL"
                self.market_trend = "BULLISH"

                # Find originating Order Block (lowest down-candle before breakout impulse)
                ob_high = curr_l
                ob_low = self.last_swing_low.price if self.last_swing_low else (curr_l - 5.0)
                inv_level = ob_low

                event = StructureEvent(
                    event_type=event_type,
                    timestamp=ts,
                    price=curr_c,
                    level=self.last_swing_high.price,
                    order_block_high=ob_high,
                    order_block_low=ob_low,
                    invalidation_level=inv_level
                )
                if is_reversal:
                    self.last_choch = event
                else:
                    self.last_bos = event

                # Register active order block for pullback retest
                self.active_order_blocks.append({
                    "type": "DEMAND",
                    "top": self.last_swing_high.price,
                    "retest_price": self.last_swing_high.price - (self.last_swing_high.price - ob_low) * self.retest_depth_pct,
                    "bottom": ob_low,
                    "timestamp": ts,
                    "tested": False
                })

        # Bearish Breakdown: Close crosses below unbroken Swing Low
        if self.last_swing_low and not self.last_swing_low.broken:
            if curr_c < self.last_swing_low.price:
                self.last_swing_low.broken = True
                
                is_reversal = (self.market_trend != "BEARISH")
                event_type = "CHOCH_BEAR" if is_reversal else "BOS_BEAR"
                self.market_trend = "BEARISH"

                ob_low = curr_h
                ob_high = self.last_swing_high.price if self.last_swing_high else (curr_h + 5.0)
                inv_level = ob_high

                event = StructureEvent(
                    event_type=event_type,
                    timestamp=ts,
                    price=curr_c,
                    level=self.last_swing_low.price,
                    order_block_high=ob_high,
                    order_block_low=ob_low,
                    invalidation_level=inv_level
                )
                if is_reversal:
                    self.last_choch = event
                else:
                    self.last_bos = event

                self.active_order_blocks.append({
                    "type": "SUPPLY",
                    "top": ob_high,
                    "retest_price": self.last_swing_low.price + (ob_high - self.last_swing_low.price) * self.retest_depth_pct,
                    "bottom": self.last_swing_low.price,
                    "timestamp": ts,
                    "tested": False
                })

        return event

    def check_pullback_retest(self, curr_bar: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        Checks if the current bar pulls back into an active Order Block / FVG,
        triggering a high-probability Retest Entry with tight risk.
        """
        curr_l = float(curr_bar["low"])
        curr_h = float(curr_bar["high"])
        curr_c = float(curr_bar["close"])

        for ob in reversed(self.active_order_blocks):
            if ob["tested"]:
                continue
            
            # Demand Retest (Buy on Dip)
            if ob["type"] == "DEMAND":
                # Price dips into the retest zone [retest_price, top] without breaking bottom
                if curr_l <= ob["top"] and curr_l >= ob["bottom"]:
                    # Bullish reaction: close closes above low
                    if curr_c > curr_l:
                        ob["tested"] = True
                        return {
                            "signal": "BUY",
                            "action": "BUY_RETEST",
                            "entry_price": max(curr_c, ob["retest_price"]),
                            "stop_loss": ob["bottom"] - 1.5,
                            "type": "DEMAND_RETEST",
                            "target_risk_reward": 2.5
                        }
            
            # Supply Retest (Sell / Put on Bounce)
            elif ob["type"] == "SUPPLY":
                if curr_h >= ob["bottom"] and curr_h <= ob["top"]:
                    if curr_c < curr_h:
                        ob["tested"] = True
                        return {
                            "signal": "SELL",
                            "action": "SELL_RETEST",
                            "entry_price": min(curr_c, ob["retest_price"]),
                            "stop_loss": ob["top"] + 1.5,
                            "type": "SUPPLY_RETEST",
                            "target_risk_reward": 2.5
                        }

        return None

