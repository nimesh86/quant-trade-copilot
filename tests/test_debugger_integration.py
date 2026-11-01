"""
Integration tests for Native Debug Runner Process and Webview IPC Socket.
Launches debug_runner.py as a real operating system subprocess, connects over WebSocket,
issues replay commands, and verifies frame synchronization and clean termination.
"""

import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import pytest
import websockets


@pytest.fixture
def venv_python():
    py = Path(__file__).parent.parent / "venv" / "bin" / "python"
    assert py.exists(), f"Virtualenv python not found at {py}"
    return str(py)


@pytest.fixture
def indicator_path():
    p = Path(__file__).parent.parent / "trading_engine" / "indicators" / "bss_pro_v5.py"
    assert p.exists()
    return str(p)


@pytest.mark.asyncio
async def test_debug_runner_process_lifecycle(venv_python, indicator_path):
    """Spawns debug_runner.py as a real process, connects, steps, and validates frames."""
    test_port = 8811
    workspace_root = str(Path(__file__).parent.parent)

    cmd = [
        venv_python,
        "-m",
        "trading_engine.replay.debug_runner",
        "--file", indicator_path,
        "--symbol", "NIFTY",
        "--contract", "NIFTY_ATM_CE",
        "--port", str(test_port),
        "--warmup", "20",
    ]

    proc = subprocess.Popen(
        cmd,
        cwd=workspace_root,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    uri = f"ws://127.0.0.1:{test_port}"
    connected = False
    ws = None

    # Wait up to 8 seconds for the server to bind and accept connections
    for _ in range(40):
        try:
            ws = await websockets.connect(uri)
            connected = True
            break
        except Exception:
            await asyncio.sleep(0.2)

    assert connected, "Could not connect to spawned debug_runner.py process"

    try:
        # 1. Verify Handshake
        raw_init = await asyncio.wait_for(ws.recv(), timeout=4.0)
        init_data = json.loads(raw_init)
        assert init_data["type"] == "init"
        assert init_data["symbol"] == "NIFTY"
        assert init_data["total_ticks"] > 500

        # 2. Step 2 ticks
        await ws.send(json.dumps({"action": "step", "ticks": 2}))
        raw_frame = await asyncio.wait_for(ws.recv(), timeout=4.0)
        frame_data = json.loads(raw_frame)
        assert frame_data["type"] == "frame"
        assert "frame" in frame_data
        assert "spot_bar" in frame_data["frame"]
        assert "indicator_plots" in frame_data["frame"]

        # 3. Step bar
        await ws.send(json.dumps({"action": "step_bar"}))
        raw_bar = await asyncio.wait_for(ws.recv(), timeout=4.0)
        bar_data = json.loads(raw_bar)
        assert bar_data["type"] == "frame"

        # 4. Seek to tick 80
        await ws.send(json.dumps({"action": "seek", "tick": 80}))
        raw_seek = await asyncio.wait_for(ws.recv(), timeout=4.0)
        seek_data = json.loads(raw_seek)
        assert seek_data["type"] == "seek"
        assert seek_data["tick_cursor"] >= 80

        # 5. Ping-pong
        await ws.send(json.dumps({"action": "ping"}))
        raw_pong = await asyncio.wait_for(ws.recv(), timeout=4.0)
        assert json.loads(raw_pong)["type"] == "pong"

    finally:
        if ws:
            await ws.close()
        proc.terminate()
        try:
            proc.wait(timeout=3.0)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=2.0)
