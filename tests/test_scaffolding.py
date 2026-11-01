"""
Automated Test Suite for Task 0: Project Scaffolding, Core Types & Shared Contracts.
Validates all acceptance criteria: AC-0.1, AC-0.2, AC-0.3, AC-0.4, AC-0.5.
"""
import pytest
from pathlib import Path


def test_directory_structure_exists():
    """AC-0.1: All sub-packages exist with valid __init__.py files."""
    repo_root = Path(__file__).resolve().parent.parent
    expected_dirs = [
        repo_root / "trading_engine" / "quant_lib",
        repo_root / "trading_engine" / "runtime",
        repo_root / "trading_engine" / "data",
        repo_root / "trading_engine" / "replay",
        repo_root / "trading_engine" / "indicators",
    ]

    for d in expected_dirs:
        assert d.exists(), f"Directory missing: {d}"
        init_file = d / "__init__.py"
        assert init_file.exists(), f"__init__.py missing in: {d}"


def test_core_contracts_instantiation():
    """AC-0.2: Core contracts instantiate and serialize properly."""
    from trading_engine.quant_lib.base import (
        IndicatorSignal,
        SynchronizedBar,
        InputDescriptor,
        BaseIndicator,
        Indicator,
        input as input_factory,
    )

    # 1. IndicatorSignal instantiation
    sig = IndicatorSignal(
        action="BUY_CE",
        trigger_price=180.0,
        stop_loss=160.0,
        target=220.0,
        tag="BREAKOUT_CE"
    )
    assert sig.action == "BUY_CE"
    assert sig.trigger_price == 180.0
    assert sig.risk_points == 20.0
    assert sig.reward_points == 40.0
    assert sig.rr_ratio == 2.0

    # 2. SynchronizedBar instantiation
    bar = SynchronizedBar(
        timestamp="2024-10-24 09:15:00",
        spot_open=24150.0,
        spot_high=24180.0,
        spot_low=24140.0,
        spot_close=24175.0,
        spot_volume=125000,
        opt_open=110.0,
        opt_high=125.0,
        opt_low=108.0,
        opt_close=122.5,
        opt_volume=45000,
        opt_oi=1500000,
    )
    assert bar.spot_close == 24175.0
    assert bar.opt_close == 122.5

    # 3. InputDescriptor & factory helpers
    d_int = input_factory.int(21, "Fast EMA", minval=2, maxval=200, tooltip="Length of fast EMA")
    assert d_int.default == 21
    assert d_int.value == 21
    assert d_int.input_type == "int"
    schema = d_int.to_dict()
    assert schema["title"] == "Fast EMA"
    assert schema["minval"] == 2

    d_float = input_factory.float(1.5, "ATR Multiplier", step=0.1)
    assert d_float.value == 1.5

    d_bool = input_factory.bool(True, "Use Volume Filter")
    assert d_bool.value is True

    # 4. BaseIndicator subclassing
    class CustomIndicator(Indicator):
        def init(self):
            self.ema_len = input_factory.int(21, "EMA Length")

    ind = CustomIndicator(ema_len=50)
    assert ind.ema_len.value == 50
    sig_out = ind.signal("BUY_CE", trigger_price=100.0, stop_loss=90.0, target=120.0)
    assert sig_out.action == "BUY_CE"
    assert sig_out.rr_ratio == 2.0


def test_environment_dependencies():
    """AC-0.3: Key mathematical and runtime libraries are installed and functional."""
    import duckdb
    import numba
    import numpy as np
    import scipy
    import pandas as pd
    import aiohttp
    import fastapi

    # Verify DuckDB in-memory works
    con = duckdb.connect(":memory:")
    res = con.execute("SELECT 42 AS test").fetchone()
    assert res[0] == 42
    con.close()

    # Verify NumPy works
    arr = np.array([1.0, 2.0, 3.0])
    assert arr.mean() == 2.0

    # Verify Numba works
    @numba.njit
    def add_numba(a, b):
        return a + b
    assert add_numba(10, 20) == 30


def test_signal_auto_calculation():
    """AC-0.4: Risk, reward, and R:R ratio mathematics are strictly accurate."""
    from trading_engine.quant_lib.base import IndicatorSignal

    # Case 1: Long CE (target > trigger > stop_loss)
    sig1 = IndicatorSignal(
        action="BUY_CE",
        trigger_price=200.0,
        stop_loss=185.0,
        target=230.0,
    )
    assert sig1.risk_points == 15.0
    assert sig1.reward_points == 30.0
    assert sig1.rr_ratio == 2.0

    # Case 2: Short / Put entry with fractional points
    sig2 = IndicatorSignal(
        action="BUY_PE",
        trigger_price=150.25,
        stop_loss=140.25,
        target=175.25,
    )
    assert sig2.risk_points == 10.0
    assert sig2.reward_points == 25.0
    assert sig2.rr_ratio == 2.5

    # Case 3: Zero stop-loss (no risk calculation error)
    sig3 = IndicatorSignal(
        action="HOLD",
        trigger_price=100.0,
    )
    assert sig3.risk_points == 0.0
    assert sig3.reward_points == 0.0
    assert sig3.rr_ratio == 0.0


def test_clean_package_imports():
    """AC-0.5: All new package namespaces import without circular errors."""
    import sys
    repo_root = Path(__file__).resolve().parent.parent
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))

    from trading_engine import quant_lib
    from trading_engine import runtime
    from trading_engine import data
    from trading_engine import replay
    from trading_engine import indicators

    assert quant_lib is not None
    assert runtime is not None
    assert data is not None
    assert replay is not None
    assert indicators is not None
