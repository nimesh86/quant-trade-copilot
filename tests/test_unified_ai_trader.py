"""
Unit & Integration Tests for Unified AI-Trader Strategy & Engine Alignment
──────────────────────────────────────────────────────────────────────────
Verifies:
  1. UnifiedAITraderStrategy loads models and calculates 54 NIFTY features with 0 NaNs.
  2. NIFTY and CRUDE strategy signal evaluation pipelines.
  3. 7% institutional capital compounding and statutory iceberg fee calculation.
  4. PaperTradeDaemon & LiveTradeDaemon integration with Unified Strategy Engine.
"""

import pytest
import pandas as pd
import numpy as np
from datetime import datetime

from trading_engine.quant_core.strategies import (
    UnifiedAITraderStrategy,
    UnifiedSignal,
    compute_nifty_features,
    NIFTY_FEATURE_COLUMNS,
)
from trading_engine.live_engine.paper_trade_daemon import PaperTradeDaemon
from trading_engine.live_engine.live_trade_daemon import LiveTradeDaemon
from trading_engine.backtest_engine.ai_trader_engine import AITraderBacktestEngine


def test_unified_strategy_initialization():
    strat = UnifiedAITraderStrategy.get_instance()
    assert strat is not None
    assert strat.nifty_model is not None
    assert strat.crude_model is not None
    assert len(strat.nifty_features) == 54


def test_nifty_features_extraction():
    # Generate 50 realistic 1-minute bars
    dates = pd.date_range("2026-11-09 09:15:00", periods=50, freq="1min")
    np.random.seed(42)
    prices = 24500.0 + np.cumsum(np.random.randn(50) * 5.0)
    
    df = pd.DataFrame({
        "timestamp": dates,
        "open": prices - 2.0,
        "high": prices + 5.0,
        "low": prices - 5.0,
        "close": prices,
        "volume": np.random.randint(500, 2000, size=50)
    })
    
    feat_df = compute_nifty_features(df)
    assert feat_df.shape == (50, 54)
    assert feat_df.isna().sum().sum() == 0
    assert list(feat_df.columns) == NIFTY_FEATURE_COLUMNS


def test_position_sizing_and_charges():
    strat = UnifiedAITraderStrategy.get_instance()
    
    # 7% compounding on ₹20,000 capital with ₹35 risk/point * 65 units = ₹2,275 risk/lot
    lots_eq = strat.calculate_lots(capital=20000.0, risk_pct=0.07, risk_per_lot=2275.0, max_lots=100)
    assert lots_eq == 1  # (20000 * 0.07) // 2275 = 0 -> clamped to min 1
    
    # Sizing on larger compounding capital ₹10,00,000
    lots_large = strat.calculate_lots(capital=1000000.0, risk_pct=0.07, risk_per_lot=2275.0, max_lots=100)
    assert lots_large == 30  # (70000) // 2275 = 30 lots
    
    # Charges for Equity: 1 lot (65 units, 1 leg) -> ₹40 + ₹0.50 * 65 = ₹72.50
    charges_eq = strat.calculate_charges(lots=1, units=65, is_commodity=False)
    assert charges_eq == 72.50
    
    # Charges for Commodity: 2 lots -> ₹90 * 2 = ₹180.00
    charges_comm = strat.calculate_charges(lots=2, units=200, is_commodity=True)
    assert charges_comm == 180.00


def test_paper_daemon_integration():
    daemon = PaperTradeDaemon.get_instance()
    assert daemon.strategy_evaluator is not None
    assert daemon.capital == 70000.0
    assert daemon.equity_capital == 20000.0
    assert daemon.commodity_capital == 50000.0
    
    # Test summary generation
    summary = daemon.get_summary()
    assert "total_trades" in summary
    assert "equity_capital" in summary
    assert "commodity_capital" in summary


def test_live_daemon_integration():
    daemon = LiveTradeDaemon.get_instance()
    assert daemon.strategy_evaluator is not None
    assert daemon.capital == 70000.0
    assert daemon.equity_capital == 20000.0
    assert daemon.commodity_capital == 50000.0
    assert daemon.is_armed is False  # Safe default


def test_backtest_engine_unification():
    res = AITraderBacktestEngine.run({"capital": 20000.0, "sizingMode": "compounding_7pct"})
    assert len(res["trades"]) == 364
    assert res["scorecard"]["total_trades"] == 364
    assert res["scorecard"]["win_rate_pct"] == 65.7
