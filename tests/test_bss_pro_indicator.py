"""
Automated Test Suite for Task 6: Pre-Packaged Templates & End-to-End Validation.
Validates:
- AC-6.1: BSS Pro v5 Rule Fidelity (Trend Pullback & Breakout signals on synthetic market setups)
- AC-6.2: Presets Discovery & Loading (bss_pro_v5, algoalpha_breakout, supertrend_cloud)
- AC-6.3: End-to-End Replay Run (Multi-hundred candle replay with zero crashes)
- AC-6.4: Latency Compliance (Single-tick / incremental evaluation takes <= 15 microseconds in RAM)
- AC-6.5: Dynamic Inputs Adaptation (Adjusting threshold inputs updates strategy behavior instantly)
"""
import time
from datetime import timedelta
import numpy as np
import pandas as pd
import pytest

from trading_engine.runtime.compiler import IndicatorCompiler
from trading_engine.indicators.bss_pro_v5 import BSSProV5Indicator
from trading_engine.indicators.algoalpha_breakout import AlgoAlphaBreakoutIndicator
from trading_engine.indicators.supertrend_cloud import SupertrendCloudIndicator
from trading_engine.indicators.presets import PresetsManager
from trading_engine.replay.session import ReplaySession


# ---------------------------------------------------------
# Test Data Fixtures
# ---------------------------------------------------------

@pytest.fixture
def synthetic_market_data():
    """Generates 200 bars of synthetic market data with realistic trend, pullbacks, and breakout dynamics."""
    n_bars = 200
    base_time = pd.Timestamp("2024-10-24 09:15:00")
    timestamps = [base_time + timedelta(minutes=i) for i in range(n_bars)]

    records = []
    cur_price = 24000.0
    for i in range(n_bars):
        # Cyclical pattern: consolidation -> pullback -> strong buyer breakout
        cycle_idx = i % 30
        if cycle_idx == 24:  # Pullback dip towards EMA fast
            cur_price -= 10.0
            op = cur_price + 8.0
            hi = cur_price + 9.0
            lo = cur_price - 2.0
            cl = cur_price + 1.0
            vol = 30000
        elif cycle_idx == 25:  # High buyer volume expansion candle
            cur_price += 14.0
            op = cur_price - 12.0
            hi = cur_price + 1.0
            lo = cur_price - 13.0
            cl = cur_price
            vol = 85000
        else:  # Steady structural uptrend
            cur_price += 2.0
            op = cur_price - 1.0
            hi = cur_price + 3.0
            lo = cur_price - 2.0
            cl = cur_price + 2.0
            vol = 25000

        opt_price = 100.0 + (cur_price - 24000.0) * 0.4
        records.append({
            "timestamp": timestamps[i],
            "symbol": "NIFTY",
            "contract": "NIFTY24OCT24200CE",
            "timeframe": "1m",
            "spot_open": op,
            "spot_high": hi,
            "spot_low": lo,
            "spot_close": cl,
            "spot_volume": vol,
            "opt_open": opt_price - 1.0,
            "opt_high": opt_price + 3.0,
            "opt_low": opt_price - 2.0,
            "opt_close": opt_price,
            "opt_volume": int(vol * 0.6),
            "opt_oi": 1500000 + i * 200,
        })
    return pd.DataFrame(records)


# ---------------------------------------------------------
# Test Cases
# ---------------------------------------------------------

def test_presets_loading():
    """
    AC-6.2: Presets Discovery & Loading.
    Validates that all presets in presets.py load and compile cleanly via IndicatorCompiler.
    """
    presets = PresetsManager.list_presets()
    assert len(presets) >= 3

    preset_ids = [p["id"] for p in presets]
    assert "bss_pro_v5" in preset_ids
    assert "algoalpha_breakout" in preset_ids
    assert "supertrend_cloud" in preset_ids

    # Verify each preset code compiles and produces an executable Indicator subclass
    for p in presets:
        code = PresetsManager.get_preset_code(p["id"])
        assert len(code) > 100
        ind_cls, schema = IndicatorCompiler.compile_and_load(code)
        assert ind_cls is not None
        assert isinstance(schema, dict)


