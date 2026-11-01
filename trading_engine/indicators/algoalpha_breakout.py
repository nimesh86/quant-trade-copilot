"""
AlgoAlpha Breakout Channels - Native Python Implementation.
Detects consolidation boxes and fires breakout signals with volatility expansion.
"""
from typing import Dict, Any, Optional
import numpy as np
import pandas as pd

from trading_engine.quant_lib.base import Indicator, IndicatorSignal, input
from trading_engine.quant_lib import ta


class AlgoAlphaBreakoutIndicator(Indicator):
    """
    Detects dynamic consolidation channels and triggers explosive breakout entries.
    """

    def init(self):
        self.box_length = input.int(15, "Consolidation Box Length", minval=5, maxval=50, group="Channels")
        self.atr_period = input.int(14, "ATR Period", minval=5, maxval=50, group="Volatility")
        self.atr_mult   = input.float(1.5, "Target Multiplier", minval=0.5, step=0.1, group="Targets")

        self.last_signal_bar = -999

    def on_bar(self, option_bar: Dict[str, Any], history: pd.DataFrame) -> Optional[IndicatorSignal]:
        close = history["opt_close"].values if "opt_close" in history else history["close"].values
        high = history["opt_high"].values if "opt_high" in history else history["high"].values
        low = history["opt_low"].values if "opt_low" in history else history["low"].values
        open_ = history["opt_open"].values if "opt_open" in history else history["open"].values
        volume = history["opt_volume"].values if "opt_volume" in history else history["volume"].values

        n_bars = len(close)
        if n_bars < max(30, self.box_length.value + 5):
            return None

        close_f = close.astype(float)
        high_f = high.astype(float)
        low_f = low.astype(float)
        open_f = open_.astype(float)
        vol_f = volume.astype(float)

        channels = ta.breakout_channels(open_f, high_f, low_f, close_f, vol_f, box_len=self.box_length.value)
        atr = ta.atr(high_f, low_f, close_f, self.atr_period.value)

        upper = float(channels.top)
        lower = float(channels.bottom)
        c = float(close_f[-1])
        c_prev = float(close_f[-2])
        cur_atr = float(atr[-1]) if atr[-1] > 0 else 5.0

        self.plots = {
            "upper_box": upper,
            "lower_box": lower,
            "atr": cur_atr
        }

        # Upward Breakout
        if c > upper and c_prev <= upper:
            if (n_bars - self.last_signal_bar) > 5:
                self.last_signal_bar = n_bars
                sl = lower
                target = c + (upper - lower) * self.atr_mult.value
                return IndicatorSignal(
                    action="BUY_CE",
                    trigger_price=round(c, 2),
                    stop_loss=round(max(0.05, sl), 2),
                    target=round(target, 2),
                    tag="ALGO_BOX_BREAKOUT_CE",
                    timestamp=str(option_bar.get("timestamp", ""))
                )

        # Downward Breakdown
        if c < lower and c_prev >= lower:
            if (n_bars - self.last_signal_bar) > 5:
                self.last_signal_bar = n_bars
                sl = upper
                target = c - (upper - lower) * self.atr_mult.value
                return IndicatorSignal(
                    action="BUY_PE",
                    trigger_price=round(c, 2),
                    stop_loss=round(sl, 2),
                    target=round(max(0.05, target), 2),
                    tag="ALGO_BOX_BREAKDOWN_PE",
                    timestamp=str(option_bar.get("timestamp", ""))
                )

        return None
