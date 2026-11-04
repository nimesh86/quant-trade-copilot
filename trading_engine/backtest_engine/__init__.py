"""
Layer B: Backtest Engine - Interactive & User-Driven
────────────────────────────────────────────────────
Simulates realistic trading on historical data, driven on-demand from:
  1. Main Web Application (Backtest Page)
  2. Quant Studio (VS Code Extension)
Strictly imports and reuses Quant Core mathematical and verification logic.
"""
from trading_engine.backtest_engine.feeder import BacktestFeeder
from trading_engine.backtest_engine.replayer import BacktestReplayer
from trading_engine.backtest_engine.scorecard import ScorecardGenerator
from trading_engine.backtest_engine.engine import BacktestEngine

__all__ = [
    "BacktestFeeder",
    "BacktestReplayer",
    "ScorecardGenerator",
    "BacktestEngine"
]
