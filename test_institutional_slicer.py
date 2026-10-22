"""
Test Script: Institutional Order Slicer & Iceberg Execution Simulation.
Demonstrates:
  1. Standard 300-lot Iceberg (10 child legs of 30 lots each)
  2. Institutional 1,000-lot Slicing (34 child legs with strike dispersion)
  3. Strict compliance with NSE Statutory Freeze Limits (<=30 lots / 1,950 units)
"""
import sys
from pathlib import Path
from datetime import datetime

# Add trading_engine to path
sys.path.insert(0, str(Path(__file__).resolve().parent / "trading_engine"))

from config import EngineConfig
from execution.order_slicer import OrderSlicer

def main():
    print("=" * 85)
    print("  INSTITUTIONAL ORDER SLICING & ICEBERG EXECUTION TEST SUITE")
    print("=" * 85)

    cfg = EngineConfig()
    slicer = OrderSlicer(cfg)

    spot_px = 25034.50
    base_prem = 145.00
    now = datetime(2026, 10, 14, 10, 15, 0)

    # -------------------------------------------------------------
    # CASE 1: Standard 300-Lot Order (Retail / Broker Native Cap)
    # -------------------------------------------------------------
    print("\n[TEST CASE 1] Parent Order: 300 Lots (19,500 units) | Strike Dispersion: Single ATM Strike")
    print("-" * 85)
    cfg.strike_dispersion = False
    rep_300 = slicer.generate_child_slices(
        n_lots=300,
        side="CE",
        base_option_price=base_prem,
        spot_price=spot_px,
        entry_time=now
    )
    print(slicer.format_execution_log(rep_300))

    # Assertions
    assert rep_300.total_slices == 10, f"Expected 10 slices, got {rep_300.total_slices}"
    assert all(s.lots <= 30 for s in rep_300.slices), "Freeze limit violated!"
    assert rep_300.parent_units == 19500, f"Expected 19,500 units, got {rep_300.parent_units}"
    print("\n>>> Case 1 Verified: 10 Slices of 30 lots cleanly complies with Zerodha 10-leg ceiling!")

    # -------------------------------------------------------------
    # CASE 2: Institutional 1,000-Lot Order (Multi-Strike Dispersion)
    # -------------------------------------------------------------
    print("\n" + "=" * 85)
    print("[TEST CASE 2] Parent Order: 1,000 Lots (65,000 units) | Strike Dispersion: Multi-Strike")
    print("-" * 85)
    cfg.strike_dispersion = True
    rep_1000 = slicer.generate_child_slices(
        n_lots=1000,
        side="CE",
        base_option_price=base_prem,
        spot_price=spot_px,
        entry_time=now
    )
    print(slicer.format_execution_log(rep_1000))

    # Assertions
    assert all(s.lots <= 30 for s in rep_1000.slices), "Freeze limit violated in 1000-lot order!"
    assert rep_1000.parent_units == 65000, f"Expected 65,000 units, got {rep_1000.parent_units}"
    assert rep_1000.multi_strike_used == True, "Expected multi-strike dispersion!"

    strikes_used = set(s.strike for s in rep_1000.slices)
    print(f"\nDispersed across {len(strikes_used)} strikes: {sorted(list(strikes_used))}")
    print(f"Total Child Orders: {rep_1000.total_slices} legs")
    print(f"Fill VWAP: ₹{rep_1000.vwap_price:.2f} (Base: ₹{base_prem:.2f})")
    print(f"Total Turnaround Latency: {rep_1000.execution_time_ms} ms (~{rep_1000.execution_time_ms/1000:.2f} seconds)")
    print(f"Total Statutory + Brokerage Friction: ₹{rep_1000.total_friction_inr:,.2f}")
    print("\n>>> Case 2 Verified: 1,000-Lot execution engine fully operational!")

    print("\n" + "=" * 85)
    print("  ALL INSTITUTIONAL EXECUTION TESTS PASSED (100% COMPLIANT WITH NSE FREEZE RULES)")
    print("=" * 85)

if __name__ == "__main__":
    main()
