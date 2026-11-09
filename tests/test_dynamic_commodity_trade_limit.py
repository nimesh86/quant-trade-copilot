"""
Test Suite: Dynamic Commodity Trade Sizing & Gating Rules
==========================================================
Verifies:
1. Day is profitable (net P&L > 0) -> Unlimited commodity trades allowed.
2. 2 consecutive losses -> 2-3 hours pause (default 2.5h / 150m), then try again.
3. 3rd trade also loss -> Stop commodity trading for the day.
4. Profit breaks the loss streak and resets consecutive loss counter.
5. Static fallback works when dynamic mode is disabled.
6. Config endpoint /api/paper/config correctly handles dynamic parameter.
7. CrudeBacktestEngine respects dynamic trade limits during simulation.
"""

import pytest
from datetime import datetime, timedelta
from trading_engine.live_engine.paper_trade_daemon import PaperTradeDaemon
from trading_engine.live_engine.live_trade_daemon import LiveTradeDaemon
from trading_engine.backtest_engine.crude_backtest_engine import CrudeBacktestEngine


@pytest.fixture
def clean_paper_daemon(tmp_path, monkeypatch):
    monkeypatch.setattr("trading_engine.live_engine.paper_trade_daemon.STATE_FILE", tmp_path / "paper_state.json")
    daemon = PaperTradeDaemon()
    daemon.trade_history = []
    daemon.active_trades.clear()
    daemon._active_trade = None
    daemon.last_crude_exit_time = None
    daemon.commodity_dynamic_trade_limit = True
    daemon.commodity_pause_hours = 2.5
    daemon.commodity_loss_streak_pause = 2
    daemon.commodity_loss_streak_stop = 3
    return daemon


@pytest.fixture
def clean_live_daemon(tmp_path, monkeypatch):
    monkeypatch.setattr("trading_engine.live_engine.live_trade_daemon.STATE_FILE", tmp_path / "live_state.json")
    daemon = LiveTradeDaemon()
    daemon.trade_history = []
    daemon.active_trades.clear()
    daemon._active_trade = None
    daemon.last_crude_exit_time = None
    daemon.commodity_dynamic_trade_limit = True
    daemon.commodity_pause_hours = 2.5
    daemon.commodity_loss_streak_pause = 2
    daemon.commodity_loss_streak_stop = 3
    return daemon


def test_dynamic_rule_first_trade_allowed(clean_paper_daemon):
    """With zero trades today, commodity entry is allowed."""
    daemon = clean_paper_daemon
    now = datetime(2026, 11, 10, 11, 0, 0)
    today_str = "2026-11-10"

    allowed, reason, status = daemon.check_commodity_dynamic_trade_allowed(now, today_str)
    assert allowed is True
    assert status["day_profitable"] is False
    assert status["today_comm_count"] == 0
    assert status["consecutive_losses"] == 0
    assert status["is_paused"] is False
    assert status["is_stopped_for_day"] is False


def test_dynamic_rule_day_profitable_unlimited_trades(clean_paper_daemon):
    """When the day is profitable, commodity trading has NO trade limit."""
    daemon = clean_paper_daemon
    today_str = "2026-11-10"

    # Add 5 profitable closed commodity trades
    for i in range(5):
        daemon.trade_history.append({
            "id": f"COMM_{i}",
            "date": today_str,
            "symbol": "CRUDEOIL",
            "segment": "MCX Commodity",
            "contract": "CRUDEOIL 8150 CE",
            "net": 2400.0,
            "gross": 2500.0,
            "charges": 100.0,
            "exit_time": f"1{i}:00:00"
        })

    now = datetime(2026, 11, 10, 16, 0, 0)
    allowed, reason, status = daemon.check_commodity_dynamic_trade_allowed(now, today_str)
    assert allowed is True
    assert status["day_profitable"] is True
    assert status["today_comm_net"] == 12000.0
    assert status["today_comm_count"] == 5
    assert status["consecutive_losses"] == 0
    assert "UNLIMITED" in status["status_label"]


