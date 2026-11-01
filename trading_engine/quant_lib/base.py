"""
Core Shared Data Contracts and Base Classes for Quant Studio.
Authoritative source for IndicatorSignal, SynchronizedBar, InputDescriptor, and BaseIndicator.
"""
from dataclasses import dataclass, field
from typing import Dict, Any, Optional, List, Union
import numpy as np
import pandas as pd


@dataclass
class IndicatorSignal:
    """
    Universal Signal Contract produced by indicators and consumed by the execution engine.
    Automatically calculates risk_points, reward_points, and rr_ratio upon initialization.
    """
    action: str                 # "BUY_CE", "BUY_PE", "EXIT_CE", "EXIT_PE", "HOLD"
    trigger_price: float        # Execution price recommendation
    stop_loss: float = 0.0      # Invalidation price
    target: float = 0.0         # Primary profit target
    target_2: float = 0.0       # Secondary profit target
    risk_points: float = 0.0    # abs(trigger_price - stop_loss)
    reward_points: float = 0.0  # abs(target - trigger_price)
    rr_ratio: float = 0.0       # reward_points / risk_points
    confidence: float = 1.0     # 0.0 to 1.0 score
    tag: str = "SIGNAL"         # e.g. "BSS_PULLBACK", "BREAKOUT"
    timestamp: str = ""
    symbol: str = ""
    contract: str = ""
    extra: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        # Auto-calculate risk points if stop loss is specified
        if self.risk_points == 0.0 and self.stop_loss > 0.0:
            self.risk_points = round(abs(self.trigger_price - self.stop_loss), 2)
        # Auto-calculate reward points if target is specified
        if self.reward_points == 0.0 and self.target > 0.0:
            self.reward_points = round(abs(self.target - self.trigger_price), 2)
        # Auto-calculate reward-to-risk ratio
        if self.risk_points > 0.0 and self.rr_ratio == 0.0:
            self.rr_ratio = round(self.reward_points / self.risk_points, 2)

    def to_dict(self) -> Dict[str, Any]:
        from dataclasses import asdict
        return asdict(self)


@dataclass
class SynchronizedBar:
    """
    Synchronized Spot Index and Option Strike 1-minute OHLCV bundle.
    Guarantees strict zero-lookahead alignment between Spot and Option data.
    """
    timestamp: str
    spot_open: float
    spot_high: float
    spot_low: float
    spot_close: float
    spot_volume: int
    opt_open: float
    opt_high: float
    opt_low: float
    opt_close: float
    opt_volume: int
    opt_oi: int


class InputDescriptor:
    """
    Metadata container for an indicator parameter rendered as an interactive UI control.
    """
    def __init__(
        self,
        default: Any,
        title: str,
        input_type: str,
        minval: Optional[Union[int, float]] = None,
        maxval: Optional[Union[int, float]] = None,
        step: Optional[Union[int, float]] = None,
        options: Optional[List[str]] = None,
        group: str = "General",
        tooltip: str = ""
    ):
        self.default = default
        self.title = title
        self.input_type = input_type
        self.minval = minval
        self.maxval = maxval
        self.step = step
        self.options = options or []
        self.group = group
        self.tooltip = tooltip
        self.value = default

    def to_dict(self) -> Dict[str, Any]:
        return {
            "title": self.title,
            "type": self.input_type,
            "default": self.default,
            "current": self.value,
            "minval": self.minval,
            "maxval": self.maxval,
            "step": self.step,
            "options": self.options,
            "group": self.group,
            "tooltip": self.tooltip,
        }


class input:
    """Factory helper to declare inputs with Pine Script-identical ergonomics."""
    @staticmethod
    def int(default: int, title: str, minval: int = 1, maxval: int = 10000, step: int = 1, group: str = "General", tooltip: str = "") -> InputDescriptor:
        return InputDescriptor(default, title, "int", minval=minval, maxval=maxval, step=step, group=group, tooltip=tooltip)

    @staticmethod
    def float(default: float, title: str, minval: float = 0.0, maxval: float = 10000.0, step: float = 0.1, group: str = "General", tooltip: str = "") -> InputDescriptor:
        return InputDescriptor(default, title, "float", minval=minval, maxval=maxval, step=step, group=group, tooltip=tooltip)

    @staticmethod
    def bool(default: bool, title: str, group: str = "General", tooltip: str = "") -> InputDescriptor:
        return InputDescriptor(default, title, "bool", group=group, tooltip=tooltip)

    @staticmethod
    def string(default: str, title: str, options: Optional[List[str]] = None, group: str = "General", tooltip: str = "") -> InputDescriptor:
        return InputDescriptor(default, title, "string", options=options, group=group, tooltip=tooltip)

    @staticmethod
    def color(default: str, title: str, group: str = "Appearance", tooltip: str = "") -> InputDescriptor:
        return InputDescriptor(default, title, "color", group=group, tooltip=tooltip)


class BaseIndicator:
    """
    Abstract Base Class for all Indicators.
    Supports input introspection, historical warmup, candle close evaluation, and sub-second live ticks.
    """
    def __init__(self, **kwargs):
        self.plots: Dict[str, Any] = {}
        self.inputs: Dict[str, InputDescriptor] = {}
        self.init()
        # Bind any override kwargs passed to constructor
        for k, v in kwargs.items():
            if hasattr(self, k) and isinstance(getattr(self, k), InputDescriptor):
                getattr(self, k).value = v

    def init(self):
        """Override to declare input parameters."""
        pass

    def warm_up(self, historical_bars: pd.DataFrame):
        """Pre-populates rolling states (EMAs, RSIs, S/R levels) using historical bars."""
        pass

    def on_bar(self, bar: Dict[str, Any], history: Any) -> Optional[IndicatorSignal]:
        """
        Invoked on candle close (or during bar replay).
        Returns an IndicatorSignal if an entry or exit condition is confirmed, else None.
        """
        pass

    def on_tick(self, ltp: float, volume: int, timestamp: str) -> Optional[IndicatorSignal]:
        """
        Invoked on sub-second live tick updates.
        Returns an IndicatorSignal if an instant breakout or trailing SL invalidation occurs.
        """
        pass

    def signal(self, action: str, trigger_price: float, stop_loss: float = 0.0, target: float = 0.0, **kwargs) -> IndicatorSignal:
        """Helper to construct an IndicatorSignal with automatic risk/reward calculation."""
        return IndicatorSignal(action=action, trigger_price=trigger_price, stop_loss=stop_loss, target=target, **kwargs)


# Alias Indicator to BaseIndicator for clean user scripts
Indicator = BaseIndicator
