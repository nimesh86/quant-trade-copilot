"""
Live Engine: Institutional Order Dispatcher (Layer C).
───────────────────────────────────────────────────────
Dispatches multi-slice orders to real broker APIs (Zerodha Kite / Shoonya / Fyers).
Directly composes Quant Core (Layer A) PositionSizer to enforce identical
freeze-limit slicing (<= 30 lots) and slippage control with ZERO code duplication.
"""
import time
from typing import Dict, Any, List, Optional, Tuple
from datetime import datetime

from trading_engine.quant_core.contracts import OrderSlice, Signal, Position
from trading_engine.quant_core.position_sizer import PositionSizer
from trading_engine.config import get_symbol_lot_size


class LiveOrderDispatcher:
    """
    Executes live orders with sub-second throttling and Iceberg slicing.
    Composes Quant Core PositionSizer.
    """

    def __init__(
        self,
        broker_client=None,
        sizer: Optional[PositionSizer] = None,
        slice_delay_ms: int = 250,
        is_paper: bool = False
    ):
        self.broker = broker_client
        self.sizer = sizer or PositionSizer()
        self.slice_delay_ms = slice_delay_ms
        self.is_paper = is_paper or (broker_client is None)

    def dispatch_entry(
        self,
        signal: Signal,
        capital: float,
        lot_size: int,
        freeze_lots: int,
        max_lots: int = 50
    ) -> Tuple[Position, List[OrderSlice]]:
        """
        Sizes and slices an incoming Quant Core signal, then dispatches order legs.
        """
        stop_dist = max(5.0, abs(signal.price - signal.stop_loss))

        # 1. Quant Core Position Sizing
        lots, units, est_prem = self.sizer.calculate_lots(
            capital=capital,
            spot_price=signal.price,
            atr=signal.atr,
            stop_dist_pts=stop_dist,
            lot_size=lot_size,
            freeze_lots=freeze_lots,
            max_lots_cap=max_lots
        )

        # 2. Quant Core Institutional Order Slicing
        slices, eff_price = self.sizer.generate_slices(
            n_lots=lots,
            lot_size=lot_size,
            freeze_lots=freeze_lots,
            base_price=signal.price,
            side=signal.side
        )

        # 3. Execution routing
        for sl in slices:
            if not self.is_paper and self.broker:
                # Real broker API routing (e.g. Kite Connect)
                try:
                    order_id = self.broker.place_order(
                        variety="regular",
                        exchange="NFO",
                        tradingsymbol=f"{signal.symbol}26OCT{int(round(signal.price/50)*50)}{signal.side}",
                        transaction_type="BUY",
                        quantity=sl.units,
                        product="MIS",
                        order_type="MARKET"
                    )
                    sl.order_id = str(order_id)
                    sl.status = "FILLED"
                except Exception as e:
                    sl.status = "FAILED"
            else:
                # Paper execution
                sl.status = "FILLED"
                sl.order_id = f"SIM-{sl.slice_id}"

            if self.slice_delay_ms > 0:
                time.sleep(self.slice_delay_ms / 1000.0)

        trade_id = f"LIVE-{signal.timestamp.strftime('%Y%m%d')}-{int(time.time())}"

        pos = Position(
            id=trade_id,
            symbol=signal.symbol,
            side=signal.side,
            lots=lots,
            units=units,
            entry_time=signal.timestamp,
            entry_price=eff_price,
            entry_atr=signal.atr,
            initial_sl=signal.stop_loss,
            current_sl=signal.stop_loss,
            target_price=signal.target,
            peak_price=eff_price,
            trailing_stage=0,
            strategy_name=signal.indicator,
            slices=slices,
            metadata={"confidence": signal.probability, **signal.metadata}
        )

        return pos, slices

    def dispatch_exit(self, position: Position, reason: str, exit_price: float):
        """
        Dispatches square-off market orders across all active legs.
        """
        if self.is_paper or not self.broker:
            return

        for sl in position.slices:
            try:
                self.broker.place_order(
                    variety="regular",
                    exchange="NFO",
                    tradingsymbol=f"{position.symbol}26OCT{int(round(position.entry_price/50)*50)}{position.side}",
                    transaction_type="SELL",
                    quantity=sl.units,
                    product="MIS",
                    order_type="MARKET"
                )
            except Exception as e:
                pass
            if self.slice_delay_ms > 0:
                time.sleep(self.slice_delay_ms / 1000.0)
