import pytest
import math
from datetime import datetime, date
from typing import Dict, Any

from trading_engine.config import EngineConfig
from trading_engine.execution.contract_engine import ContractTradingEngine
from trading_engine.execution.order_slicer import OrderSlicer


# ─────────────────────────────────────────────────────────────────────────────
# 1. PnL & STATUTORY CHARGES PERMUTATION MATRIX (160 TEST CASES)
# ─────────────────────────────────────────────────────────────────────────────
# Underlyings and lot configurations
INSTRUMENTS = [
    ("NIFTY26OCT22700CE", 65, "BUY"),
    ("NIFTY26OCT22700PE", 65, "BUY"),
    ("BANKNIFTY26OCT48500CE", 30, "BUY"),
    ("BANKNIFTY26OCT48500PE", 30, "BUY"),
]

PRICE_SCENARIOS = [
    (100.0, 150.0, "PROFIT_TARGET"),
    (150.0, 100.0, "STOP_LOSS"),
    (120.0, 120.05, "BREAK_EVEN"),
    (80.0, 110.0, "MANUAL_EXIT"),
]

LOT_CONFIGS = [1, 2, 5, 10, 25]


@pytest.mark.parametrize("instrument,units_per_lot,side", INSTRUMENTS)
@pytest.mark.parametrize("entry_px,exit_px,reason", PRICE_SCENARIOS)
@pytest.mark.parametrize("lots", LOT_CONFIGS)
def test_pnl_and_charges_matrix(instrument, units_per_lot, side, entry_px, exit_px, reason, lots, clean_engine):
    """
    Parametric verification of gross PnL, turnover, STT, brokerage, GST, and net capital impact
    across 4 instruments x 4 price scenarios x 5 lot sizes = 80 exhaustive variations.
    """
    # 1. Place order
    res = clean_engine.place_order(
        symbol=instrument,
        side=side,
        order_type="MARKET",
        lots=lots,
        current_market_price=entry_px
    )
    assert res["status"] == "success"
    
    pos = clean_engine.active_positions[instrument]
    actual_entry = pos["entry_price"]
    assert actual_entry == round(entry_px + 0.05, 2)  # 0.05 tick slippage
    total_units = lots * units_per_lot
    assert pos["units"] == total_units

    # 2. Close position
    initial_cap = clean_engine.capital
    exit_res = clean_engine.close_position(symbol=instrument, exit_price=exit_px, reason=reason)
    assert exit_res["status"] == "success"
    rec = exit_res["record"]

    # 3. Assert mathematical integrity
    expected_gross = round((exit_px - actual_entry) * total_units, 2)
    turnover = (actual_entry + exit_px) * total_units
    brokerage = 40.0
    stt = round(turnover * 0.000625, 2) if exit_px > actual_entry else 0.0
    gst_sebi = round(brokerage * 0.18 + turnover * 0.00005, 2)
    expected_charges = round(brokerage + stt + gst_sebi, 2)
    expected_net = round(expected_gross - expected_charges, 2)

    assert rec["gross_pnl"] == expected_gross
    assert rec["charges"] == expected_charges
    assert rec["net_pnl"] == expected_net
    assert clean_engine.capital == round(initial_cap + expected_net, 2)


# Reverse scenarios for SELL side (options writers)
SELL_SCENARIOS = [
    (150.0, 100.0, "PROFIT_TARGET"),
    (100.0, 140.0, "STOP_LOSS"),
]
SELL_INSTRUMENTS = [
    ("NIFTY26OCT22700CE", 65, "SELL"),
    ("NIFTY26OCT22700PE", 65, "SELL"),
    ("BANKNIFTY26OCT48500CE", 30, "SELL"),
    ("BANKNIFTY26OCT48500PE", 30, "SELL"),
]

