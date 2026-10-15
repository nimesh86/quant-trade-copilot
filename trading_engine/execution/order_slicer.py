"""
Institutional Order Slicing & Iceberg Execution Engine.
Complies with NSE Statutory Freeze Limits (30 lots / 1,950 units per order)
and provides automated sequential order dispatching with Level 2 depth modeling
and multi-strike liquidity dispersion.
"""
from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional, Tuple
from datetime import datetime, timedelta
import math
import time

try:
    from ..config import EngineConfig
except ImportError:
    from config import EngineConfig

@dataclass
class ChildSlice:
    slice_id: int
    strike: float
    option_type: str        # 'CE' | 'PE'
    trading_symbol: str
    lots: int
    units: int
    limit_price: float
    expected_fill_price: float
    slippage_pts: float
    brokerage_inr: float
    delay_ms: int
    status: str = "PENDING"
    fill_time: Optional[datetime] = None
    order_id: Optional[str] = None

@dataclass
class SlicedExecutionReport:
    parent_lots: int
    parent_units: int
    side: str
    total_slices: int
    vwap_price: float
    total_slippage_pts: float
    total_brokerage_inr: float
    total_friction_inr: float
    execution_time_ms: int
    slices: List[ChildSlice] = field(default_factory=list)
    multi_strike_used: bool = False

