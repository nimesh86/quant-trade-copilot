"""
Smart Money Concepts (SMC) & Order Flow Delta Indicator
=======================================================
Implements Institutional Market Structure & Order Flow Imbalance:
  1. Swing High & Swing Low Fractal Pivots
  2. Break of Structure (BOS): Option premium trend continuation
  3. Change of Character (CHoCH): Trend reversal & early exit invalidation
  4. Order Flow Buyer vs Seller Strength % (CVD / Delta Proxy)
  5. Pullback / Order Block Retest confirmation
"""
from typing import Dict, List, Any, Optional, Tuple
from dataclasses import dataclass
import numpy as np

from trading_engine.quant_lib.base import Indicator, IndicatorSignal, input


@dataclass
class SwingNode:
    index: int
    price: float
    is_high: bool
    timestamp: str
    broken: bool = False


class SMCOrderFlowIndicator(Indicator):
    """
    SMC + Order Flow Strategy Engine:
    Trades BOS continuation and CHoCH reversals with Buyer/Seller volume delta confirmation.
    """

    def init(self):
        self.pivot_lookback = input.int(3, "Pivot Lookback", minval=2, maxval=10, group="Structure Settings")
        self.delta_threshold = input.float(52.0, "Buyer Delta %", minval=50.0, maxval=70.0, group="Order Flow")
        self.enable_choch_exit = input.bool(True, "CHoCH Early Exit", group="Execution")
        self.reset()

    def reset(self):
        self.swing_highs: List[SwingNode] = []
        self.swing_lows: List[SwingNode] = []
        self.market_trend: str = "NEUTRAL"  # "BULLISH", "BEARISH", "NEUTRAL"
        self.recent_bars: List[Dict[str, Any]] = []
        self._bar_count: int = 0

    @staticmethod
    def calculate_order_flow_delta(bar: Dict[str, Any]) -> Tuple[float, float]:
        """
        Computes Buyer Strength % and Seller Strength % from 1-min OHLCV.
        Buyer Push = (Close - Low) + Lower Wick + Bullish Body
        Seller Push = (High - Close) + Upper Wick + Bearish Body
        """
        o = float(bar.get("open", 0.0))
        h = float(bar.get("high", 0.0))
        l = float(bar.get("low", 0.0))
        c = float(bar.get("close", 0.0))

        rng = max(h - l, 0.05)
        body = abs(c - o)
        upper_wick = h - max(o, c)
        lower_wick = min(o, c) - l

        buyer_push = (c - l) + lower_wick + max(0.0, c - o)
        seller_push = (h - c) + upper_wick + max(0.0, o - c)

        total = max(buyer_push + seller_push, 0.1)
        buyer_pct = (buyer_push / total) * 100.0
        seller_pct = (seller_push / total) * 100.0
        return buyer_pct, seller_pct

    def warm_up(self, historical_bars: Any):
        self.reset()
        if historical_bars is None:
            return
        bars = historical_bars if isinstance(historical_bars, list) else historical_bars.to_dict("records")
        for b in bars:
            self._update_structure(b)

    def _update_structure(self, bar: Dict[str, Any]):
        self.recent_bars.append(bar)
        if len(self.recent_bars) > 60:
            self.recent_bars.pop(0)

        n = len(self.recent_bars)
        lookback = self.pivot_lookback.value if hasattr(self.pivot_lookback, "value") else self.pivot_lookback
        if n < lookback * 2 + 1:
            return

        check_idx = n - 1 - lookback
        check_bar = self.recent_bars[check_idx]
        check_h = float(check_bar["high"])
        check_l = float(check_bar["low"])

        # Check Swing High
        is_pivot_h = True
        for i in range(check_idx - lookback, check_idx + lookback + 1):
            if i != check_idx and float(self.recent_bars[i]["high"]) >= check_h:
                is_pivot_h = False
                break
        if is_pivot_h:
            self.swing_highs.append(SwingNode(
                index=self._bar_count - lookback,
                price=check_h,
                is_high=True,
                timestamp=str(check_bar.get("timestamp") or check_bar.get("time") or "")
            ))
            if len(self.swing_highs) > 10:
                self.swing_highs.pop(0)

        # Check Swing Low
        is_pivot_l = True
        for i in range(check_idx - lookback, check_idx + lookback + 1):
            if i != check_idx and float(self.recent_bars[i]["low"]) <= check_l:
                is_pivot_l = False
                break
        if is_pivot_l:
            self.swing_lows.append(SwingNode(
                index=self._bar_count - lookback,
                price=check_l,
                is_high=False,
                timestamp=str(check_bar.get("timestamp") or check_bar.get("time") or "")
            ))
            if len(self.swing_lows) > 10:
                self.swing_lows.pop(0)

    def on_bar(
        self,
        option_bar: Dict[str, Any],
        history: Any,
        active_trade: Optional[Dict[str, Any]] = None
    ) -> Optional[IndicatorSignal]:
        self._bar_count += 1
        curr_bar = option_bar
        ts = str(curr_bar.get("timestamp") or curr_bar.get("time") or "")
        c = float(curr_bar.get("close", 0.0))
        h = float(curr_bar.get("high", 0.0))
        l = float(curr_bar.get("low", 0.0))
        o = float(curr_bar.get("open", 0.0))

        self._update_structure(curr_bar)
        buyer_pct, seller_pct = self.calculate_order_flow_delta(curr_bar)

        # ── 1. ACTIVE TRADE MONITORING: CHoCH Early Exit ─────────────────────
        if active_trade is not None:
            side = str(active_trade.get("trade_side", active_trade.get("side", ""))).upper()
            exit_act = "EXIT_CE" if "CE" in side else "EXIT_PE"
            
            # If current option closes below its most recent swing low -> CHoCH bearish exit
            if self.swing_lows:
                last_sl = self.swing_lows[-1]
                if not last_sl.broken and c < last_sl.price:
                    last_sl.broken = True
                    self.market_trend = "BEARISH"
                    return IndicatorSignal(
                        action=exit_act,
                        trigger_price=round(c, 2),
                        tag="CHOCH_BEARISH_EXIT",
                        timestamp=ts,
                        extra={"reason": f"CHoCH Invalidation: Broken Option Swing Low ({last_sl.price:.2f})"}
                    )
            return None

        # ── 2. NEW ENTRY EVALUATION: BOS / CHoCH + Order Flow Delta ───────────
        delta_thresh = self.delta_threshold.value if hasattr(self.delta_threshold, "value") else self.delta_threshold

        # Option Buying Trigger: Option Close breaks above Swing High
        if self.swing_highs:
            last_sh = self.swing_highs[-1]
            if not last_sh.broken and c > last_sh.price:
                last_sh.broken = True
                is_choch = (self.market_trend != "BULLISH")
                self.market_trend = "BULLISH"
                
                # Check Order Flow Delta Confirmation
                if buyer_pct >= delta_thresh:
                    tag = "CHOCH_BULLISH_REVERSAL" if is_choch else "BOS_BULLISH_CONTINUATION"
                    reason = f"{tag}: Break of {last_sh.price:.2f} (Buyers {buyer_pct:.1f}% vs Sellers {seller_pct:.1f}%)"
                    return IndicatorSignal(
                        action="BUY_CE",  # Will be mapped to BUY_CE or BUY_PE based on channel
                        trigger_price=round(c, 2),
                        stop_loss=round(max(1.0, last_sh.price - 6.0), 2),
                        target=round(c + 25.0, 2),
                        tag=tag,
                        timestamp=ts,
                        extra={"reason": reason, "buyer_pct": buyer_pct, "structure": "BOS" if not is_choch else "CHOCH"}
                    )

        return None
