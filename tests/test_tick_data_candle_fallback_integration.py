import pytest
from datetime import datetime
from sqlalchemy import create_engine, text
from fastapi.testclient import TestClient

from trading_engine.web.contract_data_manager import fetch_contract_candles
from trading_engine.web.server import app, state, _cached_sim_dates, _last_dates_fetch_ts
import trading_engine.web.server as server_module


from sqlalchemy.pool import StaticPool


@pytest.fixture
def test_db_engine():
    """
    Creates an isolated in-memory SQLite database simulating TimescaleDB:
    - minute_candles and option_minute_candles are empty for 2026-10-29.
    - tick_data contains authentic ticks for 2026-10-29.
    """
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    with engine.begin() as conn:
        conn.execute(text("""
            CREATE TABLE minute_candles (
                timestamp TEXT,
                symbol TEXT,
                open REAL,
                high REAL,
                low REAL,
                close REAL,
                volume INTEGER
            );
        """))
        conn.execute(text("""
            CREATE TABLE option_minute_candles (
                timestamp TEXT,
                symbol TEXT,
                open REAL,
                high REAL,
                low REAL,
                close REAL,
                volume INTEGER
            );
        """))
        conn.execute(text("""
            CREATE TABLE tick_data (
                timestamp TEXT,
                symbol TEXT,
                price REAL,
                volume INTEGER
            );
        """))

        # Insert historical older day into minute_candles (e.g. 2026-10-26)
        conn.execute(text("""
            INSERT INTO minute_candles (timestamp, symbol, open, high, low, close, volume)
            VALUES ('2026-10-26 15:30:00+05:30', 'NIFTY-I', 22500.0, 22520.0, 22490.0, 22510.0, 1000);
        """))

        # Insert off-market midnight tick on 2026-10-29 (should be pruned by market hours 09:15-15:30)
        conn.execute(text("""
            INSERT INTO tick_data (timestamp, symbol, price, volume)
            VALUES ('2026-10-29 02:00:00+05:30', 'NIFTY', 22750.0, 0);
        """))

        # Insert 09:15:00 - 09:16:00 ticks for NIFTY on 2026-10-29
        # Tick 1: 09:15:05 -> Open
        # Tick 2: 09:15:20 -> High
        # Tick 3: 09:15:40 -> Low
        # Tick 4: 09:15:55 -> Close
        ticks_nifty = [
            ('2026-10-29 09:15:05+05:30', 'NIFTY', 22690.0, 100),
            ('2026-10-29 09:15:20+05:30', 'NIFTY', 22710.0, 250),
            ('2026-10-29 09:15:40+05:30', 'NIFTY', 22680.0, 350),
            ('2026-10-29 09:15:55+05:30', 'NIFTY', 22700.0, 500),
            # Minute 2 (09:16)
            ('2026-10-29 09:16:05+05:30', 'NIFTY', 22702.0, 600),
            ('2026-10-29 09:16:30+05:30', 'NIFTY', 22720.0, 800),
            ('2026-10-29 09:16:55+05:30', 'NIFTY', 22715.0, 1000),
        ]
        for ts, sym, px, vol in ticks_nifty:
            conn.execute(text("INSERT INTO tick_data (timestamp, symbol, price, volume) VALUES (:ts, :sym, :px, :vol);"),
                         {"ts": ts, "sym": sym, "px": px, "vol": vol})

        # Insert ticks for option strike NIFTY26N0322750CE on 2026-10-29
        ticks_opt = [
            ('2026-10-29 09:15:10+05:30', 'NIFTY26N0322750CE', 134.35, 1000),
            ('2026-10-29 09:15:25+05:30', 'NIFTY26N0322750CE', 149.25, 2500),
            ('2026-10-29 09:15:45+05:30', 'NIFTY26N0322750CE', 126.05, 3200),
            ('2026-10-29 09:15:58+05:30', 'NIFTY26N0322750CE', 140.85, 4500),
        ]
        for ts, sym, px, vol in ticks_opt:
            conn.execute(text("INSERT INTO tick_data (timestamp, symbol, price, volume) VALUES (:ts, :sym, :px, :vol);"),
                         {"ts": ts, "sym": sym, "px": px, "vol": vol})

    return engine


def test_integration_01_fetch_contract_candles_fallback_to_tick_data(test_db_engine):
    """
    IT-01: When minute_candles and option_minute_candles have 0 rows for 2026-10-29,
    fetch_contract_candles must automatically aggregate authentic 1-minute OHLCV candles
    directly from tick_data.
    """
    res = fetch_contract_candles("NIFTY", target_date="2026-10-29", db_engine=test_db_engine)
    
    assert res is not None
    assert res["symbol"] == "NIFTY"
    assert res["date"] == "2026-10-29"
    assert res["count"] == 2
    assert res["num_candles"] == 2
    assert len(res["candles"]) == 2

    c1 = res["candles"][0]
    assert c1["time"] == "2026-10-29 09:15"
    assert c1["open"] == 22690.0
    assert c1["high"] == 22710.0
    assert c1["low"] == 22680.0
    assert c1["close"] == 22700.0
    # Volume: max(500) - min(100) = 400
    assert c1["volume"] == 400

    c2 = res["candles"][1]
    assert c2["time"] == "2026-10-29 09:16"
    assert c2["open"] == 22702.0
    assert c2["high"] == 22720.0
    assert c2["close"] == 22715.0


def test_integration_02_option_contract_tick_data_fallback(test_db_engine):
    """
    IT-02: Option contracts (e.g. NIFTY26N0322750CE) missing in option_minute_candles
    must also aggregate correctly from tick_data.
    """
    res = fetch_contract_candles("NIFTY26N0322750CE", target_date="2026-10-29", db_engine=test_db_engine)
    
    assert res is not None
    assert res["count"] == 1
    c = res["candles"][0]
    assert c["open"] == 134.35
    assert c["high"] == 149.25
    assert c["low"] == 126.05
    assert c["close"] == 140.85
    assert c["volume"] == 3500  # 4500 - 1000


def test_integration_03_api_contracts_candles_endpoint(client, test_db_engine):
    """
    IT-03: GET /api/contracts/candles?symbol=NIFTY&date=2026-10-29 returns HTTP 200
    with populated candles instead of empty array.
    """
    original_engine = state.db_engine
    try:
        state.db_engine = test_db_engine
        response = client.get("/api/contracts/candles?symbol=NIFTY&date=2026-10-29")
        assert response.status_code == 200
        data = response.json()
        assert data["count"] > 0
        assert data["num_candles"] > 0
        assert len(data["candles"]) == 2
        assert data["candles"][0]["open"] == 22690.0
    finally:
        state.db_engine = original_engine


def test_integration_04_simulation_dates_includes_tick_data(client, test_db_engine):
    """
    IT-04: GET /api/simulation/dates must include 2026-10-29 even though it was only
    recorded in tick_data and not in minute_candles.
    """
    original_engine = state.db_engine
    # Invalidate cache so test runs query
    server_module._cached_sim_dates.clear()
    server_module._last_dates_fetch_ts = 0.0

    try:
        state.db_engine = test_db_engine
        response = client.get("/api/simulation/dates")
        assert response.status_code == 200
        data = response.json()
        assert "dates" in data
        assert "2026-10-29" in data["dates"], f"Expected 2026-10-29 in {data['dates']}"
    finally:
        state.db_engine = original_engine
        server_module._cached_sim_dates.clear()
        server_module._last_dates_fetch_ts = 0.0
