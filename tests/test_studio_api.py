"""
Automated Test Suite for Task 5: Split-Screen Studio UI & Replay APIs.
Validates:
- GET /studio serves the interactive web studio HTML
- POST /api/studio/validate catches syntax errors and provides line numbers for Monaco squigglies
- POST /api/studio/mount registers indicator and extracts dynamic input.* schema
- GET /api/studio/presets provides pre-packaged reference indicator templates
- POST /api/replay/init, /api/replay/step, /api/replay/scrub drive interactive simulation
- POST /api/backtest/run generates institutional performance scorecards
- WebSocket /ws/studio handles real-time bidirectional replay events
"""
import pytest
from fastapi.testclient import TestClient

from trading_engine.web.server import app

client = TestClient(app)


def test_serve_studio_page():
    """AC-5.1: Verifies /studio serves the studio.html interface with HTTP 200."""
    response = client.get("/studio")
    assert response.status_code == 200
    assert "Indicator Studio & Replay Debugger" in response.text
    assert "lightweight-charts" in response.text
    assert "monaco-editor" in response.text
    assert "split-gutter" in response.text


def test_api_studio_validate():
    """
    AC-5.4: Real-Time Error Squigglies.
    Verifies valid code returns valid=True and syntax errors return line numbers.
    """
    # 1. Valid code
    valid_code = """from quant_lib import Indicator, ta

class MyTestIndicator(Indicator):
    def on_bar(self, bar, history):
        return None
"""
    res1 = client.post("/api/studio/validate", json={"code": valid_code})
    assert res1.status_code == 200
    data1 = res1.json()
    assert data1["valid"] is True
    assert len(data1["errors"]) == 0

    # 2. Syntax error (broken def)
    broken_code = """from quant_lib import Indicator

class BrokenIndicator(Indicator):
    def on_bar(self, bar, history
        return None
"""
    res2 = client.post("/api/studio/validate", json={"code": broken_code})
    assert res2.status_code == 200
    data2 = res2.json()
    assert data2["valid"] is False
    assert len(data2["errors"]) > 0
    assert data2["errors"][0]["line"] == 4


def test_api_studio_mount_and_dynamic_schema():
    """
    AC-5.5: Dynamic Inputs Drawer.
    Declaring input.int(21) must extract full input descriptor schema.
    """
    script_with_inputs = """from quant_lib import Indicator, ta, input

class DynamicInputsIndicator(Indicator):
    def init(self):
        self.fast_ema_len = input.int(21, "Fast EMA Length", minval=5, maxval=200)
        self.rsi_period   = input.int(14, "RSI Period", minval=2, maxval=50)

    def on_bar(self, bar, history):
        return None
"""
    res = client.post("/api/studio/mount", json={
        "code": script_with_inputs,
        "symbol": "NIFTY",
        "contract": "NIFTY24OCT24200CE"
    })
    assert res.status_code == 200
    data = res.json()
    assert data["success"] is True
    assert "fast_ema_len" in data["inputs_schema"]
    assert data["inputs_schema"]["fast_ema_len"]["current"] == 21
    assert data["inputs_schema"]["fast_ema_len"]["title"] == "Fast EMA Length"
    assert "rsi_period" in data["inputs_schema"]
    assert data["inputs_schema"]["rsi_period"]["current"] == 14


def test_api_studio_presets():
    """Verifies that pre-packaged templates are accessible via REST."""
    res = client.get("/api/studio/presets")
    assert res.status_code == 200
    presets = res.json()
    assert len(presets) >= 2
    ids = [p["id"] for p in presets]
    assert "bss_pro_v5" in ids
    assert "algoalpha_breakout" in ids


