"""
Unit tests for Native Python Replay Debug Runner and WebSocket IPC Bridge.
Validates dynamic indicator loading, session stepping, seeking, and real-time IPC message flow.
"""

import asyncio
import json
import os
from pathlib import Path
import pytest
import websockets

from trading_engine.replay.debug_runner import (
    load_indicator_from_file,
    ReplayDebugServer,
)
from trading_engine.quant_lib.base import Indicator


@pytest.fixture
def bss_indicator_path():
    p = Path(__file__).parent.parent / "trading_engine" / "indicators" / "bss_pro_v5.py"
    assert p.exists(), f"Indicator file not found at {p}"
    return str(p)


def test_dynamic_indicator_loader(bss_indicator_path):
    """Verifies dynamic introspection and instantiation of Indicator class from file."""
    indicator = load_indicator_from_file(bss_indicator_path)
    assert isinstance(indicator, Indicator)
    assert indicator.__class__.__name__ == "BSSProV5Indicator"
    assert hasattr(indicator, "on_bar")
    assert hasattr(indicator, "on_tick")
    assert hasattr(indicator, "plots")
    assert hasattr(indicator, "ema_fast_len")
    assert indicator.ema_fast_len.value == 21


@pytest.mark.asyncio
async def test_debug_server_init_and_stepping(bss_indicator_path):
    """Verifies that ReplayDebugServer initializes session, steps ticks and bars accurately."""
    indicator = load_indicator_from_file(bss_indicator_path)
    server = ReplayDebugServer(
        indicator=indicator,
        symbol="NIFTY",
        contract="NIFTY_ATM_CE",
        date_str="2026-10-30",
        warmup_bars=20,
        port=8799,
    )

    init_payload = server.get_init_payload()
    assert init_payload["type"] == "init"
    assert init_payload["symbol"] == "NIFTY"
    assert init_payload["total_bars"] > 100
    assert init_payload["total_ticks"] > 1000
    assert len(init_payload["history_bars"]) > 0

    initial_tick = server.session.tick_cursor

    # Step 1 tick
    frame1 = await server.step(1)
    assert frame1 is not None
    assert server.session.tick_cursor == initial_tick + 1
    assert "spot_bar" in frame1.to_dict()
    assert "indicator_plots" in frame1.to_dict()

    # Step 5 ticks
    frame2 = await server.step(5)
    assert server.session.tick_cursor == initial_tick + 6

    # Step to next bar
    frame_bar = await server.step_bar()
    assert frame_bar is not None
    assert server.session.cursor > 0

    # Seek
    await server.seek(100)
    assert server.session.tick_cursor >= 100


@pytest.mark.asyncio
async def test_websocket_ipc_communication(bss_indicator_path):
    """Verifies full WebSocket handshake and bidirectional control commands."""
    indicator = load_indicator_from_file(bss_indicator_path)
    test_port = 8798
    server = ReplayDebugServer(
        indicator=indicator,
        symbol="NIFTY",
        contract="NIFTY_ATM_CE",
        date_str="2026-10-30",
        warmup_bars=15,
        port=test_port,
    )
    await server.start()

    uri = f"ws://127.0.0.1:{test_port}"
    try:
        async with websockets.connect(uri) as ws:
            # 1. First message must be the init payload
            init_raw = await asyncio.wait_for(ws.recv(), timeout=3.0)
            init_msg = json.loads(init_raw)
            assert init_msg["type"] == "init"
            assert init_msg["symbol"] == "NIFTY"

            # 2. Send step command
            await ws.send(json.dumps({"action": "step", "ticks": 2}))
            frame_raw = await asyncio.wait_for(ws.recv(), timeout=3.0)
            frame_msg = json.loads(frame_raw)
            assert frame_msg["type"] == "frame"
            assert frame_msg["tick_cursor"] == server.session.tick_cursor

            # 3. Send seek command
            await ws.send(json.dumps({"action": "seek", "tick": 50}))
            seek_raw = await asyncio.wait_for(ws.recv(), timeout=3.0)
            seek_msg = json.loads(seek_raw)
            assert seek_msg["type"] == "seek"
            assert seek_msg["tick_cursor"] in (50, 51)

            # 4. Test load_date command
            await ws.send(json.dumps({"action": "load_date", "date": "2026-10-25"}))
            load_raw = await asyncio.wait_for(ws.recv(), timeout=3.0)
            load_msg = json.loads(load_raw)
            assert load_msg["type"] == "init"
            assert load_msg["date"] == "2026-10-25"

            # 5. Test run_backtest command
            await ws.send(json.dumps({"action": "run_backtest", "start_date": "2026-10-25", "end_date": "2026-10-25"}))
            bt_raw = await asyncio.wait_for(ws.recv(), timeout=5.0)
            bt_msg = json.loads(bt_raw)
            assert bt_msg["type"] == "backtest_result"
            assert "scorecard" in bt_msg
            assert "win_rate_pct" in bt_msg["scorecard"]

            # 6. Ping-pong
            await ws.send(json.dumps({"action": "ping"}))
            pong_raw = await asyncio.wait_for(ws.recv(), timeout=3.0)
            pong_msg = json.loads(pong_raw)
            assert pong_msg["type"] == "pong"
    finally:
        await server.stop()

