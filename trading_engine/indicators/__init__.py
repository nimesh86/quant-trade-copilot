"""
Pre-Packaged Indicator Templates & Presets Library.
Reference indicators (BSS Pro v5, AlgoAlpha Breakout, Supertrend Cloud).
"""
from trading_engine.indicators.bss_pro_v5 import BSSProV5Indicator
from trading_engine.indicators.algoalpha_breakout import AlgoAlphaBreakoutIndicator
from trading_engine.indicators.supertrend_cloud import SupertrendCloudIndicator
from trading_engine.indicators.presets import PresetsManager

__all__ = [
    "BSSProV5Indicator",
    "AlgoAlphaBreakoutIndicator",
    "SupertrendCloudIndicator",
    "PresetsManager",
]