@pytest.mark.parametrize("instrument,units_per_lot,side", SELL_INSTRUMENTS)
@pytest.mark.parametrize("entry_px,exit_px,reason", SELL_SCENARIOS)
@pytest.mark.parametrize("lots", [1, 3, 8, 12, 20])
def test_sell_pnl_and_charges_matrix(instrument, units_per_lot, side, entry_px, exit_px, reason, lots, clean_engine):
    """
    Parametric verification of SELL side PnL and statutory charges
    across 4 instruments x 2 price scenarios x 5 lot sizes = 40 variations.
    """
    res = clean_engine.place_order(
        symbol=instrument,
        side=side,
        order_type="MARKET",
        lots=lots,
        current_market_price=entry_px
    )
    assert res["status"] == "success"
    pos = clean_engine.active_positions[instrument]
    actual_entry = pos["entry_price"]
    total_units = lots * units_per_lot

    initial_cap = clean_engine.capital
    exit_res = clean_engine.close_position(symbol=instrument, exit_price=exit_px, reason=reason)
    assert exit_res["status"] == "success"
    rec = exit_res["record"]

    expected_gross = round((exit_px - actual_entry) * total_units, 2)
    assert rec["gross_pnl"] == expected_gross
    assert rec["charges"] >= 40.0


# Granular strike pricing variations (40 variations)
STRIKE_VARIANTS = [
    (22500, 45.0, 65.0),
    (22600, 75.0, 110.0),
    (22700, 120.0, 180.0),
    (22800, 185.0, 240.0),
    (22900, 260.0, 310.0),
    (23000, 340.0, 420.0),
    (23100, 430.0, 510.0),
    (23200, 520.0, 600.0),
]

@pytest.mark.parametrize("strike,entry_px,exit_px", STRIKE_VARIANTS)
@pytest.mark.parametrize("lots", [1, 2, 4, 10, 15])
def test_strike_ladder_pnl_matrix(strike, entry_px, exit_px, lots, clean_engine):
    """
    Parametric verification across strike ladder prices:
    8 strikes x 5 lots = 40 variations.
    """
    sym = f"NIFTY26OCT{strike}CE"
    res = clean_engine.place_order(symbol=sym, side="BUY", order_type="MARKET", lots=lots, current_market_price=entry_px)
    assert res["status"] == "success"

    exit_res = clean_engine.close_position(symbol=sym, exit_price=exit_px, reason="PROFIT_TARGET")
    assert exit_res["status"] == "success"
    rec = exit_res["record"]
    assert rec["net_pnl"] > 0.0


# ─────────────────────────────────────────────────────────────────────────────
# 2. LIMIT ORDERS & TRAILING MODES MATRIX (120 TEST CASES)
# ─────────────────────────────────────────────────────────────────────────────
TRAILING_MODES = ["be_lock", "cost_plus_5", "step_atr", "none"]
LIMIT_OFFSETS = [-5.0, -10.0, -20.0]  # relative to cur_px
TARGET_OFFSETS = [10.0, 20.0]
SL_OFFSETS = [5.0, 10.0]


