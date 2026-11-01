"""
Unit tests for Quant Studio Live Telemetry & State API.
Verifies real-time synchronization of chart viewport, active timeframe,
focused trade, and backtest results.
"""
import pytest
import os
import json
import asyncio
from pathlib import Path
from httpx import AsyncClient, ASGITransport

from trading_engine.web.server import app
from trading_engine.replay.debug_runner import ReplayDebugServer
from trading_engine.replay.backtester import BrokerChargesConfig
from trading_engine.quant_lib.base import Indicator


class MinimalTestIndicator(Indicator):
    def __init__(self):
        super().__init__(name="MinimalTestIndicator")
    def on_tick(self, tick):
        return None
    def on_bar(self, bar, history=None):
        return None


@pytest.mark.asyncio
async def test_quant_studio_rest_api_viewport_and_state(tmp_path):
    """Verifies REST endpoints for reading and updating Quant Studio viewport state."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Post a simulated viewport change from Webview
        viewport_payload = {
            "visible_from": 1793444100,
            "visible_to": 1793448000,
            "visible_from_str": "2026-10-30 09:15",
            "visible_to_str": "2026-10-30 10:20",
            "timeframe": "1m",
            "candle_count_in_view": 65,
            "focused_trade": {
                "action": "BUY_CE",
                "entry_time": "2026-10-30 09:35",
                "exit_time": "2026-10-30 09:48",
                "gross_pnl_inr": 1250.0,
                "net_pnl_inr": 1195.8
            }
        }

        resp = await client.post("/api/quant-studio/viewport", json=viewport_payload)
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

        # 2. Get viewport
        vp_resp = await client.get("/api/quant-studio/viewport")
        assert vp_resp.status_code == 200
        vp_data = vp_resp.json()
        assert vp_data["timeframe"] == "1m"
        assert vp_data["visible_from_str"] == "2026-10-30 09:15"
        assert vp_data["focused_trade"]["action"] == "BUY_CE"

        # 3. Get overall state
        state_resp = await client.get("/api/quant-studio/state")
        assert state_resp.status_code == 200


@pytest.mark.asyncio
async def test_debug_runner_state_persistence_and_ipc():
    """Verifies that ReplayDebugServer creates and updates quant_studio_state.json."""
    ind = MinimalTestIndicator()
    server = ReplayDebugServer(
        indicator=ind,
        symbol="NIFTY",
        contract="NIFTY_ATM_CE",
        date_str="2026-10-30",
        port=8912
    )

    # State must be initialized and persisted
    assert server.quant_studio_state["session"]["symbol"] == "NIFTY"
    assert server.quant_studio_state["session"]["date"] == "2026-10-30"

    # Simulate viewport sync
    server.quant_studio_state["viewport"]["timeframe"] = "1m"
    server.quant_studio_state["viewport"]["visible_from_str"] = "2026-10-30 09:30"
    server.quant_studio_state["viewport"]["visible_to_str"] = "2026-10-30 10:15"
    server._persist_state()

    state_path = Path("reports/quant_studio_state.json")
    assert state_path.exists()
    with open(state_path, "r") as f:
        saved = json.load(f)
    assert saved["session"]["symbol"] == "NIFTY"
    assert saved["viewport"]["timeframe"] == "1m"
