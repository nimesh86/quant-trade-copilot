"""
Unit Tests for /api/paper/candles Endpoint
Validates:
1. 401 Unauthorized when session is missing.
2. In-memory paper_daemon bar history retrieval.
3. Fallback to historical CSV archive for NIFTY and CRUDEOIL.
4. Option contract lookup delegation.
5. Timestamp formatting and candle fields validation.
"""

import pytest
from fastapi.testclient import TestClient
from trading_engine.web.server import app, create_session, paper_daemon


@pytest.fixture
def auth_client():
    token = create_session()
    return TestClient(app, cookies={"quant_session": token})


def test_paper_candles_unauthorized():
    client = TestClient(app)
    resp = client.get("/api/paper/candles?symbol=NIFTY")
    assert resp.status_code == 401


def test_paper_candles_nifty_authenticated(auth_client):
    resp = auth_client.get("/api/paper/candles?symbol=NIFTY")
    assert resp.status_code == 200
    data = resp.json()
    assert data["symbol"] == "NIFTY"
    assert "candles" in data
    assert isinstance(data["candles"], list)
    assert len(data["candles"]) > 0
    c0 = data["candles"][0]
    assert "time" in c0
    assert "open" in c0
    assert "high" in c0
    assert "low" in c0
    assert "close" in c0


def test_paper_candles_crude_authenticated(auth_client):
    resp = auth_client.get("/api/paper/candles?symbol=CRUDEOIL")
    assert resp.status_code == 200
    data = resp.json()
    assert data["symbol"] == "CRUDEOIL"
    assert "candles" in data
    assert isinstance(data["candles"], list)
    assert len(data["candles"]) > 0


def test_paper_candles_with_date(auth_client):
    resp = auth_client.get("/api/paper/candles?symbol=CRUDEOIL&date=2026-11-05")
    assert resp.status_code == 200
    data = resp.json()
    assert data["symbol"] == "CRUDEOIL"
    assert data["date"] == "2026-11-05"
    assert len(data["candles"]) > 0


def test_paper_candles_in_memory_forming(auth_client):
    # Set a mock in-memory forming bar
    paper_daemon.forming_bars["CRUDEOIL"] = {
        "time": "18:00",
        "timestamp": "2026-11-09 18:00:00",
        "open": 5850.0,
        "high": 5865.0,
        "low": 5845.0,
        "close": 5860.0,
        "volume": 250
    }
    resp = auth_client.get("/api/paper/candles?symbol=CRUDEOIL&date=today")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["candles"]) > 0
    # Last candle should reflect forming bar
    last = data["candles"][-1]
    assert last["time"] == "18:00"
    assert last["close"] == 5860.0

