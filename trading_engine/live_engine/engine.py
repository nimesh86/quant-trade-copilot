"""
Live Engine: Master Orchestrator (Layer C).
───────────────────────────────────────────
Coordinates live tick streaming, Quant Core signal generation, institutional
sliced order routing, and real-time risk guardian position tracking.
Inherits / composes Quant Core (Layer A) with ZERO logic duplication.
"""
from typing import Dict, Any, Optional, List
from datetime import datetime, date
import calendar
import pandas as pd

from trading_engine.quant_core.contracts import Bar, Signal, Position, TradeRecord
from trading_engine.quant_core.signal_evaluator import SignalEvaluator
from trading_engine.quant_core.position_sizer import PositionSizer
from trading_engine.quant_core.lifecycle import TradeLifecycleManager
from trading_engine.live_engine.dispatcher import LiveOrderDispatcher
from trading_engine.live_engine.guardian import LiveGuardian
from trading_engine.config import get_symbol_lot_size


def format_trading_date_with_expiry(d: date, symbol: str = "NIFTY") -> str:
    """
    Formats trading date with Indian market expiry indicators:
      - (ME): Monthly Expiry (last Thursday of the month for NIFTY, or last Wednesday)
      - (WE): Weekly Expiry (other Thursdays / Wednesdays)
      - plain date otherwise
    """
    base_str = d.strftime("%Y-%m-%d")
    is_nifty = "BANK" not in symbol.upper()
    expiry_weekday = 3 if is_nifty else 2  # Thursday (3) or Wednesday (2)

    if d.weekday() != expiry_weekday:
        return base_str

    # Check if last Thursday/Wednesday of the month
    year, month = d.year, d.month
    cal = calendar.monthcalendar(year, month)
    # Find all Thursdays/Wednesdays in this month
    expiry_days = [week[expiry_weekday] for week in cal if week[expiry_weekday] != 0]

    if d.day == max(expiry_days):
        return f"{base_str} (ME)"
    else:
        return f"{base_str} (WE)"


class LiveEngine:
    """
    Institutional Live Trading Engine.
    Executes live orders via broker API with strict freeze limit slicing (<= 30 lots),
    real-time trailing stop ladders, and statutory risk controls.
    Composes Layer A (Quant Core) for all signal, pricing, and sizing decisions.
    """

    def __init__(
        self,
        capital: float = 500000.0,
        symbol: str = "NIFTY",
        indicator: str = "bss_pro_v5",
        broker_client=None,
        is_paper: bool = False,
        risk_per_trade_pct: float = 0.07,
        max_lots: int = 50,
        freeze_lots: int = 30,
        lot_size: Optional[int] = None,
        slice_delay_ms: int = 250
    ):
        self.capital = float(capital)
        self.symbol = symbol.upper()
        self.lot_size = lot_size or get_symbol_lot_size(self.symbol)
        self.freeze_lots = freeze_lots
        self.max_lots = max_lots

        # Layer A (Quant Core) components
        self.sizer = PositionSizer(
            risk_per_trade_pct=risk_per_trade_pct,
            max_lots=max_lots,
            default_freeze_lots=freeze_lots,
            default_lot_size=self.lot_size
        )
        self.lifecycle = TradeLifecycleManager()
        self.evaluator = SignalEvaluator(
            indicator_identifier=indicator,
            symbol=self.symbol
        )

        # Layer C components
        self.dispatcher = LiveOrderDispatcher(
            broker_client=broker_client,
            sizer=self.sizer,
            slice_delay_ms=slice_delay_ms,
            is_paper=is_paper
        )
        self.guardian = LiveGuardian(
            dispatcher=self.dispatcher,
            lifecycle=self.lifecycle
        )

        self.closed_trades: List[TradeRecord] = []

    def on_candle_close(self, cur_bar: Bar, history_df: pd.DataFrame) -> Optional[Signal]:
        """
        Invoked on each completed 1-minute candle from live stream.
        """
        # If in position, let guardian evaluate bar
        if self.guardian.active_position:
            record = self.guardian.on_tick(
                ltp=cur_bar.close,
                high=cur_bar.high,
                low=cur_bar.low,
                timestamp=cur_bar.timestamp
            )
            if record:
                self.closed_trades.append(record)
                return None

        # If not in position, evaluate for new entry
        if not self.guardian.active_position:
            signal = self.evaluator.evaluate(
                current_bar=cur_bar,
                history_df=history_df,
                active_position=None
            )
            if signal:
                pos, slices = self.dispatcher.dispatch_entry(
                    signal=signal,
                    capital=self.capital,
                    lot_size=self.lot_size,
                    freeze_lots=self.freeze_lots,
                    max_lots=self.max_lots
                )
                self.guardian.set_active_position(pos)
                return signal

        return None

    def on_tick(self, ltp: float, high: float, low: float, timestamp: datetime) -> Optional[TradeRecord]:
        """
        Invoked on every incoming sub-second market tick.
        """
        record = self.guardian.on_tick(ltp, high, low, timestamp)
        if record:
            self.closed_trades.append(record)
        return record

    def get_live_dashboard_log(self) -> List[Dict[str, Any]]:
        """
        Generates Live Trading Log rows for the Dashboard table.
        Uses 'Trading Date' column with (ME)/(WE) expiry tags as requested.
        """
        rows = []
        for t in self.closed_trades:
            trade_dt = datetime.strptime(t.date, "%Y-%m-%d").date()
            trading_date_formatted = format_trading_date_with_expiry(trade_dt, t.symbol)
            rows.append({
                "tradingDate": trading_date_formatted,
                "symbol": t.symbol,
                "strategy": t.indicator,
                "trades": 1,
                "winRate": "100.0%" if t.net_pnl > 0 else "0.0%",
                "pf": "99.99" if t.net_pnl > 0 else "0.00",
                "mdd": "0.0%",
                "gross": f"{'+₹' if t.gross_pnl >= 0 else '-₹'}{abs(int(t.gross_pnl)):,}",
                "charges": f"-₹{abs(int(t.charges)):,}",
                "net": f"{'+₹' if t.net_pnl >= 0 else '-₹'}{abs(int(t.net_pnl)):,}"
            })
        return rows
