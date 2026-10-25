import pytest
from trading_engine.execution.contract_engine import ContractTradingEngine

def test_tc_eng_01_open_ce_market_buy_order(clean_engine):
    """TC-ENG-01: Open CE Market Buy Order creates active position and updates capital."""
    res = clean_engine.place_order(
        symbol="NIFTY26OCT22700CE",
        side="BUY",
        order_type="MARKET",
        lots=1,
        current_market_price=100.0
    )
    assert res["status"] == "success"
    assert res["action"] == "ORDER_FILLED"
    pos = res["position"]
    assert pos["symbol"] == "NIFTY26OCT22700CE"
    assert pos["units"] == 65
    assert pos["entry_price"] == 100.05  # 100.0 + 0.05 tick slippage
    assert "NIFTY26OCT22700CE" in clean_engine.active_positions

def test_tc_eng_02_pe_buy_with_ce_open_isolation(clean_engine):
    """TC-ENG-02 & TC-TRAP-003: Buying PE while in CE MUST open PE independently and NOT average into CE."""
    # 1. Buy CE
    res_ce = clean_engine.place_order(
        symbol="NIFTY26OCT22700CE",
        side="BUY",
        order_type="MARKET",
        lots=1,
        current_market_price=120.0
    )
    assert res_ce["status"] == "success"

    # 2. Buy PE
    res_pe = clean_engine.place_order(
        symbol="NIFTY26OCT22700PE",
        side="BUY",
        order_type="MARKET",
        lots=1,
        current_market_price=85.0
    )
    assert res_pe["status"] == "success"
    assert res_pe["action"] == "ORDER_FILLED"
    assert res_pe["position"]["symbol"] == "NIFTY26OCT22700PE"

    # Assert both positions exist independently
    assert "NIFTY26OCT22700CE" in clean_engine.active_positions
    assert "NIFTY26OCT22700PE" in clean_engine.active_positions
    assert clean_engine.active_positions["NIFTY26OCT22700CE"]["units"] == 65
    assert clean_engine.active_positions["NIFTY26OCT22700PE"]["units"] == 65

def test_tc_eng_03_average_into_existing_ce_position(clean_engine):
    """TC-ENG-03: Placing second buy on CE averages price and increases units."""
    clean_engine.place_order(
        symbol="NIFTY26OCT22700CE",
        side="BUY",
        order_type="MARKET",
        lots=1,
        current_market_price=100.0
    )
    res2 = clean_engine.place_order(
        symbol="NIFTY26OCT22700CE",
        side="BUY",
        order_type="MARKET",
        lots=1,
        current_market_price=120.0
    )
    assert res2["status"] == "success"
    assert res2["action"] == "POSITION_INCREASED"
    pos = clean_engine.active_positions["NIFTY26OCT22700CE"]
    assert pos["units"] == 130
    assert pos["lots"] == 2
    # First entry was 100.05, second was 120.05 -> Average is 110.05
    assert pos["entry_price"] == 110.05

def test_tc_eng_04_opposite_order_reverses_or_closes(clean_engine):
    """TC-ENG-04: Opposite side order closes existing position."""
    clean_engine.place_order(
        symbol="NIFTY26OCT22700CE",
        side="BUY",
        order_type="MARKET",
        lots=1,
        current_market_price=100.0
    )
    res_sell = clean_engine.place_order(
        symbol="NIFTY26OCT22700CE",
        side="SELL",
        order_type="MARKET",
        lots=1,
        current_market_price=110.0
    )
    assert res_sell["status"] == "success"
    # Position closed due to reverse order
    assert len(clean_engine.trade_history) == 1
    assert clean_engine.trade_history[0]["reason"] == "REVERSE_ORDER"

def test_tc_eng_05_manual_exit_ce_leaves_pe_intact(clean_engine):
    """TC-ENG-05: Exiting CE position leaves PE position open and unaffected."""
    clean_engine.place_order(symbol="NIFTY26OCT22700CE", side="BUY", current_market_price=100.0)
    clean_engine.place_order(symbol="NIFTY26OCT22700PE", side="BUY", current_market_price=80.0)

    # Close only CE
    res_exit = clean_engine.close_position(reason="MANUAL_CLOSE", exit_price=110.0, symbol="NIFTY26OCT22700CE")
    assert res_exit["status"] == "success"
    assert "NIFTY26OCT22700CE" not in clean_engine.active_positions
    assert "NIFTY26OCT22700PE" in clean_engine.active_positions
    assert clean_engine.active_positions["NIFTY26OCT22700PE"]["units"] == 65

