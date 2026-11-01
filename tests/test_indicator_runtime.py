"""
Automated Test Suite for Task 2: Dynamic Python Runtime & Hot-Reload Engine.
Validates AC-2.1 through AC-2.7: Security sandboxing, parameter extraction, warmup speed, and zero-downtime hot-swapping.
"""
import time
import threading
import pytest
import numpy as np
import pandas as pd

from trading_engine.runtime import (
    IndicatorCompiler,
    SecurityValidationError,
    DynamicIndicatorRegistry,
    WarmupManager,
)
from trading_engine.quant_lib.base import Indicator, IndicatorSignal


# ─────────────────────────────────────────────────────────────────────────────
# 1. AST SECURITY & SYNTAX VALIDATION (AC-2.1, AC-2.2)
# ─────────────────────────────────────────────────────────────────────────────

def test_ast_security_validation():
    """AC-2.1: Prohibited imports and dangerous built-ins are strictly rejected."""
    # Prohibited direct import
    code_os = """
import os
class MaliciousIndicator(Indicator):
    pass
"""
    with pytest.raises(SecurityValidationError) as exc:
        IndicatorCompiler.validate_ast(code_os)
    assert "Prohibited import: 'os'" in str(exc.value)

    # Prohibited from import
    code_sub = """
from subprocess import Popen
class MaliciousIndicator(Indicator):
    pass
"""
    with pytest.raises(SecurityValidationError) as exc:
        IndicatorCompiler.validate_ast(code_sub)
    assert "Prohibited import: 'subprocess'" in str(exc.value)

    # Prohibited built-in call
    code_eval = """
class MaliciousIndicator(Indicator):
    def on_bar(self, bar, history):
        eval("2 + 2")
"""
    with pytest.raises(SecurityValidationError) as exc:
        IndicatorCompiler.validate_ast(code_eval)
    assert "Prohibited built-in call: 'eval()'" in str(exc.value)


def test_syntax_error_reporting():
    """AC-2.2: Syntax errors report exact line and column numbers."""
    broken_code = """
class MyIndicator(Indicator):
    def init(self):
        self.fast = 21 + 
"""
    with pytest.raises(SecurityValidationError) as exc:
        IndicatorCompiler.validate_ast(broken_code)
    assert exc.value.line == 4
    assert exc.value.column > 0


# ─────────────────────────────────────────────────────────────────────────────
# 2. DYNAMIC COMPILATION & PARAMETER SCHEMA EXTRACTION (AC-2.3)
# ─────────────────────────────────────────────────────────────────────────────

def test_compiler_and_parameter_extraction():
    """AC-2.3: Valid Python code compiles and inputs are extracted into a UI schema."""
    valid_code = """
from quant_lib import Indicator, ta, input

class CustomScalper(Indicator):
    def init(self):
        self.fast_ema = input.int(21, "Fast EMA Length", minval=2, maxval=200, group="Trend")
        self.mult = input.float(1.5, "ATR Multiplier", minval=0.5, step=0.1, group="Risk")
        self.use_filter = input.bool(True, "Enable Volume Filter", group="Filter")
        self.mode = input.string("CE", "Option Side", options=["CE", "PE"])

    def on_bar(self, bar, history):
        return self.signal("BUY_CE", trigger_price=bar.get("close", 100.0), stop_loss=90.0, target=120.0)
"""
    cls, schema = IndicatorCompiler.compile_and_load(valid_code)
    assert issubclass(cls, Indicator)
    assert "fast_ema" in schema
    assert schema["fast_ema"]["type"] == "int"
    assert schema["fast_ema"]["default"] == 21
    assert schema["fast_ema"]["title"] == "Fast EMA Length"
    assert schema["mult"]["type"] == "float"
    assert schema["mult"]["default"] == 1.5
    assert schema["use_filter"]["type"] == "bool"
    assert schema["mode"]["type"] == "string"
    assert schema["mode"]["options"] == ["CE", "PE"]


# ─────────────────────────────────────────────────────────────────────────────
# 3. HISTORICAL WARM-UP PERFORMANCE (AC-2.4)
# ─────────────────────────────────────────────────────────────────────────────

def test_historical_warmup_performance():
    """AC-2.4: Historical warmup across 1,000 candles completes in <= 10 milliseconds."""
    valid_code = """
from quant_lib import Indicator, ta, input

class MovingAverageScalper(Indicator):
    def init(self):
        self.fast_len = input.int(21, "Fast")
        self.slow_len = input.int(50, "Slow")

    def warm_up(self, history_df):
        close = history_df["close"].values
        self.e21 = ta.ema(close, self.fast_len.value)
        self.e50 = ta.ema(close, self.slow_len.value)
        self.r14 = ta.rsi(close, 14)

    def on_bar(self, bar, history):
        pass
"""
    cls, _ = IndicatorCompiler.compile_and_load(valid_code)
    instance = cls()

    # 1,000 synthetic historical candles
    n = 1000
    prices = np.linspace(24000.0, 24500.0, n)
    df = pd.DataFrame({
        "timestamp": pd.date_range("2024-10-01 09:15", periods=n, freq="min"),
        "open": prices,
        "high": prices + 5.0,
        "low": prices - 5.0,
        "close": prices,
        "volume": np.full(n, 1000),
    })

    # Pre-warm JIT functions
    WarmupManager.warm_up_indicator(instance, df.iloc[:20], trailing_step_bars=5)

    # Benchmark historical warmup across 1,000 bars
    res = WarmupManager.warm_up_indicator(instance, df, trailing_step_bars=50)
    assert res["success"] is True
    assert res["bars_processed"] == 1000
    print(f"\n[WARMUP BENCHMARK] Warmup Time: {res['warmup_time_ms']} ms")
    assert res["warmup_time_ms"] <= 10.0, f"Warmup {res['warmup_time_ms']}ms exceeded 10ms threshold!"


