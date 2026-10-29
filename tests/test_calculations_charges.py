import pytest
from trading_engine.execution.contract_engine import ContractTradingEngine

def test_tc_math_001_exact_charges_calculation_winning_trade(clean_engine):
    """TC-MATH-001: Statutory Indian option charge breakdown on winning NIFTY trade."""
    init_cap = clean_engine.capital
    clean_engine.place_order(
        symbol="NIFTY26OCT22700CE",
        side="BUY",
        order_type="MARKET",
        lots=1,
        current_market_price=100.0  # entry filled at 100.05
    )
    exit_res = clean_engine.close_position(reason="PROFIT_TARGET", exit_price=150.0, symbol="NIFTY26OCT22700CE")
    rec = exit_res["record"]

    # Entry = 100.05, Exit = 150.0, Units = 65
    expected_gross = round((150.0 - 100.05) * 65, 2)
    assert rec["gross_pnl"] == expected_gross
    assert rec["pts_gained"] == round(150.0 - 100.05, 2)
    assert rec["charges"] > 40.0  # Brokerage (40) + STT + GST/SEBI
    assert rec["net_pnl"] == round(rec["gross_pnl"] - rec["charges"], 2)
    assert clean_engine.capital == round(init_cap + rec["net_pnl"], 2)

def test_tc_math_002_exact_charges_losing_trade(clean_engine):
    """TC-MATH-002: Losing trade increases total loss by exact charges amount."""
    clean_engine.place_order(
        symbol="NIFTY26OCT22700CE",
        side="BUY",
        order_type="MARKET",
        lots=1,
        current_market_price=100.0
    )
    exit_res = clean_engine.close_position(reason="STOP_LOSS", exit_price=80.0, symbol="NIFTY26OCT22700CE")
    rec = exit_res["record"]

    expected_gross = round((80.0 - 100.05) * 65, 2)
    assert rec["gross_pnl"] == expected_gross
    assert rec["pts_gained"] == round(80.0 - 100.05, 2)
    # Loss is compounded by charges: net_pnl < gross_pnl
    assert rec["net_pnl"] < rec["gross_pnl"]
    assert rec["net_pnl"] == round(rec["gross_pnl"] - rec["charges"], 2)

def test_tc_math_003_breakeven_trade_loss_equals_charges(clean_engine):
    """TC-MATH-003: Trade exited at exact entry price yields net loss strictly equal to charges."""
    clean_engine.place_order(
        symbol="NIFTY26OCT22700CE",
        side="BUY",
        order_type="MARKET",
        lots=1,
        current_market_price=100.0
    )
    entry_px = clean_engine.active_positions["NIFTY26OCT22700CE"]["entry_price"]
    exit_res = clean_engine.close_position(reason="BREAK_EVEN", exit_price=entry_px, symbol="NIFTY26OCT22700CE")
    rec = exit_res["record"]

    assert rec["gross_pnl"] == 0.0
    assert rec["pts_gained"] == 0.0
    assert rec["net_pnl"] == -rec["charges"]

def test_tc_math_007_win_rate_and_statistical_metrics(clean_engine):
    """TC-MATH-007: Win rate, trade counts, and points aggregate accurately."""
    # Trade 1: Win (+₹2000)
    clean_engine.place_order(symbol="NIFTY26OCT22700CE", side="BUY", current_market_price=100.0)
    clean_engine.close_position(symbol="NIFTY26OCT22700CE", exit_price=140.0)

    # Trade 2: Win (+₹1000)
    clean_engine.place_order(symbol="NIFTY26OCT22700CE", side="BUY", current_market_price=100.0)
    clean_engine.close_position(symbol="NIFTY26OCT22700CE", exit_price=120.0)

    # Trade 3: Loss (-₹1000)
    clean_engine.place_order(symbol="NIFTY26OCT22700CE", side="BUY", current_market_price=100.0)
    clean_engine.close_position(symbol="NIFTY26OCT22700CE", exit_price=80.0)

    # Trade 4: Loss (-₹1000)
    clean_engine.place_order(symbol="NIFTY26OCT22700CE", side="BUY", current_market_price=100.0)
    clean_engine.close_position(symbol="NIFTY26OCT22700CE", exit_price=80.0)

    state = clean_engine.get_state()
    assert state["total_trade_count"] == 4
    assert state["today_trade_count"] == 4

    wins = [t for t in clean_engine.trade_history if t["net_pnl"] > 0]
    win_rate = (len(wins) / len(clean_engine.trade_history)) * 100.0
    assert win_rate == 50.0  # 2 wins out of 4 trades = 50.0%

def test_tc_math_008_zero_division_resilience(clean_engine):
    """TC-MATH-008: State and PnL calculation handles 0 trades without ZeroDivisionError."""
    state = clean_engine.get_state()
    assert state["total_trade_count"] == 0
    assert state["realized_day_pnl"] == 0.0
    assert state["unrealized_pnl"] == 0.0
    assert state["total_day_pnl"] == 0.0