def test_tc_eng_06_manual_exit_pe_leaves_ce_intact(clean_engine):
    """TC-ENG-06: Exiting PE position leaves CE position open and unaffected."""
    clean_engine.place_order(symbol="NIFTY26OCT22700CE", side="BUY", current_market_price=100.0)
    clean_engine.place_order(symbol="NIFTY26OCT22700PE", side="BUY", current_market_price=80.0)

    # Close only PE
    res_exit = clean_engine.close_position(reason="MANUAL_CLOSE", exit_price=90.0, symbol="NIFTY26OCT22700PE")
    assert res_exit["status"] == "success"
    assert "NIFTY26OCT22700PE" not in clean_engine.active_positions
    assert "NIFTY26OCT22700CE" in clean_engine.active_positions
    assert clean_engine.active_positions["NIFTY26OCT22700CE"]["units"] == 65

def test_tc_eng_07_ratchet_t1_be_lock(clean_engine):
    """TC-ENG-07: When price hits Target 1, SL is moved to Breakeven (Entry Price)."""
    clean_engine.place_order(
        symbol="NIFTY26OCT22700CE",
        side="BUY",
        order_type="MARKET",
        lots=1,
        sl=90.0,
        target1=120.0,
        target2=140.0,
        trailing_mode="be_lock",
        current_market_price=100.0
    )
    pos = clean_engine.active_positions["NIFTY26OCT22700CE"]
    assert pos["current_sl"] == 90.0
    assert not pos["be_locked"]

    # Price moves to 121.0 (exceeds target 1)
    clean_engine.update_price_tick("NIFTY26OCT22700CE", 121.0)
    pos = clean_engine.active_positions["NIFTY26OCT22700CE"]
    assert pos["be_locked"] is True
    assert pos["current_sl"] >= 100.05  # Moved to breakeven

def test_tc_eng_08_ratchet_t2_automatic_profit_exit(clean_engine):
    """TC-ENG-08: When price reaches Target 2, position automatically closes with TARGET_2_HIT."""
    clean_engine.place_order(
        symbol="NIFTY26OCT22700CE",
        side="BUY",
        order_type="MARKET",
        lots=1,
        target1=120.0,
        target2=140.0,
        current_market_price=100.0
    )
    # Price hits Target 2
    res = clean_engine.update_price_tick("NIFTY26OCT22700CE", 140.5)
    assert res is not None
    assert res["status"] == "success"
    assert res["action"] == "POSITION_CLOSED"
    assert res["record"]["reason"] == "TARGET_2_HIT"
    assert "NIFTY26OCT22700CE" not in clean_engine.active_positions

def test_tc_eng_09_stop_loss_trigger(clean_engine):
    """TC-ENG-09: When price falls to or below SL, position automatically exits with STOP_LOSS_HIT."""
    clean_engine.place_order(
        symbol="NIFTY26OCT22700CE",
        side="BUY",
        order_type="MARKET",
        lots=1,
        sl=90.0,
        current_market_price=100.0
    )
    # Price drops to 89.5
    res = clean_engine.update_price_tick("NIFTY26OCT22700CE", 89.5)
    assert res is not None
    assert res["status"] == "success"
    assert res["action"] == "POSITION_CLOSED"
    assert res["record"]["reason"] == "STOP_LOSS_HIT"
    assert "NIFTY26OCT22700CE" not in clean_engine.active_positions

def test_tc_eng_10_kill_switch_flattens_and_locks(clean_engine):
    """TC-ENG-10: Emergency Kill Switch flattens all positions, cancels orders, and prevents new orders."""
    clean_engine.place_order(symbol="NIFTY26OCT22700CE", side="BUY", current_market_price=100.0)
    clean_engine.place_order(
        symbol="NIFTY26OCT22700CE",
        side="BUY",
        order_type="LIMIT",
        limit_price=90.0,
        current_market_price=100.0
    )
    assert len(clean_engine.pending_orders) == 1

    # Trigger Kill Switch
    ks_res = clean_engine.trigger_kill_switch()
    assert ks_res["status"] == "success"
    assert clean_engine.is_kill_switch_active is True
    assert len(clean_engine.active_positions) == 0
    assert len(clean_engine.pending_orders) == 0

    # Attempt to place order while locked
    blocked_res = clean_engine.place_order(symbol="NIFTY26OCT22700CE", side="BUY", current_market_price=100.0)
    assert blocked_res["status"] == "error"
    assert "Kill switch is ACTIVE" in blocked_res["message"]

    # Unlock Kill Switch
    unlock_res = clean_engine.unlock_kill_switch()
    assert unlock_res["status"] == "success"
    assert clean_engine.is_kill_switch_active is False

    # Placing order now succeeds
    ok_res = clean_engine.place_order(symbol="NIFTY26OCT22700CE", side="BUY", current_market_price=100.0)
    assert ok_res["status"] == "success"