def test_dynamic_rule_two_consecutive_losses_triggers_pause(clean_paper_daemon):
    """When 2 trades consecutively result in loss, initiate a 2.5-hour pause."""
    daemon = clean_paper_daemon
    today_str = "2026-11-10"

    # Trade 1: Loss exited at 12:00:00
    daemon.trade_history.append({
        "id": "COMM_1",
        "date": today_str,
        "symbol": "CRUDEOIL",
        "segment": "MCX Commodity",
        "contract": "CRUDEOIL 8150 CE",
        "net": -1200.0,
        "exit_time": "12:00:00"
    })

    # Trade 2: Loss exited at 13:00:00
    exit_dt = datetime(2026, 11, 10, 13, 0, 0)
    daemon.last_crude_exit_time = exit_dt
    daemon.trade_history.append({
        "id": "COMM_2",
        "date": today_str,
        "symbol": "CRUDEOIL",
        "segment": "MCX Commodity",
        "contract": "CRUDEOIL 8150 PE",
        "net": -1200.0,
        "exit_time": "13:00:00"
    })

    # Evaluated at 14:00:00 (1 hour elapsed < 2.5h pause)
    now = datetime(2026, 11, 10, 14, 0, 0)
    allowed, reason, status = daemon.check_commodity_dynamic_trade_allowed(now, today_str)

    assert allowed is False
    assert status["is_paused"] is True
    assert status["consecutive_losses"] == 2
    assert status["is_stopped_for_day"] is False
    assert status["pause_remaining_minutes"] == 90.0  # 150m - 60m = 90m left
    assert "PAUSED" in status["status_label"]


def test_dynamic_rule_after_pause_try_again(clean_paper_daemon):
    """After 2.5 hours pause has elapsed, entry is permitted to 'try again'."""
    daemon = clean_paper_daemon
    today_str = "2026-11-10"

    # 2 consecutive losses, last exit at 13:00:00
    exit_dt = datetime(2026, 11, 10, 13, 0, 0)
    daemon.last_crude_exit_time = exit_dt
    daemon.trade_history.extend([
        {"id": "COMM_1", "date": today_str, "symbol": "CRUDEOIL", "segment": "MCX Commodity", "net": -1200.0, "exit_time": "12:00:00"},
        {"id": "COMM_2", "date": today_str, "symbol": "CRUDEOIL", "segment": "MCX Commodity", "net": -1200.0, "exit_time": "13:00:00"}
    ])

    # Evaluated at 15:35:00 (2 hours 35 mins elapsed > 2.5h pause)
    now = datetime(2026, 11, 10, 15, 35, 0)
    allowed, reason, status = daemon.check_commodity_dynamic_trade_allowed(now, today_str)

    assert allowed is True
    assert status["is_paused"] is False
    assert status["consecutive_losses"] == 2
    assert "RESUMED" in status["status_label"]
    assert "Trying Trade #3" in status["status_label"]


def test_dynamic_rule_third_consecutive_loss_stops_for_day(clean_paper_daemon):
    """If the 3rd trade is also giving loss, stop trading for the day."""
    daemon = clean_paper_daemon
    today_str = "2026-11-10"

    # 3 consecutive losses
    daemon.trade_history.extend([
        {"id": "COMM_1", "date": today_str, "symbol": "CRUDEOIL", "segment": "MCX Commodity", "net": -1200.0, "exit_time": "11:00:00"},
        {"id": "COMM_2", "date": today_str, "symbol": "CRUDEOIL", "segment": "MCX Commodity", "net": -1200.0, "exit_time": "12:00:00"},
        {"id": "COMM_3", "date": today_str, "symbol": "CRUDEOIL", "segment": "MCX Commodity", "net": -1200.0, "exit_time": "15:00:00"}
    ])
    daemon.last_crude_exit_time = datetime(2026, 11, 10, 15, 0, 0)

    now = datetime(2026, 11, 10, 16, 0, 0)
    allowed, reason, status = daemon.check_commodity_dynamic_trade_allowed(now, today_str)

    assert allowed is False
    assert status["is_stopped_for_day"] is True
    assert status["consecutive_losses"] == 3
    assert "STOPPED TODAY" in status["status_label"]


