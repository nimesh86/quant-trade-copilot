"""
Live Engine: Real-Time Risk Guardian & Position Synchronizer (Layer C).
────────────────────────────────────────────────────────────────────────
Monitors live tick prices, updates trailing stop ladders (BE -> Lock -> Dynamic ATR),
enforces 15:15 IST intraday auto square-offs, and computes exact statutory fees.
Directly composes Quant Core (Layer A) TradeLifecycleManager with ZERO code duplication.
"""
from typing import Optional, Dict, Any, List
from datetime import datetime

from trading_engine.quant_core.contracts import Position, TradeRecord, ExitReason
from trading_engine.quant_core.lifecycle import TradeLifecycleManager
from trading_engine.live_engine.dispatcher import LiveOrderDispatcher


class LiveGuardian:
    """
    Real-time safety guardian and position lifecycle monitor.
    Composes Quant Core TradeLifecycleManager.
    """

    def __init__(
        self,
        dispatcher: LiveOrderDispatcher,
        lifecycle: Optional[TradeLifecycleManager] = None
    ):
        self.dispatcher = dispatcher
        self.lifecycle = lifecycle or TradeLifecycleManager()
        self.active_position: Optional[Position] = None
        self.live_trade_log: List[TradeRecord] = []

    def set_active_position(self, position: Position):
        self.active_position = position

    def on_tick(self, ltp: float, high: float, low: float, timestamp: datetime) -> Optional[TradeRecord]:
        """
        Evaluates incoming live tick against active position.
        Triggers emergency or target exit if thresholds are breached.
        """
        if not self.active_position:
            return None

        # Evaluate against Quant Core lifecycle manager
        trade_record = self.lifecycle.evaluate_bar(
            position=self.active_position,
            bar_high=max(high, ltp),
            bar_low=min(low, ltp),
            bar_close=ltp,
            timestamp=timestamp
        )

        if trade_record:
            # Trigger real broker exit dispatching
            self.dispatcher.dispatch_exit(
                position=self.active_position,
                reason=trade_record.exit_reason,
                exit_price=trade_record.exit_rate
            )
            self.live_trade_log.append(trade_record)
            self.active_position = None
            return trade_record

        return None