def test_api_replay_lifecycle():
    """
    AC-5.6 & AC-5.7: Replay Initialization, Stepping, and Timeline Scrubbing.
    """
    # 1. Initialize session
    init_res = client.post("/api/replay/init", json={
        "symbol": "NIFTY",
        "contract": "NIFTY_ATM_CE",
        "warmup_bars": 20
    })
    assert init_res.status_code == 200
    init_data = init_res.json()
    session_id = init_data["session_id"]
    assert init_data["cursor"] >= 20
    assert init_data["total_bars"] > 20

    # 2. Step forward (+1)
    step_res = client.post("/api/replay/step", json={
        "session_id": session_id,
        "direction": "+1",
        "count": 1
    })
    assert step_res.status_code == 200
    step_data = step_res.json()
    assert step_data["success"] is True
    assert "frame" in step_data
    frame = step_data["frame"]
    assert "spot_bar" in frame
    assert "option_bar" in frame
    assert "variable_watch" in frame

    # 3. Step backward (-1)
    back_res = client.post("/api/replay/step", json={
        "session_id": session_id,
        "direction": "-1",
        "count": 1
    })
    assert back_res.status_code == 200
    back_data = back_res.json()
    assert back_data["success"] is True

    # 4. Scrub to specific bar
    scrub_res = client.post("/api/replay/scrub", json={
        "session_id": session_id,
        "bar_index": 35
    })
    assert scrub_res.status_code == 200
    scrub_data = scrub_res.json()
    assert scrub_data["success"] is True
    assert scrub_data["cursor"] == 36  # evaluated bar 35, cursor moves to 36


def test_api_backtest_run():
    """Verifies vectorized backtest execution via REST endpoint."""
    code = """from quant_lib import Indicator, ta, IndicatorSignal

class QuickBacktestIndicator(Indicator):
    def on_bar(self, bar, history):
        if len(history) % 20 == 0:
            c = float(bar["close"])
            return IndicatorSignal(action="BUY_CE", trigger_price=c, stop_loss=c-10, target=c+20)
        return None
"""
    res = client.post("/api/backtest/run", json={
        "code": code,
        "symbol": "NIFTY",
        "contract": "NIFTY_ATM_CE",
        "lot_size": 50,
        "slippage_pts": 0.5
    })
    assert res.status_code == 200
    data = res.json()
    assert "total_trades" in data
    assert "win_rate_pct" in data
    assert "profit_factor" in data
    assert "total_pnl_inr" in data
    assert "max_drawdown_inr" in data
    assert data["total_trades"] > 0


def test_studio_websocket_channel():
    """Verifies WebSocket /ws/studio connection and message handling."""
    with client.websocket_connect("/ws/studio") as websocket:
        # 1. Bind session
        init_res = client.post("/api/replay/init", json={"symbol": "NIFTY", "contract": "NIFTY_ATM_CE"})
        session_id = init_res.json()["session_id"]

        websocket.send_json({"action": "BIND_SESSION", "session_id": session_id})

        # 2. Step via WebSocket
        websocket.send_json({"action": "STEP", "direction": "+1"})
        response = websocket.receive_json()
        assert response["type"] == "REPLAY_FRAME"
        assert "frame" in response
        assert "cursor" in response


def test_available_dates_endpoint():
    """Bug 2 & 8 Verification: Tests /api/replay/available-dates returns latest session default 2026-10-30."""
    res = client.get("/api/replay/available-dates")
    assert res.status_code == 200
    data = res.json()
    assert "dates" in data
    assert len(data["dates"]) >= 3
    assert "2026-10-30" in data["dates"]
    assert data["default_date"] == "2026-10-30"