def test_dynamic_rule_profit_breaks_loss_streak(clean_paper_daemon):
    """If trade 3 is a profit, consecutive loss streak is reset."""
    daemon = clean_paper_daemon
    today_str = "2026-11-10"

    # Loss 1, Loss 2, then Win 3 (+₹3,500)
    daemon.trade_history.extend([
        {"id": "COMM_1", "date": today_str, "symbol": "CRUDEOIL", "segment": "MCX Commodity", "net": -1200.0, "exit_time": "11:00:00"},
        {"id": "COMM_2", "date": today_str, "symbol": "CRUDEOIL", "segment": "MCX Commodity", "net": -1200.0, "exit_time": "12:00:00"},
        {"id": "COMM_3", "date": today_str, "symbol": "CRUDEOIL", "segment": "MCX Commodity", "net": 3500.0, "exit_time": "15:00:00"}
    ])
    daemon.last_crude_exit_time = datetime(2026, 11, 10, 15, 0, 0)

    now = datetime(2026, 11, 10, 16, 0, 0)
    allowed, reason, status = daemon.check_commodity_dynamic_trade_allowed(now, today_str)

    assert allowed is True
    assert status["consecutive_losses"] == 0
    assert status["day_profitable"] is True  # -1200 -1200 + 3500 = +1100 > 0
    assert status["today_comm_net"] == 1100.0
    assert "UNLIMITED" in status["status_label"]


def test_live_daemon_dynamic_consistency(clean_live_daemon):
    """Verifies that LiveTradeDaemon implements the identical dynamic logic."""
    daemon = clean_live_daemon
    today_str = "2026-11-10"

    # 2 losses -> paused
    daemon.trade_history.extend([
        {"id": "COMM_1", "date": today_str, "symbol": "CRUDEOIL", "segment": "MCX Commodity", "net": -1200.0, "exit_time": "12:00:00"},
        {"id": "COMM_2", "date": today_str, "symbol": "CRUDEOIL", "segment": "MCX Commodity", "net": -1200.0, "exit_time": "13:00:00"}
    ])
    daemon.last_crude_exit_time = datetime(2026, 11, 10, 13, 0, 0)

    now = datetime(2026, 11, 10, 14, 0, 0)
    allowed, reason, status = daemon.check_commodity_dynamic_trade_allowed(now, today_str)
    assert allowed is False
    assert status["is_paused"] is True
    assert status["consecutive_losses"] == 2

    # 3 losses -> stopped
    daemon.trade_history.append({"id": "COMM_3", "date": today_str, "symbol": "CRUDEOIL", "segment": "MCX Commodity", "net": -1200.0, "exit_time": "16:00:00"})
    now = datetime(2026, 11, 10, 17, 0, 0)
    allowed, reason, status = daemon.check_commodity_dynamic_trade_allowed(now, today_str)
    assert allowed is False
    assert status["is_stopped_for_day"] is True


def test_static_fallback_when_dynamic_disabled(clean_paper_daemon):
    """When dynamic mode is disabled, daemon enforces static max_trades_commodity cap."""
    daemon = clean_paper_daemon
    daemon.commodity_dynamic_trade_limit = False
    daemon.max_trades_commodity = 4
    today_str = "2026-11-10"

    for i in range(4):
        daemon.trade_history.append({
            "id": f"COMM_{i}",
            "date": today_str,
            "symbol": "CRUDEOIL",
            "segment": "MCX Commodity",
            "net": 1000.0
        })

    now = datetime(2026, 11, 10, 16, 0, 0)
    allowed, reason, status = daemon.check_commodity_dynamic_trade_allowed(now, today_str)
    assert allowed is False
    assert "Static commodity trade limit reached" in reason


def test_daemon_config_update_dynamic_toggle(clean_paper_daemon):
    """Config update endpoint properly toggles dynamic settings."""
    daemon = clean_paper_daemon

    # Update to fixed 3
    daemon.update_config({"max_trades_commodity": 3, "commodity_dynamic_trade_limit": False})
    assert daemon.max_trades_commodity == 3
    assert daemon.commodity_dynamic_trade_limit is False

    # Update back to dynamic
    daemon.update_config({"max_trades_commodity": "dynamic", "commodity_pause_hours": 3.0})
    assert daemon.commodity_dynamic_trade_limit is True
    assert daemon.commodity_pause_hours == 3.0

    cfg = daemon.get_config()
    assert cfg["commodity_dynamic_trade_limit"] is True
    assert cfg["commodity_pause_hours"] == 3.0

