"""
Quant Core: Institutional Position Sizer & Capital Allocator.
─────────────────────────────────────────────────────────────
Calculates exact lot sizing for Option Buyers and Option Sellers,
allocates risk capital, and slices orders exceeding exchange freeze limits.
Shared identically by Backtest Engine and Autonomous Live Engine.
"""
from typing import Dict, Any, List, Tuple
import math

try:
    from .contracts import OrderSlice, OptionContract
except ImportError:
    from contracts import OrderSlice, OptionContract


class PositionSizer:
    """
    Unified position sizing and statutory order slicing.
    Ensures identical lot sizing across Backtest simulation and Live broker routing.
    """

    def __init__(
        self,
        risk_per_trade_pct: float = 0.10,        # Max risk budget percentage (10%)
        max_capital_allocation_pct: float = 0.85,# Max capital to commit in single trade (85%)
        max_lots: int = 10,
        slippage_per_side: float = 0.40,         # Slippage in ₹ on market orders
        depth_slip_per_slice: float = 0.20
    ):
        self.risk_per_trade_pct = risk_per_trade_pct
        self.max_capital_allocation_pct = max_capital_allocation_pct
        self.max_lots = max_lots
        self.slippage_per_side = slippage_per_side
        self.depth_slip_per_slice = depth_slip_per_slice

    def calculate_option_buy_lots(
        self,
        capital: float,
        option_premium: float,
        contract: OptionContract,
        max_lots_cap: int = 0,
        sizing_mode: str = "conservative",
        stop_loss_points: float = 0.0
    ) -> Tuple[int, int, float]:
        """
        Calculates exact number of lots an option buyer can afford based on sizing mode:
          - "conservative": Capped at 10 lots (or max_lots_cap) with 85% usable capital cushion
          - "fixed_1": Strictly 1 lot (if capital >= cost)
          - "compounding_7pct": Compounding 7% equity risk per trade, up to 500 lots
          - "compounding_4pct": Compounding 4% equity risk per trade, up to 300 lots
        
        Returns:
            (n_lots, units, capital_required)
        """
        cap = max(1000.0, float(capital))
        premium = max(5.0, float(option_premium))
        lot_size = max(1, contract.lot_size)
        cost_per_lot = premium * lot_size

        mode_norm = (sizing_mode or "conservative").lower().strip()

        if mode_norm in ("fixed_1", "fixed_1_lot", "1_lot"):
            n_lots = 1
        elif mode_norm in ("compounding_7pct", "compounding", "aggressive"):
            # Aggressive Compounding (7% risk budget scaling from run_2cr_benchmark.py)
            opt_sl = max(3.5, stop_loss_points if stop_loss_points > 0 else (premium * 0.20))
            risk_per_lot = opt_sl * lot_size
            risk_lots = int((cap * 0.07) // risk_per_lot)
            limit = max_lots_cap if max_lots_cap > 0 else 500
            n_lots = max(1, min(max(1, risk_lots), limit))
        elif mode_norm in ("compounding_4pct", "moderate"):
            # Moderate Compounding (4% risk budget scaling)
            opt_sl = max(3.5, stop_loss_points if stop_loss_points > 0 else (premium * 0.20))
            risk_per_lot = opt_sl * lot_size
            risk_lots = int((cap * 0.04) // risk_per_lot)
            limit = max_lots_cap if max_lots_cap > 0 else 300
            n_lots = max(1, min(max(1, risk_lots), limit))
        else:
            # Conservative default: 85% cushion, max 10 lots
            usable_capital = cap * self.max_capital_allocation_pct
            affordable_lots = int(usable_capital // cost_per_lot)
            limit = max_lots_cap if max_lots_cap > 0 else self.max_lots
            n_lots = max(1, min(max(1, affordable_lots), limit))

        units = n_lots * lot_size
        capital_required = units * premium

        return n_lots, units, capital_required

    def generate_slices(
        self,
        n_lots: int,
        contract: OptionContract,
        base_price: float,
        side: str
    ) -> Tuple[List[OrderSlice], float]:
        """
        Slices order into exchange compliant legs (<= freeze_lots) and applies market depth slippage.
        """
        freeze = max(1, contract.freeze_lots)
        lot_size = max(1, contract.lot_size)
        rem_lots = max(1, int(n_lots))

        slices: List[OrderSlice] = []
        slice_idx = 1
        total_slippage = self.slippage_per_side

        while rem_lots > 0:
            take = min(rem_lots, freeze)
            slice_units = take * lot_size
            slices.append(
                OrderSlice(
                    slice_id=slice_idx,
                    lots=take,
                    units=slice_units,
                    limit_price=round(base_price + (slice_idx * self.depth_slip_per_slice), 2),
                    side=side,
                    trading_symbol=contract.symbol,
                    status="FILLED"
                )
            )
            rem_lots -= take
            if rem_lots > 0:
                total_slippage += self.depth_slip_per_slice
            slice_idx += 1

        effective_entry_price = round(base_price + total_slippage, 2)
        return slices, effective_entry_price