# ─────────────────────────────────────────────────────────────────────────────
# 4. ATOMIC HOT-SWAP LATENCY & THREAD SAFETY (AC-2.5, AC-2.6)
# ─────────────────────────────────────────────────────────────────────────────

def test_atomic_hot_swap_latency():
    """AC-2.5: Swapping an active indicator for a symbol completes in <= 1 millisecond."""
    registry = DynamicIndicatorRegistry()
    dummy_code = """
from quant_lib import Indicator
class DummyInd(Indicator):
    pass
"""
    cls, _ = IndicatorCompiler.compile_and_load(dummy_code)
    instance1 = cls()
    instance2 = cls()

    symbol = "NIFTY24OCT24200CE"
    registry.mount(symbol, "main_strategy", instance1)

    start = time.perf_counter()
    # Atomic swap
    registry.mount(symbol, "main_strategy", instance2)
    elapsed_ms = (time.perf_counter() - start) * 1000.0

    print(f"\n[HOT-SWAP BENCHMARK] Atomic Swap Latency: {elapsed_ms:.4f} ms")
    assert elapsed_ms <= 1.0, f"Hot swap latency {elapsed_ms}ms exceeded 1.0ms limit!"
    active = registry.get_indicators_for_symbol(symbol)
    assert len(active) == 1
    assert active[0] is instance2


def test_zero_tick_loss_concurrency():
    """AC-2.6: High-frequency live tick dispatching runs concurrent with hot swaps without dropping ticks."""
    registry = DynamicIndicatorRegistry()
    dummy_code = """
from quant_lib import Indicator
class TickCounterIndicator(Indicator):
    def __init__(self):
        super().__init__()
        self.tick_count = 0
    def on_tick(self, ltp, vol, ts):
        self.tick_count += 1
"""
    cls, _ = IndicatorCompiler.compile_and_load(dummy_code)
    symbol = "NIFTY24OCT24200CE"

    active_ind = cls()
    registry.mount(symbol, "counter", active_ind)

    ticks_produced = 5000
    ticks_processed = 0
    stop_event = threading.Event()

    def tick_stream_worker():
        nonlocal ticks_processed
        for i in range(ticks_produced):
            inds = registry.get_indicators_for_symbol(symbol)
            for ind in inds:
                ind.on_tick(24100.0, 100, "10:00:00")
                ticks_processed += 1
        stop_event.set()

    def hot_swap_worker():
        swaps = 0
        while not stop_event.is_set():
            new_ind = cls()
            registry.mount(symbol, "counter", new_ind)
            swaps += 1
            time.sleep(0.001)

    t_tick = threading.Thread(target=tick_stream_worker)
    t_swap = threading.Thread(target=hot_swap_worker)

    t_tick.start()
    t_swap.start()

    t_tick.join(timeout=5.0)
    t_swap.join(timeout=5.0)

    # Every produced tick was dispatched to an active instance without crashing or locking
    assert ticks_processed == ticks_produced


# ─────────────────────────────────────────────────────────────────────────────
# 5. SIGNAL ROUTING & CONTRACT INTEGRITY (AC-2.7)
# ─────────────────────────────────────────────────────────────────────────────

def test_signal_routing_contract():
    """AC-2.7: Signals emitted by compiled indicators strictly adhere to execution contract."""
    trigger_code = """
from quant_lib import Indicator

class TriggerIndicator(Indicator):
    def on_tick(self, ltp, vol, ts):
        if ltp >= 24200.0:
            return self.signal(
                action="BUY_CE",
                trigger_price=ltp,
                stop_loss=24150.0,
                target=24300.0,
                tag="TEST_TRIGGER"
            )
        return None
"""
    cls, _ = IndicatorCompiler.compile_and_load(trigger_code)
    ind = cls()

    # Sub-threshold tick: No signal
    sig1 = ind.on_tick(24180.0, 100, "10:00:00")
    assert sig1 is None

    # Triggering tick: Signal emitted
    sig2 = ind.on_tick(24205.0, 500, "10:00:01")
    assert isinstance(sig2, IndicatorSignal)
    assert sig2.action == "BUY_CE"
    assert sig2.trigger_price == 24205.0
    assert sig2.stop_loss == 24150.0
    assert sig2.target == 24300.0
    assert sig2.risk_points == 55.0
    assert sig2.reward_points == 95.0
    assert sig2.rr_ratio == 1.73
    assert sig2.tag == "TEST_TRIGGER"
