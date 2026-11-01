"""
Unit tests for Vectorized Backtester Charges, ICICI Direct Defaults, and Bifurcation.
"""
import pytest
import pandas as pd
import numpy as np

from trading_engine.replay.backtester import (
    BrokerChargesConfig,
    TradeChargesBifurcation,
    VectorizedBacktester,
    TradeRecord,
    BacktestScorecard
)
from trading_engine.quant_lib.base import Indicator, IndicatorSignal


class DummySignalIndicator(Indicator):
    """Generates a test trade signal."""
    def __init__(self):
        super().__init__(name="DummySignalIndicator")
        self.bars = 0

    def on_tick(self, tick):
        return None

    def on_bar(self, bar, history=None):
        self.bars += 1
        if self.bars == 5:
            return IndicatorSignal(action="BUY_CE", trigger_price=105.0, stop_loss=80.0, target=140.0)
        return None


def test_icici_direct_charges_defaults():
    """Verify official ICICI Direct options trading charges calculation."""
    cfg = BrokerChargesConfig()
    assert cfg.broker_name == "ICICI Direct"
    assert cfg.brokerage_per_order == 20.0
    assert cfg.stt_rate == 0.0010
    assert cfg.exchange_txn_rate == 0.0003503
    assert cfg.gst_rate == 0.18
    # Dynamic lot size defaults to None until resolved by symbol or explicitly specified
    assert cfg.lot_size is None

    cfg_custom = BrokerChargesConfig(lot_size=65)
    assert cfg_custom.lot_size == 65

    # Calculate charges for Buy at 100, Sell at 140, Qty = 50 (1 lot)
    # Buy turnover = 100 * 50 = 5000
    # Sell turnover = 140 * 50 = 7000
    # Total turnover = 12000
    charges = cfg.calculate_trade_charges(entry_price=100.0, exit_price=140.0, qty=50)

    # 1. Brokerage: 20 * 2 = 40.0
    assert charges.brokerage == 40.0

    # 2. STT: 7000 * 0.0010 = 7.0
    assert charges.stt == 7.0

    # 3. Exchange Txn: 12000 * 0.0003503 = 4.20
    assert round(charges.exchange_txn, 2) == 4.20

    # 4. SEBI: 12000 * 0.000001 = 0.01
    assert round(charges.sebi, 2) == 0.01

    # 5. GST: 18% of (40.0 + 4.20 + 0.01) = 18% of 44.21 = 7.96
    assert round(charges.gst, 2) == 7.96

    # 6. Stamp duty: 5000 * 0.00003 = 0.15
    assert round(charges.stamp_duty, 2) == 0.15

    # Total charges: 40 + 7 + 4.20 + 0.01 + 7.96 + 0.15 = 59.32
    assert charges.total_charges == round(40.0 + 7.0 + 4.20 + 0.01 + 7.96 + 0.15, 2)


def test_trade_record_bifurcation_and_net_pnl():
    """Verify TradeRecord carries both gross and net P&L with full charges breakdown."""
    cfg = BrokerChargesConfig(brokerage_per_order=20.0, lot_size=50)
    charges = cfg.calculate_trade_charges(100.0, 140.0, 50)
    gross_pnl_pts = 40.0
    gross_pnl_inr = 40.0 * 50  # 2000.0
    net_pnl_inr = gross_pnl_inr - charges.total_charges
    net_pnl_pts = net_pnl_inr / 50

    record = TradeRecord(
        entry_time="2026-10-30 09:30:00",
        exit_time="2026-10-30 09:45:00",
        action="BUY_CE",
        entry_price=100.0,
        exit_price=140.0,
        gross_pnl_points=gross_pnl_pts,
        gross_pnl_inr=gross_pnl_inr,
        net_pnl_points=round(net_pnl_pts, 2),
        net_pnl_inr=round(net_pnl_inr, 2),
        charges=charges,
        total_charges_inr=charges.total_charges,
        return_pct=40.0,
        exit_reason="TARGET",
        pnl_points=round(net_pnl_pts, 2),
        pnl_inr=round(net_pnl_inr, 2)
    )

    d = record.to_dict()
    assert "charges" in d
    assert d["charges"]["brokerage"] == 40.0
    assert d["charges"]["stt"] == 7.0
    assert d["total_charges_inr"] == charges.total_charges
    assert d["gross_pnl_inr"] == 2000.0
    assert d["net_pnl_inr"] < 2000.0
    assert d["net_pnl_inr"] == round(2000.0 - charges.total_charges, 2)


def test_vectorized_backtester_with_charges_scorecard():
    """Verify VectorizedBacktester computes charges bifurcation across all trades."""
    # Build 100 synthetic bars
    timestamps = pd.date_range("2026-10-30 09:15:00", periods=100, freq="1min")
    df = pd.DataFrame({
        "timestamp": timestamps,
        "open": np.linspace(24000, 24100, 100),
        "high": np.linspace(24010, 24110, 100),
        "low": np.linspace(23990, 24090, 100),
        "close": np.linspace(24005, 24105, 100),
        "volume": [1000] * 100,
        "opt_open": [100.0] * 55 + [105.0] * 5 + [145.0] * 40,
        "opt_high": [105.0] * 55 + [110.0] * 5 + [150.0] * 40,
        "opt_low": [98.0] * 55 + [102.0] * 5 + [142.0] * 40,
        "opt_close": [101.0] * 55 + [108.0] * 5 + [148.0] * 40,
        "opt_volume": [500] * 100,
    })

    indicator = DummySignalIndicator()
    cfg = BrokerChargesConfig(broker_name="ICICI Direct", brokerage_per_order=20.0, lot_size=50)
    backtester = VectorizedBacktester(lot_size=50, charges_config=cfg)

    scorecard = backtester.run_backtest(df, indicator, warmup_bars=50)

    assert scorecard.total_trades >= 1
    assert scorecard.total_charges_inr > 0
    assert "brokerage" in scorecard.charges_bifurcation
    assert "stt" in scorecard.charges_bifurcation
    assert "exchange_txn" in scorecard.charges_bifurcation
    assert "gst" in scorecard.charges_bifurcation
    assert "stamp_duty" in scorecard.charges_bifurcation
    assert "sebi" in scorecard.charges_bifurcation
    assert scorecard.total_pnl_inr == round(scorecard.gross_pnl_inr - scorecard.total_charges_inr, 2)
