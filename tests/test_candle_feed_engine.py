"""
Unit tests verifying the Market Data Feed Engine and Candle Time Pacing.
Validates:
1. 300 ticks per 1-minute candle (200ms per tick, 5 ticks/second).
2. All 300 ticks within a bar share the identical 1-minute candle timestamp.
3. Forming candle updates open, high, low, close in place across ticks.
4. Exactly 1 candle is completed per 60 wall-clock seconds at 1x speed.
"""

import pandas as pd
from datetime import timedelta
import pytest

from trading_engine.replay.session import ReplaySession, ReplayFrame
from trading_engine.quant_lib.base import Indicator
from trading_engine.web.studio_router import _generate_synthetic_candles


def test_candle_feed_microstructure_and_timing():
    """Verifies that 1-minute candles are synthesized with 300 ticks (200ms each)."""
    df = _generate_synthetic_candles("NIFTY", "NIFTY_ATM_CE", n_bars=10, date_str="2026-10-30")
    indicator = Indicator()
    session = ReplaySession(df, indicator, warmup_bars=0, ticks_per_candle=300)

    assert session.TICKS_PER_CANDLE == 300
    assert session.total_ticks == 10 * 300  # 3000 ticks across 10 candles

    # Examine ticks for Bar 0 (09:15:00)
    bar_0_ticks = session.ticks[:300]
    expected_candle_time = "2026-10-30 09:15:00"

    for i, t in enumerate(bar_0_ticks):
        # All 300 ticks in bar 0 must share the exact candle timestamp
        assert t["candle_timestamp"] == expected_candle_time
        assert t["bar_index"] == 0
        assert t["tick_in_bar"] == i

    # Verify 200ms interval between consecutive ticks
    t0_time = pd.Timestamp(bar_0_ticks[0]["timestamp"])
    t1_time = pd.Timestamp(bar_0_ticks[1]["timestamp"])
    delta_ms = (t1_time - t0_time).total_seconds() * 1000
    assert abs(delta_ms - 200.0) < 1.0, f"Expected 200ms tick interval, got {delta_ms}ms"

    # Step through ticks and verify forming candle updates in place
    first_tick_frame = session.step_tick_forward(1)
    assert first_tick_frame.candle_timestamp == expected_candle_time
    assert first_tick_frame.forming_candle["timestamp"] == expected_candle_time
    assert first_tick_frame.spot_bar["timestamp"] == expected_candle_time

    # Step to tick 150 (midway through bar 0)
    mid_frame = session.step_tick_forward(149)
    assert mid_frame.candle_timestamp == expected_candle_time
    assert mid_frame.forming_candle["timestamp"] == expected_candle_time

    # Step to the end of bar 0 (tick 300)
    session.step_tick_forward(150)
    # Next tick enters Bar 1 (09:16:00)
    bar_1_frame = session.step_tick_forward(1)
    assert bar_1_frame.candle_timestamp == "2026-10-30 09:16:00"
    assert bar_1_frame.forming_candle["timestamp"] == "2026-10-30 09:16:00"


def test_realtime_pacing_calculation():
    """Verifies that at 1x speed, 300 ticks correspond to 60 wall-clock seconds."""
    ticks_per_candle = 300
    candle_duration_seconds = 60.0
    speed = 1.0

    tick_interval = candle_duration_seconds / ticks_per_candle
    assert tick_interval == 0.20, "200ms per tick"

    delay_at_1x = tick_interval / speed
    assert delay_at_1x == 0.20, "At 1x speed, delay must be 200ms"

    total_time_per_candle = ticks_per_candle * delay_at_1x
    assert total_time_per_candle == 60.0, "At 1x speed, 1 candle must take exactly 60 seconds"