def test_bss_pro_signal_logic(synthetic_market_data):
    """
    AC-6.1: BSS Pro v5 Rule Fidelity.
    Tests that BSSProV5Indicator produces a valid BUY_CE signal on bullish momentum setups.
    """
    indicator = BSSProV5Indicator()
    indicator.dom_thresh.value = 52.0  # Slightly lower threshold for synthetic test

    signals = []
    for i in range(55, len(synthetic_market_data)):
        history_slice = synthetic_market_data.iloc[:i + 1]
        row = synthetic_market_data.iloc[i].to_dict()
        opt_bar = {k.replace("opt_", ""): v for k, v in row.items() if k.startswith("opt_")}
        opt_bar["timestamp"] = row["timestamp"]

        sig = indicator.on_bar(opt_bar, history_slice)
        if sig is not None:
            signals.append(sig)

    assert len(signals) > 0, "Expected at least 1 BSS Pro signal during bullish trend!"
    first_sig = signals[0]
    assert first_sig.action in ("BUY_CE", "BUY_PE")
    assert first_sig.trigger_price > 0.0
    assert first_sig.stop_loss > 0.0
    assert first_sig.target > first_sig.trigger_price
    assert "BSS_" in first_sig.tag


def test_e2e_bss_pro_replay(synthetic_market_data):
    """
    AC-6.3: End-to-End Replay Run.
    Executes ReplaySession over synthetic data, testing step-forward, state caching, and signal generation.
    """
    indicator = BSSProV5Indicator()
    session = ReplaySession(synthetic_market_data, indicator, warmup_bars=55, break_on_signal=False)

    frames = []
    signals_found = 0

    while session.cursor < session.total_bars:
        frame = session.step_forward(1, mode="bar")
        if frame is not None:
            frames.append(frame)
            if frame.active_signal is not None:
                signals_found += 1

    assert len(frames) == (len(synthetic_market_data) - 55)
    # Check that plots dictionary was updated
    last_frame = frames[-1]
    assert "ema_fast" in last_frame.indicator_plots
    assert "supertrend" in last_frame.indicator_plots
    assert "buyer_pct" in last_frame.indicator_plots


def test_bss_pro_latency_benchmark():
    """
    AC-6.4: Latency Compliance.
    Single-tick evaluation in RAM must execute in <= 15 microseconds.
    """
    indicator = BSSProV5Indicator()

    # Pre-compile JIT / warm up incremental states
    for _ in range(100):
        indicator.on_tick(24250.0, 5000, "2024-10-24 09:15:00")

    # Benchmarking 10,000 tick evaluations
    n_ticks = 10000
    prices = 24200.0 + np.random.normal(0, 5, n_ticks)

    t0 = time.perf_counter()
    for i in range(n_ticks):
        indicator.on_tick(float(prices[i]), 1000, "2024-10-24 09:15:00")
    total_time_s = time.perf_counter() - t0

    avg_latency_us = (total_time_s / n_ticks) * 1_000_000
    print(f"\n[BSS Pro Live Tick Benchmark] Average latency: {avg_latency_us:.3f} µs per tick (Target <= 15.0 µs)")
    assert avg_latency_us <= 15.0, f"Average latency {avg_latency_us:.3f} µs exceeded 15.0 µs target!"


def test_bss_pro_dynamic_inputs(synthetic_market_data):
    """
    AC-6.5: Dynamic Inputs Adaptation.
    Modifying input parameters dynamically updates strategy output without code re-parsing.
    """
    ind_relaxed = BSSProV5Indicator(dom_thresh=50.0)
    ind_strict = BSSProV5Indicator(dom_thresh=95.0)  # Impossible 95% threshold

    signals_relaxed = 0
    signals_strict = 0

    for i in range(55, len(synthetic_market_data)):
        history_slice = synthetic_market_data.iloc[:i + 1]
        row = synthetic_market_data.iloc[i].to_dict()
        opt_bar = {k.replace("opt_", ""): v for k, v in row.items() if k.startswith("opt_")}
        opt_bar["timestamp"] = row["timestamp"]

        sig_rel = ind_relaxed.on_bar(opt_bar, history_slice)
        if sig_rel is not None:
            signals_relaxed += 1

        sig_str = ind_strict.on_bar(opt_bar, history_slice)
        if sig_str is not None:
            signals_strict += 1

    assert signals_relaxed > 0, "Relaxed threshold should have generated signals"
    assert signals_strict == 0, "Strict 95% threshold should have blocked all signals"