def test_tc_eng_11_pending_limit_order_fill(clean_engine):
    """TC-ENG-11: Limit order sits pending until market price reaches limit price."""
    res = clean_engine.place_order(
        symbol="NIFTY26OCT22700CE",
        side="BUY",
        order_type="LIMIT",
        limit_price=95.0,
        current_market_price=100.0
    )
    assert res["action"] == "LIMIT_ORDER_PLACED"
    assert len(clean_engine.pending_orders) == 1

    # Price stays above limit (97.0) -> No fill
    clean_engine.update_price_tick("NIFTY26OCT22700CE", 97.0)
    assert len(clean_engine.pending_orders) == 1
    assert "NIFTY26OCT22700CE" not in clean_engine.active_positions

    # Price drops to 94.5 (satisfies limit of 95.0) -> Filled!
    fill_res = clean_engine.update_price_tick("NIFTY26OCT22700CE", 94.5)
    assert fill_res["action"] == "ORDER_FILLED"
    assert len(clean_engine.pending_orders) == 0
    assert "NIFTY26OCT22700CE" in clean_engine.active_positions
    assert clean_engine.active_positions["NIFTY26OCT22700CE"]["entry_price"] == 95.0

def test_tc_eng_12_cancel_pending_limit_order(clean_engine):
    """TC-ENG-12: Limit orders can be cancelled cleanly."""
    clean_engine.place_order(
        symbol="NIFTY26OCT22700CE",
        side="BUY",
        order_type="LIMIT",
        limit_price=90.0,
        current_market_price=100.0
    )
    assert len(clean_engine.pending_orders) == 1
    clean_engine.cancel_pending_order()
    assert len(clean_engine.pending_orders) == 0

def test_tc_eng_13_different_index_lot_sizes(clean_engine):
    """TC-ENG-13: Engine accurately resolves lot sizes for BANKNIFTY, SENSEX, and NIFTY."""
    # NIFTY = 65
    clean_engine.place_order(symbol="NIFTY26OCT22700CE", side="BUY", lots=1, current_market_price=100.0)
    assert clean_engine.active_positions["NIFTY26OCT22700CE"]["units"] == 65
    clean_engine.close_position(symbol="NIFTY26OCT22700CE", exit_price=100.0)

    # BANKNIFTY = 30
    clean_engine.place_order(symbol="BANKNIFTY26OCT51200CE", side="BUY", lots=1, current_market_price=200.0)
    assert clean_engine.active_positions["BANKNIFTY26OCT51200CE"]["units"] == 30
    clean_engine.close_position(symbol="BANKNIFTY26OCT51200CE", exit_price=200.0)

    # SENSEX = 20
    clean_engine.place_order(symbol="SENSEX26OCT80000CE", side="BUY", lots=1, current_market_price=250.0)
    assert clean_engine.active_positions["SENSEX26OCT80000CE"]["units"] == 20

def test_tc_eng_14_account_reset(clean_engine):
    """TC-ENG-14: Account reset restores initial capital and wipes active trades."""
    clean_engine.place_order(symbol="NIFTY26OCT22700CE", side="BUY", current_market_price=100.0)
    clean_engine.reset_account(capital=75000.0)
    assert clean_engine.capital == 75000.0
    assert len(clean_engine.active_positions) == 0
    assert len(clean_engine.trade_history) == 0

def test_tc_eng_15_modify_levels_on_the_fly(clean_engine):
    """TC-ENG-15: Modify levels updates SL, Target 1, Target 2 on active position."""
    clean_engine.place_order(symbol="NIFTY26OCT22700CE", side="BUY", current_market_price=100.0)
    res = clean_engine.modify_active_levels(sl=95.0, target1=115.0, target2=130.0)
    assert res["status"] == "success"
    pos = clean_engine.active_position
    assert pos["current_sl"] == 95.0
    assert pos["target1"] == 115.0
    assert pos["target2"] == 130.0

