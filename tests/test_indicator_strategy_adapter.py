"""
Test suite for IndicatorStrategyAdapter and StrategyDetectorAdapter integration.
Verifies that any BaseIndicator (like BSSProV6Indicator) plugs directly into
BaseStrategy and StrategyDetectorAdapter for live paper trading and live execution.
"""
import pytest
from datetime import datetime

from trading_engine.config import EngineConfig
from trading_engine.strategy.base import StrategySignal
from trading_engine.strategy.indicator_strategy import IndicatorStrategyAdapter
from trading_engine.indicators.bss_pro_v6 import BSSProV6Indicator
from trading_engine.adapters.strategy_detector_adapter import StrategyDetectorAdapter


def test_indicator_strategy_adapter_initialization():
    cfg = EngineConfig()
    ind = BSSProV6Indicator()
    strat = IndicatorStrategyAdapter(ind, cfg, {"dom_thresh": 55.0}, name_override="BSS_PRO_V6")

    assert strat.name == "BSS_PRO_V6"
    assert strat.enabled is True
    assert strat.indicator.dom_thresh.value == 55.0


def test_strategy_detector_adapter_loads_bss_pro_v6():
    cfg = EngineConfig()
    detector = StrategyDetectorAdapter(cfg)

    strategy_names = [s.name for s in detector.strategies]
    print("Registered strategies:", strategy_names)

    assert "AMD_VOLUME_PROFILE" in strategy_names
    assert "BSS_PRO_V6" in strategy_names


def test_dynamic_indicator_hot_mount():
    cfg = EngineConfig()
    detector = StrategyDetectorAdapter(cfg)
    initial_count = len(detector.strategies)

    # Hot-mount a custom indicator instance
    custom_ind = BSSProV6Indicator()
    detector.register_indicator(custom_ind, name="CUSTOM_BSS_PRO_HOT")

    strategy_names = [s.name for s in detector.strategies]
    assert "CUSTOM_BSS_PRO_HOT" in strategy_names
    assert len(detector.strategies) == initial_count + 1
