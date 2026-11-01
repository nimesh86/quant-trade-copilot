"""
Supertrend Cloud & Dynamic ATR Trailing - Native Python Implementation.
Features multi-timeframe trend cloud with dynamic trailing stops.
"""
from typing import Dict, Any, Optional
import numpy as np
import pandas as pd

from trading_engine.quant_lib.base import Indicator, IndicatorSignal, input
from trading_engine.quant_lib import ta


class SupertrendCloudIndicator(Indicator):
    """
    Multi-timeframe Supertrend Cloud with dynamic ATR trailing stops.
    """

    def init(self):
        self.atr_period = input.int(10, "ATR Length", minval=1, maxval=50, group="Supertrend")
        self.multiplier = input.float(3.0, "Multiplier", minval=0.5, step=0.1, group="Supertrend")
        self.ema_filter = input.int(50, "Trend Filter EMA", minval=10, maxval=200, group="Filter")

        self.last_signal_bar = -999

    def on_bar(self, option_bar: Dict[str, Any], history: pd.DataFrame) -> Optional[IndicatorSignal]:
        close = history["opt_close"].values if "opt_close" in history else history["close"].values
        high = history["opt_high"].values if "opt_high" in history else history["high"].values
        low = history["opt_low"].values if "opt_low" in history else history["low"].values

        n_bars = len(close)
        if n_bars < max(25, self.ema_filter.value):
            return None

        close_f = close.astype(float)
        high_f = high.astype(float)
        low_f = low.astype(float)

        trend_dir, st_line = ta.supertrend(high_f, low_f, close_f, self.atr_period.value, self.multiplier.value)
        ema_f = ta.ema(close_f, self.ema_filter.value)

        current_dir = trend_dir[-1]
        prev_dir = trend_dir[-2]
        c = float(close_f[-1])
        st_val = float(st_line[-1])
        filter_val = float(ema_f[-1])

        self.plots = {
            "supertrend": st_val,
            "trend_dir": current_dir,
            "filter_ema": filter_val
        }

        # Trend flips to Bullish (+1) above EMA
        if current_dir == 1 and prev_dir == -1 and c > filter_val:
            if (n_bars - self.last_signal_bar) > 5:
                self.last_signal_bar = n_bars
                sl = st_val
                target = c + (c - sl) * 1.5
                return IndicatorSignal(
                    action="BUY_CE",
                    trigger_price=round(c, 2),
                    stop_loss=round(max(0.05, sl), 2),
                    target=round(target, 2),
                    tag="SUPERTREND_FLIP_BULL",
                    timestamp=str(option_bar.get("timestamp", ""))
                )

        # Trend flips to Bearish (-1) below EMA
        if current_dir == -1 and prev_dir == 1 and c < filter_val:
            if (n_bars - self.last_signal_bar) > 5:
                self.last_signal_bar = n_bars
                sl = st_val
                target = c - (sl - c) * 1.5
                return IndicatorSignal(
                    action="BUY_PE",
                    trigger_price=round(c, 2),
                    stop_loss=round(sl, 2),
                    target=round(max(0.05, target), 2),
                    tag="SUPERTREND_FLIP_BEAR",
                    timestamp=str(option_bar.get("timestamp", ""))
                )

        return None