def test_tc_eng_16_insufficient_margin_protection(clean_engine):
    """TC-ENG-16: Order requiring more capital than available is safely rejected."""
    clean_engine.capital = 500.0  # Only ₹500
    res = clean_engine.place_order(symbol="NIFTY26OCT22700CE", side="BUY", lots=1, current_market_price=100.0)
    # Margin required = 100.05 * 65 = 6503.25 > 500.0
    assert res["status"] == "error"
    assert "Insufficient margin" in res["message"]

def test_tc_eng_17_mode_switching(clean_engine):
    """TC-ENG-17: Switching mode between paper, live, replay, and rejecting invalid mode."""
    assert clean_engine.set_mode("live")["status"] == "success"
    assert clean_engine.mode == "live"
    assert clean_engine.set_mode("replay")["status"] == "success"
    assert clean_engine.mode == "replay"
    assert clean_engine.set_mode("paper")["status"] == "success"
    assert clean_engine.mode == "paper"
    err = clean_engine.set_mode("invalid_mode")
    assert err["status"] == "error"

def test_tc_eng_18_finnifty_midcpnifty_lot_sizes(clean_engine):
    """TC-ENG-18: FINNIFTY (65) and MIDCPNIFTY (120) lot size resolution."""
    assert clean_engine.get_index_lot_size("FINNIFTY26OCT23000CE") == 65
    assert clean_engine.get_index_lot_size("MIDCPNIFTY26OCT12500CE") == 120

def test_tc_eng_19_invalid_limit_price(clean_engine):
    """TC-ENG-19: Limit order with 0 or negative price returns error."""
    res = clean_engine.place_order(symbol="NIFTY26OCT22700CE", order_type="LIMIT", limit_price=0.0)
    assert res["status"] == "error"
    assert "Limit price must be greater than 0" in res["message"]

def test_tc_eng_20_modify_pending_order_and_empty(clean_engine):
    """TC-ENG-20: modify_active_levels modifies pending order or returns error if empty."""
    err = clean_engine.modify_active_levels(sl=90.0)
    assert err["status"] == "error"

    # Place pending limit order
    clean_engine.place_order(symbol="NIFTY26OCT22700CE", order_type="LIMIT", limit_price=90.0, current_market_price=100.0)
    mod_res = clean_engine.modify_active_levels(limit_price=92.0, sl=85.0, target1=110.0, target2=125.0)
    assert mod_res["status"] == "success"
    assert mod_res["action"] == "PENDING_ORDER_MODIFIED"
    assert clean_engine.pending_orders[0]["limit_price"] == 92.0

def test_tc_eng_21_close_when_empty(clean_engine):
    """TC-ENG-21: Closing when no position exists returns clean error."""
    res = clean_engine.close_position()
    assert res["status"] == "error"
    assert "No active position" in res["message"]

def test_tc_eng_22_cancel_order_by_id_and_empty(clean_engine):
    """TC-ENG-22: Cancel specific order_id or handle empty pending orders."""
    err = clean_engine.cancel_pending_order()
    assert err["status"] == "error"

    clean_engine.place_order(symbol="NIFTY26OCT22700CE", order_type="LIMIT", limit_price=90.0, current_market_price=100.0)
    oid = clean_engine.pending_orders[0]["order_id"]
    res = clean_engine.cancel_pending_order(order_id=oid)
    assert res["status"] == "success"
    assert len(clean_engine.pending_orders) == 0

def test_tc_eng_23_invalid_price_tick(clean_engine):
    """TC-ENG-23: Tick update with invalid or <= 0 price is safely ignored."""
    assert clean_engine.update_price_tick("NIFTY26OCT22700CE", 0.0) is None
    assert clean_engine.update_price_tick("NIFTY26OCT22700CE", -5.0) is None

def test_tc_eng_24_chandelier_trailing_atr(clean_engine):
    """TC-ENG-24: Chandelier ATR dynamic trailing ratchets SL as price creates new highs."""
    clean_engine.place_order(
        symbol="NIFTY26OCT22700CE",
        side="BUY",
        order_type="MARKET",
        lots=1,
        sl=90.0,
        trailing_mode="chandelier",
        current_market_price=100.0
    )
    # Price rises to 120.0 with ATR 3.0 -> Chandelier stop = 120 - (1.5 * 3) = 115.5
    clean_engine.update_price_tick("NIFTY26OCT22700CE", 120.0, atr=3.0)
    pos = clean_engine.active_positions["NIFTY26OCT22700CE"]
    assert pos["current_sl"] == 115.5

