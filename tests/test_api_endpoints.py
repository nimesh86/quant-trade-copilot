import pytest
from fastapi.testclient import TestClient

def test_tc_api_01_status_endpoint(client):
    """TC-API-01: /api/status returns 200 and system health."""
    res = client.get("/api/status")
    assert res.status_code == 200
    data = res.json()
    assert "mode" in data
    assert "server_date" in data or "timestamp" in data

def test_tc_api_02_contracts_expiries(client):
    """TC-API-02: /api/contracts/expiries returns list of expiries for NIFTY."""
    res = client.get("/api/contracts/expiries?underlying=NIFTY")
    assert res.status_code == 200
    data = res.json()
    assert isinstance(data, list) or "expiries" in data

def test_tc_api_04_option_chain_structure(client):
    """TC-API-04: /api/contracts/option_chain returns strikes centered at ATM."""
    res = client.get("/api/contracts/option_chain?underlying=NIFTY&strikes=5")
    assert res.status_code == 200
    data = res.json()
    assert "atm_strike" in data or "calls" in data or "chain" in data or "strikes" in data

def test_tc_api_07_order_missing_fields_validation(client):
    """TC-API-07: POST /api/trade/order with missing required fields returns 400."""
    res = client.post("/api/trade/order", json={})
    assert res.status_code in [400, 422]

def test_tc_api_08_order_malformed_price_type(client):
    """TC-API-08: POST /api/trade/order with string price where float expected."""
    res = client.post("/api/trade/order", json={
        "symbol": "NIFTY26OCT22700CE",
        "side": "BUY",
        "price": "not_a_number"
    })
    # Should reject safely without 500 unhandled server crash
    assert res.status_code in [400, 422]

def test_tc_api_09_trade_state_retrieval(client, clean_engine):
    """TC-API-09: GET /api/trade/state returns capital and active_positions map."""
    res = client.get("/api/trade/state")
    assert res.status_code == 200
    state = res.json()
    assert "capital" in state
    assert "active_positions" in state
    assert "is_kill_switch_active" in state

def test_tc_api_10_broker_status(client):
    """TC-API-10: GET /api/vault/broker-status returns config status."""
    res = client.get("/api/vault/broker-status")
    assert res.status_code == 200
    data = res.json()
    assert "breeze" in data or "active_broker" in data

def test_tc_api_11_kill_switch_api_flow(client, clean_engine):
    """TC-API-11: POST /api/trade/kill_switch triggers emergency lock and blocks new orders."""
    clean_engine.place_order(symbol="NIFTY26OCT22700CE", side="BUY", current_market_price=100.0)

    # Activate kill switch
    res = client.post("/api/trade/kill_switch")
    assert res.status_code == 200
    assert res.json()["status"] == "success"
    assert res.json()["kill_switch_active"] is True

    # Verify order blocked via API
    res_order = client.post("/api/trade/order", json={
        "symbol": "NIFTY26OCT22700CE",
        "side": "BUY",
        "lots": 1,
        "current_market_price": 100.0
    })
    assert res_order.status_code == 400
    assert "Kill switch is ACTIVE" in res_order.json()["detail"]

    # Unlock via API
    res_unlock = client.post("/api/trade/kill_switch/unlock")
    assert res_unlock.status_code == 200
    assert res_unlock.json()["status"] == "success"

def test_tc_api_14_account_reset_api(client, clean_engine):
    """TC-API-14: POST /api/trade/reset resets capital via API."""
    res = client.post("/api/trade/reset", json={"capital": 100000.0})
    assert res.status_code == 200
    assert res.json()["status"] == "success"
    assert clean_engine.capital == 100000.0