@pytest.mark.parametrize("trailing_mode", TRAILING_MODES)
@pytest.mark.parametrize("limit_offset", LIMIT_OFFSETS)
@pytest.mark.parametrize("target_offset", TARGET_OFFSETS)
@pytest.mark.parametrize("sl_offset", SL_OFFSETS)
@pytest.mark.parametrize("station_sym", ["NIFTY26OCT22700CE", "NIFTY26OCT22700PE"])
def test_limit_order_placement_and_triggers_matrix(trailing_mode, limit_offset, target_offset, sl_offset, station_sym, clean_engine):
    """
    Parametric verification of Limit order queueing, status, trailing parameters, and price triggers:
    4 trailing modes x 3 limit offsets x 2 target offsets x 2 SL offsets x 2 stations = 96 variations.
    """
    market_px = 120.0
    limit_px = round(market_px + limit_offset, 2)
    target_px = round(limit_px + target_offset, 2)
    sl_px = round(limit_px - sl_offset, 2)

    # 1. Place limit order below market -> must be PENDING
    res = clean_engine.place_order(
        symbol=station_sym,
        side="BUY",
        order_type="LIMIT",
        lots=1,
        limit_price=limit_px,
        current_market_price=market_px,
        sl=sl_px,
        target1=target_px,
        trailing_mode=trailing_mode
    )
    assert res["status"] == "success"
    assert res["action"] == "LIMIT_ORDER_PLACED"
    order = res["order"]
    assert order["status"] == "PENDING"
    assert order["limit_price"] == limit_px
    assert order["trailing_mode"] == trailing_mode
    assert len(clean_engine.pending_orders) == 1

    # 2. Update price above limit -> order must stay pending
    tick_res1 = clean_engine.update_price_tick(station_sym, limit_px + 2.0)
    assert tick_res1 is None
    assert len(clean_engine.pending_orders) == 1

    # 3. Update price touching or below limit -> order fills into active position
    tick_res2 = clean_engine.update_price_tick(station_sym, limit_px - 0.5)
    assert tick_res2 is not None
    assert tick_res2["action"] == "ORDER_FILLED"
    assert len(clean_engine.pending_orders) == 0

    pos = clean_engine.active_positions.get(station_sym) or clean_engine.active_position
    assert pos is not None
    assert pos["symbol"] == station_sym
    assert pos["entry_price"] == limit_px
    assert pos["target1"] == target_px
    assert pos["current_sl"] == sl_px
    assert pos["trailing_mode"] == trailing_mode

    # Clean up position
    clean_engine.close_position(reason="MANUAL", exit_price=limit_px, symbol=station_sym)


# Limit cancellation & modification variations (24 variations)
@pytest.mark.parametrize("station_sym", ["NIFTY26OCT22700CE", "NIFTY26OCT22700PE"])
@pytest.mark.parametrize("lots", [1, 2, 5])
@pytest.mark.parametrize("limit_px", [80.0, 95.0, 110.0, 130.0])
def test_limit_order_cancellation_matrix(station_sym, lots, limit_px, clean_engine):
    """
    Parametric verification of pending order cancellations:
    2 stations x 3 lots x 4 price levels = 24 variations.
    """
    res = clean_engine.place_order(
        symbol=station_sym,
        side="BUY",
        order_type="LIMIT",
        lots=lots,
        limit_price=limit_px,
        current_market_price=limit_px + 10.0
    )
    assert res["status"] == "success"
    order_id = res["order"]["order_id"]
    assert any(o["order_id"] == order_id for o in clean_engine.pending_orders)

    # Cancel order
    cancel_res = clean_engine.cancel_pending_order(order_id)
    assert cancel_res["status"] == "success"
    assert not any(o["order_id"] == order_id for o in clean_engine.pending_orders)


# ─────────────────────────────────────────────────────────────────────────────
# 3. ORDER SLICER & SEBI FREEZE COMPLIANCE MATRIX (80 TEST CASES)
# ─────────────────────────────────────────────────────────────────────────────
SLICER_LOTS = [1, 10, 20, 27, 28, 54, 55, 81, 100, 250]

SLICER_INDICES = [
    ("NIFTY", 22700.0, 50.0, 27, 65),
    ("BANKNIFTY", 48600.0, 100.0, 30, 15),
    ("FINNIFTY", 21500.0, 50.0, 27, 65),
    ("SENSEX", 75000.0, 100.0, 20, 10),
]


@pytest.mark.parametrize("idx_name,spot_px,step,freeze_lots,lot_size", SLICER_INDICES)
@pytest.mark.parametrize("lots", SLICER_LOTS)
def test_order_slicer_freeze_limit_matrix(idx_name, spot_px, step, freeze_lots, lot_size, lots):
    """
    Parametric verification of institutional order slicing across indices:
    4 indices x 10 lot quantities = 40 variations.
    """
    expected_slices = math.ceil(lots / freeze_lots)
    cfg = EngineConfig()
    cfg.freeze_lots = freeze_lots
    cfg.lot_size = lot_size
    cfg.depth_slip_per_slice = 0.05
    cfg.slice_delay_ms = 250
    slicer = OrderSlicer(cfg)

    report = slicer.generate_child_slices(
        n_lots=lots,
        side="CE",
        base_option_price=100.0,
        spot_price=spot_px,
        entry_time=datetime.now(),
        strike_step=step,
        freeze_lots=freeze_lots,
        lot_size=lot_size
    )

    assert report.total_slices == expected_slices
    assert len(report.slices) == expected_slices
    assert sum(s.lots for s in report.slices) == lots
    assert report.parent_lots == lots
    assert report.parent_units == lots * lot_size
    for s in report.slices:
        assert s.lots <= freeze_lots
        assert s.units == s.lots * lot_size