def test_date_change_produces_distinct_candles():
    """Bug 8 Verification: Tests that switching session dates yields distinct candle data."""
    res1 = client.post("/api/replay/init", json={
        "symbol": "NIFTY",
        "contract": "NIFTY_ATM_CE",
        "date": "2026-10-30",
        "warmup_bars": 10
    })
    res2 = client.post("/api/replay/init", json={
        "symbol": "NIFTY",
        "contract": "NIFTY_ATM_CE",
        "date": "2026-10-29",
        "warmup_bars": 10
    })
    assert res1.status_code == 200
    assert res2.status_code == 200
    bars1 = res1.json()["history_bars"]
    bars2 = res2.json()["history_bars"]
    closes1 = [b["spot_close"] for b in bars1]
    closes2 = [b["spot_close"] for b in bars2]
    assert closes1 != closes2, "Candles for different dates must not be identical!"


def test_market_candles_organic_dynamics_no_staircase():
    """Bug 9 Verification: Verifies synthetic session contains both green and red candles, eliminating staircase."""
    from trading_engine.web.studio_router import _generate_synthetic_candles
    df = _generate_synthetic_candles("NIFTY", "NIFTY_ATM_CE", 120, "2026-10-30")
    assert len(df) == 120
    
    # Must have both upward and downward candles (no monotonic staircase)
    green_candles = df[df["spot_close"] > df["spot_open"]]
    red_candles = df[df["spot_close"] < df["spot_open"]]
    assert len(green_candles) > 10, "Should contain multiple green candles"
    assert len(red_candles) > 10, "Should contain multiple red candles (no unidirectional staircase)"
    
    # Standard deviation of spot returns should be non-zero and realistic
    price_deltas = df["spot_close"].diff().dropna()
    assert price_deltas.std() > 0.5, "Market must have authentic volatility"


def test_breakpoint_line_included_in_frame():
    """Bug 10 Verification: Verifies breakpoint line number is captured in ReplayFrame."""
    res = client.post("/api/replay/init", json={
        "symbol": "NIFTY",
        "contract": "NIFTY_ATM_CE",
        "date": "2026-10-30",
        "warmup_bars": 10,
        "breakpoints": [74],
        "break_on_signal": True
    })
    assert res.status_code == 200
    data = res.json()
    session_id = data["session_id"]

    # Step through replay until breakpoint is hit
    found_bp = False
    for _ in range(50):
        step_res = client.post("/api/replay/step", json={"session_id": session_id, "direction": "+1", "count": 1})
        assert step_res.status_code == 200
        step_data = step_res.json()
        frame = step_data.get("frame")
        if frame and frame.get("breakpoint_hit"):
            found_bp = True
            assert frame.get("breakpoint_line") == 74
            break
    assert found_bp, "Breakpoint at line 74 should have fired"



def test_custom_indicators_crud():
    """Bug 6 Verification: Tests saving, listing, and deleting custom indicators."""
    test_code = "from quant_lib import Indicator\nclass CustomTest(Indicator):\n    pass\n"
    save_res = client.post("/api/studio/indicators/save", json={
        "name": "My Custom Scalper",
        "code": test_code,
        "description": "High win-rate breakout strategy"
    })
    assert save_res.status_code == 200
    save_data = save_res.json()
    assert save_data["success"] is True
    ind_id = save_data["id"]

    # List indicators
    list_res = client.get("/api/studio/indicators/list")
    assert list_res.status_code == 200
    items = list_res.json()
    assert any(i["id"] == ind_id for i in items)

    # Delete indicator
    del_res = client.delete(f"/api/studio/indicators/{ind_id}")
    assert del_res.status_code == 200


def test_replay_init_history_bars_and_date():
    """Bug 3 Verification: Tests that /api/replay/init returns full historical bars array."""
    res = client.post("/api/replay/init", json={
        "symbol": "NIFTY",
        "contract": "NIFTY_ATM_CE",
        "date": "2024-10-24",
        "warmup_bars": 30
    })
    assert res.status_code == 200
    data = res.json()
    assert data["date"] == "2024-10-24"
    assert "history_bars" in data
    assert len(data["history_bars"]) > 0
    first_bar = data["history_bars"][0]
    assert "spot_open" in first_bar
    assert "spot_close" in first_bar
    assert "opt_close" in first_bar

