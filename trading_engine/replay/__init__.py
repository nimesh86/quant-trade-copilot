"""
Replay & Backtest Engine Core
Zero-lookahead simulation, step-by-step debugger, Break on Signal, and vectorized backtester.
"""
from trading_engine.replay.session import ReplayFrame, ReplaySession
from trading_engine.replay.breakpoints import BreakpointManager
from trading_engine.replay.backtester import (
    TradeRecord,
    BacktestScorecard,
    VectorizedBacktester,
)

__all__ = [
    "ReplayFrame",
    "ReplaySession",
    "BreakpointManager",
    "TradeRecord",
    "BacktestScorecard",
    "VectorizedBacktester",
]
