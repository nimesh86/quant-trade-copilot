import pytest
from datetime import datetime
from trading_engine.config import EngineConfig
from trading_engine.execution.order_slicer import OrderSlicer, ChildSlice, SlicedExecutionReport

@pytest.fixture
def slicer():
    cfg = EngineConfig()
    cfg.freeze_lots = 27  # SEBI limit for NIFTY (1,800 units / 65)
    cfg.lot_size = 65
    cfg.depth_slip_per_slice = 0.05
    cfg.slice_delay_ms = 250
    return OrderSlicer(cfg)

def test_tc_slice_01_atm_strike_calculation(slicer):
    """TC-SLICE-01: ATM strike calculation for NIFTY and BANKNIFTY."""
    # NIFTY 50 step
    assert slicer.calculate_atm_strike(22718.5, 50.0) == 22700.0
    assert slicer.calculate_atm_strike(22735.0, 50.0) == 22750.0
    # BANKNIFTY 100 step
    assert slicer.calculate_atm_strike(48640.0, 100.0) == 48600.0
    assert slicer.calculate_atm_strike(48660.0, 100.0) == 48700.0

def test_tc_slice_02_order_below_freeze_limit(slicer):
    """TC-SLICE-02: Order with lots <= freeze limit creates exactly 1 slice."""
    report = slicer.generate_child_slices(
        n_lots=20,
        side="CE",
        base_option_price=150.0,
        spot_price=22700.0,
        entry_time=datetime(2026, 10, 26, 10, 0, 0),
        strike_step=50.0
    )
    assert report.total_slices == 1
    assert len(report.slices) == 1
    assert report.slices[0].lots == 20
    assert report.slices[0].units == 20 * 65
    assert report.slices[0].status == "FILLED"
    assert report.multi_strike_used is False

def test_tc_slice_03_order_exceeding_sebi_freeze_limit(slicer):
    """TC-SLICE-03: Order with 60 lots sliced into compliant tranches <= 27 lots."""
    report = slicer.generate_child_slices(
        n_lots=60,
        side="CE",
        base_option_price=120.0,
        spot_price=22700.0,
        entry_time=datetime(2026, 10, 26, 10, 0, 0),
        strike_step=50.0
    )
    # 60 lots with freeze=27 -> [27, 27, 6]
    assert report.total_slices == 3
    assert len(report.slices) == 3
    assert [s.lots for s in report.slices] == [27, 27, 6]
    assert sum(s.lots for s in report.slices) == 60
    assert report.parent_lots == 60
    assert report.parent_units == 60 * 65

def test_tc_slice_04_sequential_delays_and_depth_impact(slicer):
    """TC-SLICE-04: Slices receive sequential timestamp delays and market depth slippage."""
    report = slicer.generate_child_slices(
        n_lots=81,
        side="PE",
        base_option_price=200.0,
        spot_price=22700.0,
        entry_time=datetime(2026, 10, 26, 11, 0, 0),
        strike_step=50.0
    )
    # 81 lots -> [27, 27, 27]
    assert report.total_slices == 3
    s1, s2, s3 = report.slices
    assert s1.delay_ms == 0
    assert s2.delay_ms == 250
    assert s3.delay_ms == 500
    assert s2.expected_fill_price > s1.expected_fill_price
    assert s3.expected_fill_price > s2.expected_fill_price
    assert report.execution_time_ms == 500

def test_tc_slice_05_multi_strike_dispersion_ultra_large_order(slicer):
    """TC-SLICE-05: Institutional order > 300 lots disperses liquidity across ATM, ITM, and OTM."""
    report = slicer.generate_child_slices(
        n_lots=400,
        side="CE",
        base_option_price=180.0,
        spot_price=22700.0,
        entry_time=datetime(2026, 10, 26, 11, 30, 0),
        strike_step=50.0
    )
    assert report.multi_strike_used is True
    assert report.parent_lots == 400
    strikes = {s.strike for s in report.slices}
    # 22700 (ATM), 22650 (ITM-1 for CE), 22750 (OTM-1 for CE)
    assert 22700.0 in strikes
    assert 22650.0 in strikes
    assert 22750.0 in strikes
    assert sum(s.lots for s in report.slices) == 400

def test_tc_slice_06_banknifty_lot_size_resolution(slicer):
    """TC-SLICE-06: Slicer honors custom freeze limit and lot size for BANKNIFTY."""
    report = slicer.generate_child_slices(
        n_lots=50,
        side="PE",
        base_option_price=350.0,
        spot_price=48700.0,
        entry_time=datetime(2026, 10, 26, 12, 0, 0),
        strike_step=100.0,
        freeze_lots=30,
        lot_size=15
    )
    # 50 lots with freeze=30 -> [30, 20]
    assert report.total_slices == 2
    assert [s.lots for s in report.slices] == [30, 20]
    assert report.slices[0].units == 30 * 15
    assert report.slices[1].units == 20 * 15
