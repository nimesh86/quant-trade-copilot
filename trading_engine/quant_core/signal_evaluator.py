"""
Quant Core: Unified Lookahead-Free Signal Evaluator.
────────────────────────────────────────────────────
Executes indicator pipelines (BSS Pro v5/v6, AlgoAlpha, Supertrend)
on synchronized historical bars or live tick aggregates.
Converts indicator output into standardized Quant Core Signal contracts.
Shared identically by Backtest Engine (Layer B) and Live Engine (Layer C).
"""
from typing import Dict, Any, Optional, Union
from datetime import datetime
import pandas as pd
import numpy as np

try:
    from .contracts import Signal, Bar, Position
except ImportError:
    from contracts import Signal, Bar, Position

from trading_engine.quant_lib.base import Indicator, IndicatorSignal
from trading_engine.indicators.adaptive_trend_pro import AdaptiveTrendProIndicator
from trading_engine.indicators.bss_pro_v5 import BSSProV5Indicator
from trading_engine.indicators.bss_pro_v6 import BSSProV6Indicator
from trading_engine.indicators.algoalpha_breakout import AlgoAlphaBreakoutIndicator
from trading_engine.indicators.supertrend_cloud import SupertrendCloudIndicator


class SignalEvaluator:
    """
    Standardized Indicator Adapter & Signal Pipeline.
    Guarantees deterministic, lookahead-free signal generation identical in backtest & live.
    """

    INDICATOR_REGISTRY = {
        "adaptive_trend_pro": AdaptiveTrendProIndicator,
        "bss_pro_v5": BSSProV5Indicator,
        "bss_pro_v6": BSSProV6Indicator,
        "algoalpha_breakout": AlgoAlphaBreakoutIndicator,
        "supertrend_cloud": SupertrendCloudIndicator,
    }

    ALIAS_MAP = {
        "adaptive_trend_pro": "adaptive_trend_pro",
        "adaptive trend pro": "adaptive_trend_pro",
        "adaptive": "adaptive_trend_pro",
        "bss pro v5": "bss_pro_v5",
        "bss pro scalper v5": "bss_pro_v5",
        "bss_pro_v5": "bss_pro_v5",
        "bss pro v6": "bss_pro_v6",
        "bss pro scalper v6": "bss_pro_v6",
        "bss_pro_v6": "bss_pro_v6",
        "algoalpha": "algoalpha_breakout",
        "algoalpha_breakout": "algoalpha_breakout",
        "smart money breakout channels [algoalpha]": "algoalpha_breakout",
        "supertrend": "supertrend_cloud",
        "supertrend_cloud": "supertrend_cloud",
        "supertrend cloud & dynamic atr trailing": "supertrend_cloud",
    }

    def __init__(
        self,
        indicator_identifier: Union[str, Indicator] = "bss_pro_v5",
        params: Optional[Dict[str, Any]] = None,
        symbol: str = "NIFTY"
    ):
        self.symbol = symbol
        self.params = params or {}

        if isinstance(indicator_identifier, Indicator):
            self.indicator = indicator_identifier
            self.indicator_name = indicator_identifier.__class__.__name__
        else:
            norm_key = self.ALIAS_MAP.get(str(indicator_identifier).strip().lower(), "bss_pro_v5")
            indicator_cls = self.INDICATOR_REGISTRY.get(norm_key, BSSProV5Indicator)
            self.indicator = indicator_cls()
            self.indicator_name = self.indicator.__class__.__name__

        # Apply any custom parameters
        if self.params:
            for k, v in self.params.items():
                if hasattr(self.indicator, k):
                    attr = getattr(self.indicator, k)
                    if hasattr(attr, "value"):
                        attr.value = v
                    else:
                        setattr(self.indicator, k, v)

    def evaluate(
        self,
        current_bar: Bar,
        history_df: pd.DataFrame,
        active_position: Optional[Position] = None
    ) -> Optional[Signal]:
        """
        Evaluates the current bar in the context of history up to bar t.
        Strictly zero lookahead: only history_df up to t is available.
        """
        if history_df.empty or len(history_df) < 5:
            return None

        # Build option_bar dict matching indicator's on_bar interface
        option_bar = {
            "timestamp": current_bar.timestamp,
            "open": current_bar.open,
            "high": current_bar.high,
            "low": current_bar.low,
            "close": current_bar.close,
            "volume": current_bar.volume,
            "oi": current_bar.oi
        }

        # Active position context if in position
        active_trade_dict = None
        if active_position:
            active_trade_dict = {
                "side": active_position.side,
                "entry_price": active_position.entry_price,
                "entry_atr": active_position.entry_atr,
                "entry_time": active_position.entry_time,
                "initial_sl": active_position.initial_sl,
                "current_sl": active_position.current_sl,
                "target_price": active_position.target_price,
                "trailing_stage": active_position.trailing_stage
            }

        raw_signal: Optional[IndicatorSignal] = self.indicator.on_bar(
            option_bar=option_bar,
            history=history_df,
            active_trade=active_trade_dict
        )

        if not raw_signal:
            return None

        # Map actions: BUY_CE -> "CE", BUY_PE -> "PE"
        action = raw_signal.action.upper()
        if "BUY_CE" in action or action == "CE":
            side = "CE"
        elif "BUY_PE" in action or action == "PE":
            side = "PE"
        else:
            # EXIT or HOLD signals are handled by lifecycle manager
            return None

        # Compute or extract ATR from history
        atr_val = self._compute_latest_atr(history_df)

        sl = raw_signal.stop_loss if raw_signal.stop_loss > 0 else (
            current_bar.close - (1.5 * atr_val) if side == "CE" else current_bar.close + (1.5 * atr_val)
        )
        target = raw_signal.target if raw_signal.target > 0 else (
            current_bar.close + (2.0 * atr_val) if side == "CE" else current_bar.close - (2.0 * atr_val)
        )

        confidence = raw_signal.confidence if raw_signal.confidence > 0 else 0.75

        return Signal(
            timestamp=current_bar.timestamp,
            symbol=self.symbol,
            side=side,
            price=round(current_bar.close, 2),
            stop_loss=round(max(0.05, sl), 2),
            target=round(target, 2),
            atr=round(atr_val, 2),
            probability=confidence,
            indicator=self.indicator_name,
            metadata=raw_signal.extra if hasattr(raw_signal, "extra") else {}
        )

    @staticmethod
    def _compute_latest_atr(history_df: pd.DataFrame, period: int = 14) -> float:
        """Computes true range and ATR for the latest bar in history."""
        if len(history_df) < 2:
            return 15.0

        highs = history_df["spot_high"].values if "spot_high" in history_df else history_df["high"].values
        lows = history_df["spot_low"].values if "spot_low" in history_df else history_df["low"].values
        closes = history_df["spot_close"].values if "spot_close" in history_df else history_df["close"].values

        n = len(closes)
        lookback = min(n, period)
        if lookback < 2:
            return 15.0

        h = highs[-lookback:]
        l = lows[-lookback:]
        prev_c = closes[-lookback - 1 : -1] if n > lookback else closes[-lookback:]

        tr1 = h - l
        tr2 = np.abs(h - prev_c)
        tr3 = np.abs(l - prev_c)
        tr = np.maximum(tr1, np.maximum(tr2, tr3))
        atr_val = float(np.mean(tr))
        return max(5.0, atr_val)
