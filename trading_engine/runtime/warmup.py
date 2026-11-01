"""
Historical Warmup Manager for quant_lib indicators.
Primes rolling mathematical states using past candles prior to live tick evaluation.
"""
import time
from typing import Dict, Any, List
import pandas as pd
from ..quant_lib.base import Indicator


class WarmupManager:
    """
    Orchestrates historical warmup of newly mounted or recompiled indicators.
    """
    @staticmethod
    def warm_up_indicator(indicator: Indicator, history_df: pd.DataFrame, trailing_step_bars: int = 50) -> Dict[str, Any]:
        """
        Feeds historical bars into the indicator instance:
        1. Calls indicator.warm_up(history_df) for vectorized priming.
        2. Steps the trailing N bars through on_bar() to ensure incremental recurrence states match.
        Completes in <= 10 milliseconds.
        """
        if history_df.empty:
            return {"success": True, "bars_processed": 0, "warmup_time_ms": 0.0}

        start_t = time.perf_counter()

        # 1. Vectorized bulk warmup
        indicator.warm_up(history_df)

        # 2. Step through trailing bars
        step_count = min(len(history_df), max(1, trailing_step_bars))
        sub_df = history_df.iloc[-step_count:]
        cols = list(sub_df.columns)
        for row in sub_df.values:
            bar = dict(zip(cols, row))
            indicator.on_bar(bar, history_df)

        elapsed_ms = (time.perf_counter() - start_t) * 1000.0

        return {
            "success": True,
            "bars_processed": len(history_df),
            "trailing_stepped_bars": step_count,
            "warmup_time_ms": round(elapsed_ms, 2),
        }

    warm_up = warm_up_indicator