class OrderSlicer:
    def __init__(self, cfg: EngineConfig):
        self.cfg = cfg
        self.freeze_lots = getattr(cfg, "freeze_lots", 30)
        self.max_slices = getattr(cfg, "max_slices", 35)
        self.lot_size = getattr(cfg, "lot_size", 65)
        self.depth_slip_per_slice = getattr(cfg, "depth_slip_per_slice", 0.05)
        self.strike_dispersion = getattr(cfg, "strike_dispersion", True)
        self.slice_delay_ms = getattr(cfg, "slice_delay_ms", 250)

    def calculate_atm_strike(self, spot_price: float, strike_step: float = 50.0) -> float:
        """Finds nearest standard ATM strike (e.g. 25050 for 25034)."""
        return round(spot_price / strike_step) * strike_step

    def generate_child_slices(
        self,
        n_lots: int,
        side: str,                  # 'CE' or 'PE'
        base_option_price: float,
        spot_price: float,
        entry_time: datetime,
        strike_step: float = 50.0
    ) -> SlicedExecutionReport:
        """
        Breaks down a large parent order (up to 1,000+ lots) into statutory
        freeze compliant child orders. Uses strike dispersion if enabled and lots > 300.
        """
        atm_strike = self.calculate_atm_strike(spot_price, strike_step)

        # Determine strike allocations
        strike_allocations: List[Tuple[float, int, str]] = [] # (strike, lots, label)
        multi_strike = False

        if self.strike_dispersion and n_lots > 300:
            multi_strike = True
            # Split liquidity across 3 adjacent liquid strikes:
            # 50% ATM, 30% ITM-1, 20% OTM-1
            itm_strike = atm_strike - strike_step if side == "CE" else atm_strike + strike_step
            otm_strike = atm_strike + strike_step if side == "CE" else atm_strike - strike_step

            lots_atm = int(n_lots * 0.50)
            lots_itm = int(n_lots * 0.30)
            lots_otm = n_lots - (lots_atm + lots_itm)

            strike_allocations.append((atm_strike, lots_atm, "ATM"))
            strike_allocations.append((itm_strike, lots_itm, "ITM-1"))
            strike_allocations.append((otm_strike, lots_otm, "OTM-1"))
        else:
            strike_allocations.append((atm_strike, n_lots, "ATM"))

        # Generate child slices for each strike allocation
        child_slices: List[ChildSlice] = []
        global_slice_idx = 1
        total_slippage_sum = 0.0
        total_brokerage_sum = 0.0
        weighted_fill_sum = 0.0
        total_units_sum = 0

        for strike, bundle_lots, label in strike_allocations:
            rem_lots = bundle_lots
            # Approximate premium difference for ITM / OTM based on delta ~0.50
            strike_diff = (strike - atm_strike) if side == "CE" else (atm_strike - strike)
            strike_base_px = max(15.0, base_option_price - (strike_diff * 0.50))

            while rem_lots > 0:
                take_lots = min(rem_lots, self.freeze_lots)
                units = take_lots * self.lot_size
                rem_lots -= take_lots

                # Market depth impact:
                # Base bid-ask spread ₹0.50 + depth impact per consecutive slice
                depth_penalty = (global_slice_idx - 1) * self.depth_slip_per_slice
                total_leg_slip = self.cfg.slippage_per_side + depth_penalty
                fill_price = strike_base_px + total_leg_slip

                delay_ms = (global_slice_idx - 1) * self.slice_delay_ms
                fill_time = entry_time + timedelta(milliseconds=delay_ms)

                # ₹20 flat brokerage per leg
                leg_brokerage = self.cfg.brokerage_per_order

                trading_sym = f"{self.cfg.option_prefix}{int(strike)}{side}"

                child = ChildSlice(
                    slice_id=global_slice_idx,
                    strike=strike,
                    option_type=side,
                    trading_symbol=trading_sym,
                    lots=take_lots,
                    units=units,
                    limit_price=round(fill_price, 2),
                    expected_fill_price=round(fill_price, 2),
                    slippage_pts=round(total_leg_slip, 2),
                    brokerage_inr=leg_brokerage,
                    delay_ms=delay_ms,
                    status="FILLED",
                    fill_time=fill_time,
                    order_id=f"SL-{entry_time.strftime('%H%M%S')}-{global_slice_idx:02d}"
                )

                child_slices.append(child)
                weighted_fill_sum += fill_price * units
                total_units_sum += units
                total_slippage_sum += total_leg_slip * units
                total_brokerage_sum += leg_brokerage
                global_slice_idx += 1

        vwap_px = weighted_fill_sum / total_units_sum if total_units_sum > 0 else base_option_price
        avg_slippage = total_slippage_sum / total_units_sum if total_units_sum > 0 else 0.0

        # Statutory Turnovers & Friction:
        # NSE Exchange fee (0.03553%) + GST (18%) + STT on Sell
        turnover = vwap_px * total_units_sum
        exchange_fee = 0.0003553 * turnover
        gst = 0.18 * (total_brokerage_sum + exchange_fee)
        stamp_duty = 0.00003 * turnover
        total_friction = total_brokerage_sum + exchange_fee + gst + stamp_duty

        total_exec_time_ms = (len(child_slices) - 1) * self.slice_delay_ms

        report = SlicedExecutionReport(
            parent_lots=n_lots,
            parent_units=total_units_sum,
            side=side,
            total_slices=len(child_slices),
            vwap_price=round(vwap_px, 2),
            total_slippage_pts=round(avg_slippage, 2),
            total_brokerage_inr=round(total_brokerage_sum, 2),
            total_friction_inr=round(total_friction, 2),
            execution_time_ms=total_exec_time_ms,
            slices=child_slices,
            multi_strike_used=multi_strike
        )
        return report

    def format_execution_log(self, report: SlicedExecutionReport) -> str:
        """Formats an institutional-grade order fill receipt for logging."""
        lines = [
            f"[INSTITUTIONAL ICEBERG] Parent Order Filled: {report.parent_lots:,} lots ({report.parent_units:,} units) {report.side}",
            f"  ├── Slices: {report.total_slices} child legs (NSE Statutory Freeze <= {self.freeze_lots} lots)",
            f"  ├── Fill VWAP: ₹{report.vwap_price:.2f} | Avg Leg Slippage: ₹{report.total_slippage_pts:.2f}",
            f"  ├── Strike Dispersion: {'ACTIVE (ATM + ITM-1 + OTM-1)' if report.multi_strike_used else 'SINGLE ATM STRIKE'}",
            f"  ├── Total Execution Latency: {report.execution_time_ms} ms (~{report.execution_time_ms/1000:.2f}s)",
            f"  └── Total Order Friction: ₹{report.total_friction_inr:,.2f} (Brokerage: ₹{report.total_brokerage_inr:,.2f})"
        ]
        if len(report.slices) <= 5:
            for s in report.slices:
                lines.append(f"      • Leg #{s.slice_id:02d}: {s.trading_symbol} {s.lots:2d} lots ({s.units:5d} units) @ ₹{s.expected_fill_price:.2f} [{s.order_id}]")
        else:
            first_two = report.slices[:2]
            last_two = report.slices[-2:]
            for s in first_two:
                lines.append(f"      • Leg #{s.slice_id:02d}: {s.trading_symbol} {s.lots:2d} lots ({s.units:5d} units) @ ₹{s.expected_fill_price:.2f} [{s.order_id}]")
            lines.append(f"      • ... [{len(report.slices) - 4} intermediary freeze slices filled sequentially] ...")
            for s in last_two:
                lines.append(f"      • Leg #{s.slice_id:02d}: {s.trading_symbol} {s.lots:2d} lots ({s.units:5d} units) @ ₹{s.expected_fill_price:.2f} [{s.order_id}]")
        return "\n".join(lines)
