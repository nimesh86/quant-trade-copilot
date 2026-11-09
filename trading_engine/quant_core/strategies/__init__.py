"""
Quant Core Strategies Package
──────────────────────────────
Exports standardized, zero-redundancy strategy evaluators for Backtest, Paper, and Live engines.
"""

from .unified_ai_trader import UnifiedAITraderStrategy, UnifiedSignal
from .nifty_features import compute_nifty_features, NIFTY_FEATURE_COLUMNS

__all__ = [
    "UnifiedAITraderStrategy",
    "UnifiedSignal",
    "compute_nifty_features",
    "NIFTY_FEATURE_COLUMNS",
]
