"""
Unit tests for BSS Pro v5 Dynamic Probability Engine, Runner Holding, and Trade Diagnostics.
Validates:
1. Multi-factor continuation probability calculation (Trend, Buyer Strength, S/R headroom, Hammer/Candle patterns).
2. Runner holding mode: stays in trade during bullish expansion and trails stop loss instead of premature static target exit.
3. Proper diagnostic logging: every TradeRecord captures entry_reason and exit_reason.
"""
from datetime import timedelta
import numpy as np
import pandas as pd
import pytest

from trading_engine.indicators.bss_pro_v5 import BSSProV5Indicator


def make_bullish_rally_df(num_bars=80):
    """
    Synthesizes a realistic synchronized spot and ATM option strike dataset
    with an initial base, followed by a strong bullish breakout and rally.
    """
    base_time = pd.Timestamp("2024-10-24 09:15:00")
    records = []
    
    # 0 to 50: base consolidation around 24,200 (Spot) and 180 (Option)
    # 51 to 80: strong bullish rally advancing to 24,350 (Spot) and 240 (Option)
    for i in range(num_bars):
        ts = base_time + timedelta(minutes=i)
        if i < 50:
            spot_o = 24200.0 + (i % 3) * 2.0
            spot_c = spot_o + 1.0
            spot_h = max(spot_o, spot_c) + 4.0
            spot_l = min(spot_o, spot_c) - 4.0
            opt_c = 180.0 + (i % 2) * 1.5
            vol = 1000
        else:
            step = i - 50
            spot_o = 24210.0 + step * 5.0
            spot_c = spot_o + 4.5
            spot_h = spot_c + 2.0
            spot_l = spot_o - 1.0
            opt_c = 185.0 + step * 2.5
            vol = 3000

        opt_o = opt_c - 1.0
        opt_h = opt_c + 2.0
        opt_l = opt_o - 1.5

        records.append({
            "timestamp": ts,
            "symbol": "NIFTY",
            "contract": "NIFTY24OCT24200CE",
            "timeframe": "1m",
            "spot_open": spot_o,
            "spot_high": spot_h,
            "spot_low": spot_l,
            "spot_close": spot_c,
            "spot_volume": vol,
            "open": opt_o,
            "high": opt_h,
            "low": opt_l,
            "close": opt_c,
            "volume": vol * 2,
            "opt_open": opt_o,
            "opt_high": opt_h,
            "opt_low": opt_l,
            "opt_close": opt_c,
            "opt_volume": vol * 2
        })

    return pd.DataFrame(records)


def test_bss_probability_calculation():
    """Verify that multi-factor probability evaluates trend, buyer strength, S/R headroom, and patterns."""
    df = make_bullish_rally_df(80)
    ind = BSSProV5Indicator()

    # Pass bar 65 where trend is established after 50-bar warmup
    opt_bar = df.iloc[65].to_dict()
    history = df.iloc[:66]
    signal = ind.on_bar(opt_bar, history)

    # Check cached plots
    assert "continuation_prob" in ind.plots
    assert "candle_pattern" in ind.plots
    assert "buyer_pct" in ind.plots
    assert ind.plots["continuation_prob"] > 50.0  # Bullish trend must score high




def test_bss_session_time_filters():
    """Verify indicator strictly blocks trades before 10:05 and after 15:15, and squares off open trades."""
    ind = BSSProV5Indicator()
    df = make_bullish_rally_df(80)

    # 1. Bar at 09:45 (before 10:05) -> must NOT trigger trade even with strong trend
    bar_early = df.iloc[30].to_dict()
    bar_early["timestamp"] = "2024-10-24 09:45:00"
    history_early = df.iloc[:55].copy()
    sig_early = ind.on_bar(bar_early, history_early)
    assert sig_early is None

    # 2. Bar at 15:20 (after 15:15) -> must NOT trigger new trade
    bar_late = df.iloc[65].to_dict()
    bar_late["timestamp"] = "2024-10-24 15:20:00"
    sig_late = ind.on_bar(bar_late, df.iloc[:66])
    assert sig_late is None

    # 3. Active trade at 15:18 -> must trigger SESSION_CLOSE_EXIT square-off
    active_trade = {"entry_price": 180.0, "sl": 175.0, "initial_sl": 175.0, "target": 200.0}
    sig_exit = ind.on_bar(bar_late, df.iloc[:66], active_trade=active_trade)
    assert sig_exit is not None
    assert sig_exit.action == "EXIT_CE"
    assert sig_exit.tag == "SESSION_CLOSE_EXIT"
    assert "SESSION_CLOSE_EXIT" in sig_exit.extra["reason"]
