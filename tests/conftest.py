import os
import sys
import pytest
from pathlib import Path
from fastapi.testclient import TestClient

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from trading_engine.web.server import app, contract_engine

@pytest.fixture
def client():
    """Provides a clean FastAPI TestClient."""
    with TestClient(app) as test_client:
        yield test_client

@pytest.fixture
def clean_engine():
    """Resets the contract execution engine to a clean initial state before each test."""
    contract_engine.reset_account(capital=100000.0)
    contract_engine.is_kill_switch_active = False
    contract_engine.kill_switch_triggered_at = None
    contract_engine.active_positions.clear()
    contract_engine.active_position = None
    contract_engine.pending_orders.clear()
    contract_engine.trade_history.clear()
    return contract_engine

@pytest.fixture
def sample_candles():
    """Returns deterministic 1-minute OHLCV candles for math & indicator verification."""
    base_time = 1761450900  # 09:15 IST
    candles = []
    prices = [100.0, 102.5, 101.0, 104.0, 107.5, 112.0, 118.0, 122.5, 119.0, 125.0, 130.0, 132.5, 128.0, 135.0, 140.0]
    for i, p in enumerate(prices):
        candles.append({
            "time": base_time + (i * 60),
            "open": p - 1.0,
            "high": p + 2.0,
            "low": p - 1.5,
            "close": p,
            "volume": 1000 + (i * 150)
        })
    return candles