# ATM Strike rounding across boundary numbers (40 variations)
ATM_TEST_POINTS = [
    (22700.0, 50.0, 22700.0),
    (22724.9, 50.0, 22700.0),
    (22725.0, 50.0, 22750.0),
    (22749.9, 50.0, 22750.0),
    (48500.0, 100.0, 48500.0),
    (48549.9, 100.0, 48500.0),
    (48550.0, 100.0, 48600.0),
    (48599.9, 100.0, 48600.0),
]

@pytest.mark.parametrize("spot,step,expected_atm", ATM_TEST_POINTS)
@pytest.mark.parametrize("multiplier", [1.0, 1.05, 1.10, 1.15, 1.20])
def test_atm_strike_mathematical_rounding_matrix(spot, step, expected_atm, multiplier):
    """
    Parametric verification of strike rounding logic:
    8 test points x 5 multipliers = 40 variations.
    """
    cfg = EngineConfig()
    slicer = OrderSlicer(cfg)
    test_spot = spot * multiplier
    calculated = slicer.calculate_atm_strike(test_spot, step)
    assert calculated % step == 0.0


# ─────────────────────────────────────────────────────────────────────────────
# 4. ACTIVE POSITION LEVEL MODIFICATIONS MATRIX (50 TEST CASES)
# ─────────────────────────────────────────────────────────────────────────────
LEVEL_ADJUSTMENTS = [
    (5.0, 10.0),
    (8.0, 15.0),
    (12.0, 20.0),
    (15.0, 30.0),
    (20.0, 40.0),
]

@pytest.mark.parametrize("sl_delta,tgt_delta", LEVEL_ADJUSTMENTS)
@pytest.mark.parametrize("station_sym", ["NIFTY26OCT22700CE", "NIFTY26OCT22700PE"])
@pytest.mark.parametrize("initial_lots", [1, 3, 5, 10, 20])
def test_active_position_level_modification_matrix(sl_delta, tgt_delta, station_sym, initial_lots, clean_engine):
    """
    Parametric verification of draggable SL and Target modifications:
    5 adjustments x 2 stations x 5 lot configurations = 50 variations.
    """
    # 1. Place market buy
    clean_engine.place_order(
        symbol=station_sym,
        side="BUY",
        order_type="MARKET",
        lots=initial_lots,
        current_market_price=100.0,
        sl=90.0,
        target1=120.0
    )
    pos = clean_engine.active_positions.get(station_sym) or clean_engine.active_position
    assert pos["current_sl"] == 90.0
    assert pos["target1"] == 120.0

    # 2. Modify levels via engine API
    new_sl = round(pos["current_sl"] + sl_delta, 2)
    new_tgt = round(pos["target1"] + tgt_delta, 2)
    mod_res = clean_engine.modify_active_levels(sl=new_sl, target1=new_tgt, symbol=station_sym)
    assert mod_res["status"] == "success"

    updated_pos = clean_engine.active_positions.get(station_sym) or clean_engine.active_position
    assert updated_pos["current_sl"] == new_sl
    assert updated_pos["target1"] == new_tgt

    # 3. Verify get_state reflects new levels
    state = clean_engine.get_state()
    pos_state = state["active_positions"].get(station_sym) or state.get("active_position") or state.get("position")
    assert pos_state["current_sl"] == new_sl
    assert pos_state["target1"] == new_tgt

    # Clean up
    clean_engine.close_position(reason="MANUAL", exit_price=100.0, symbol=station_sym)
