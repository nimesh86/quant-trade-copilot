"""
Comprehensive Automated Test Suite for Task 1: Quant Mathematical Library (quant_lib).
Validates AC-1.1 through AC-1.9: Mathematical accuracy, boundary behavior, and sub-15µs speed benchmarks.
"""
import time
import pytest
import numpy as np
import pandas as pd

from trading_engine.quant_lib import ta, structure, order_flow, options


# ─────────────────────────────────────────────────────────────────────────────
# 1. MOVING AVERAGES (AC-1.1)
# ─────────────────────────────────────────────────────────────────────────────

def test_moving_averages_precision():
    """AC-1.1: Moving averages calculation and precision."""
    # Synthetic linear trend
    data = np.array([10.0, 11.0, 12.0, 13.0, 14.0, 15.0, 16.0, 17.0, 18.0, 19.0])
    vol = np.array([100, 200, 150, 300, 250, 400, 350, 500, 450, 600])

    # 1. SMA (Window=3)
    sma_vals = ta.sma(data, 3)
    assert np.isclose(sma_vals[2], 11.0)
    assert np.isclose(sma_vals[9], 18.0)

    # 2. EMA (Window=3, alpha=0.5)
    ema_vals = ta.ema(data, 3)
    # EMA formula: alpha = 2/(3+1) = 0.5
    # EMA[0]=10, EMA[1]=0.5*11 + 0.5*10 = 10.5, EMA[2]=0.5*12 + 0.5*10.5 = 11.25
    assert np.isclose(ema_vals[1], 10.5)
    assert np.isclose(ema_vals[2], 11.25)

    # 3. RMA (Wilder's alpha = 1/3)
    rma_vals = ta.rma(data, 3)
    assert np.isclose(rma_vals[1], (1.0/3.0)*11.0 + (2.0/3.0)*10.0)

    # 4. WMA (Weights 1, 2, 3)
    wma_vals = ta.wma(data, 3)
    expected_wma_2 = (10.0 * 1 + 11.0 * 2 + 12.0 * 3) / 6.0  # (10 + 22 + 36)/6 = 68/6 = 11.3333
    assert np.isclose(wma_vals[2], expected_wma_2)

    # 5. HMA (Hull MA)
    hma_vals = ta.hma(data, 4)
    assert len(hma_vals) == len(data)
    assert not np.isnan(hma_vals[-1])

    # 6. VWMA (Volume Weighted)
    vwma_vals = ta.vwma(data, vol, 3)
    assert len(vwma_vals) == len(data)
    assert not np.isnan(vwma_vals[-1])

    # 7. DEMA & TEMA
    dema_vals = ta.dema(data, 3)
    tema_vals = ta.tema(data, 3)
    assert len(dema_vals) == len(data)
    assert len(tema_vals) == len(data)

    # 8. ALMA
    alma_vals = ta.alma(data, 4)
    assert len(alma_vals) == len(data)

    # 9. VWAP
    h = data + 0.5
    l = data - 0.5
    c = data
    vwap_vals = ta.vwap(h, l, c, vol)
    assert len(vwap_vals) == len(data)
    assert vwap_vals[-1] > 0.0


# ─────────────────────────────────────────────────────────────────────────────
# 2. OSCILLATORS & MOMENTUM (AC-1.2)
# ─────────────────────────────────────────────────────────────────────────────

def test_oscillators_precision():
    """AC-1.2: RSI, MACD, Stochastic, CCI, and MFI accuracy and bounds."""
    # Price oscillating between 100 and 110
    prices = np.array([100.0, 102.0, 105.0, 104.0, 108.0, 110.0, 106.0, 103.0, 105.0, 109.0,
                       112.0, 115.0, 111.0, 108.0, 112.0, 116.0, 114.0, 118.0, 120.0, 117.0])
    high = prices + 1.0
    low = prices - 1.0
    vol = np.full(len(prices), 1000.0)

    # 1. RSI
    rsi_vals = ta.rsi(prices, 14)
    assert len(rsi_vals) == len(prices)
    assert 0.0 <= rsi_vals[-1] <= 100.0
    # Strong upward move should yield RSI > 50
    assert rsi_vals[-1] > 50.0

    # 2. MACD
    macd_line, sig_line, hist = ta.macd(prices, fast=4, slow=8, signal=3)
    assert len(macd_line) == len(prices)
    assert len(sig_line) == len(prices)
    assert len(hist) == len(prices)
    assert np.allclose(hist, macd_line - sig_line)

    # 3. Stochastic
    k, d = ta.stoch(prices, high, low, k_len=5, d_len=3)
    assert 0.0 <= k[-1] <= 100.0
    assert 0.0 <= d[-1] <= 100.0

    # 4. CCI & MFI
    cci_vals = ta.cci(high, low, prices, length=5)
    mfi_vals = ta.mfi(high, low, prices, vol, length=5)
    assert len(cci_vals) == len(prices)
    assert 0.0 <= mfi_vals[-1] <= 100.0


def test_crossover_crossunder():
    """AC-1.2: Strict edge-triggered boolean flags for crossover and crossunder."""
    fast = np.array([10.0, 12.0, 14.0, 13.0, 11.0])
    slow = np.array([11.0, 11.5, 12.0, 13.5, 14.0])

    # At idx 0: fast(10) < slow(11)
    # At idx 1: fast(12) > slow(11.5) -> Crossover!
    # At idx 2: fast(14) > slow(12.0) -> No new cross
    # At idx 3: fast(13) < slow(13.5) -> Crossunder!
    co = ta.crossover(fast, slow)
    cu = ta.crossunder(fast, slow)

    assert co[1] is True or co[1] == 1
    assert co[2] is False or co[2] == 0
    assert cu[3] is True or cu[3] == 1
    assert cu[4] is False or cu[4] == 0


# ─────────────────────────────────────────────────────────────────────────────
# 3. VOLATILITY & SUPERTREND (AC-1.3)
# ─────────────────────────────────────────────────────────────────────────────

def test_supertrend_reversal_logic():
    """AC-1.3: Supertrend direction flips strictly upon confirmed close beyond opposite band."""
    # Bullish trend then sudden dump
    close = np.array([100.0, 102.0, 105.0, 108.0, 110.0, 112.0, 115.0, 118.0, 95.0, 90.0, 88.0])
    high = close + 1.0
    low = close - 1.0

    direction, st_line = ta.supertrend(high, low, close, length=3, multiplier=2.0)

    # Bullish run
    assert direction[7] == 1  # Bullish
    assert st_line[7] < close[7]  # Supertrend trailing below price

    # Dump at bar 8 (close drops from 118 to 95)
    assert direction[8] == -1  # Flipped to Bearish
    assert st_line[8] > close[8]  # Supertrend band flips above price


# ─────────────────────────────────────────────────────────────────────────────
# 4. MARKET STRUCTURE & DYNAMIC S/R (AC-1.4, AC-1.5)
# ─────────────────────────────────────────────────────────────────────────────

def test_dynamic_sr_body_clustering():
    """AC-1.4: Dynamic S/R body clustering ignores wick sweeps and clusters levels within ATR."""
    n = 30
    np.random.seed(42)
    # Price oscillating around 24,000 to 24,200
    base = 24100.0 + np.sin(np.linspace(0, 4 * np.pi, n)) * 80.0
    open_ = base
    close = base + np.random.uniform(-5.0, 5.0, n)
    # Add an extreme wick sweep that reverses immediately (e.g. wick to 24,350 but body at 24,180)
    high = np.maximum(open_, close) + 5.0
    low = np.minimum(open_, close) - 5.0
    high[10] = 24350.0  # Extreme fake wick sweep
    close[10] = 24175.0
    open_[10] = 24170.0

    res = structure.dynamic_sr(open_, high, low, close, left=2, right=2, max_levels=3)

    # The resistance should be clustered around 24,180 (body), NOT 24,350 (wick)
    assert len(res.resistance_levels) > 0
    assert len(res.support_levels) > 0
    for r in res.resistance_levels:
        assert r < 24300.0  # Extreme wick was filtered out!
    assert res.nearest_resistance is not None
    assert res.nearest_support is not None


def test_breakout_channels():
    """AC-1.5: Breakout channels detect consolidation and 50% body breakout."""
    # 15 bars inside narrow range [100, 105], then strong bar closing at 115 with open at 104
    close = np.array([101.0, 102.0, 103.0, 102.5, 104.0, 103.5, 104.5, 103.0, 104.0, 103.8,
                      104.2, 103.9, 104.5, 104.1, 115.0])
    open_ = np.array([101.0, 101.5, 102.5, 103.0, 103.0, 103.8, 103.2, 103.5, 103.2, 104.0,
                      103.5, 104.0, 103.8, 104.0, 104.0])
    high = np.maximum(open_, close) + 0.5
    low = np.minimum(open_, close) - 0.5

    ch = structure.breakout_channels(open_, high, low, close, box_len=10)
    assert ch.breakout_up is True
    assert ch.breakout_down is False


# ─────────────────────────────────────────────────────────────────────────────
# 5. ORDER FLOW & PRESSURE (AC-1.6)
# ─────────────────────────────────────────────────────────────────────────────

def test_buyer_seller_pressure():
    """AC-1.6: Buyer % + Seller % must strictly equal 100%, and detect buyer dominance."""
    # Strong green candles closing at high
    open_ = np.array([100.0, 102.0, 104.0, 106.0, 108.0])
    close = np.array([102.0, 104.0, 106.0, 108.0, 110.0])
    high = close  # Closes at exact high
    low = open_
    volume = np.array([1000, 1500, 2000, 2500, 3000])

    res = order_flow.buyer_seller_pressure(open_, high, low, close, volume, lookback=5)

    # Closes at high -> Buyer volume should be 100%
    assert np.isclose(res.buyer_pct + res.seller_pct, 100.0)
    assert res.buyer_pct == 100.0
    assert res.dominant_side == "BUYER"

    # Volume profile
    vp = order_flow.volume_profile(close, volume, num_bins=5)
    assert vp.poc > 0.0
    assert vp.val <= vp.poc <= vp.vah


# ─────────────────────────────────────────────────────────────────────────────
# 6. OPTIONS GREEKS & ANALYTICS (AC-1.7)
# ─────────────────────────────────────────────────────────────────────────────

def test_black_scholes_greeks():
    """AC-1.7: Black-Scholes Greeks, Put/Call parity, and IV solver."""
    spot = 24200.0
    strike = 24200.0  # ATM
    t_years = 3.0 / 365.0  # 3 days to expiry
    r = 0.07  # 7% interest rate
    iv = 0.15  # 15% IV

    call = options.black_scholes_greeks(spot, strike, t_years, r, iv, "CE")
    put = options.black_scholes_greeks(spot, strike, t_years, r, iv, "PE")

    # ATM Call Delta should be approximately 0.50
    assert 0.45 <= call.delta <= 0.55
    # Put Delta = Call Delta - 1
    assert np.isclose(put.delta, call.delta - 1.0, atol=0.01)
    # Gamma should be positive and equal for call and put
    assert call.gamma > 0.0
    assert np.isclose(call.gamma, put.gamma, atol=1e-5)
    # Theta should be negative (decaying)
    assert call.theta < 0.0
    assert put.theta < 0.0

    # Test IV Solver
    recovered_iv = options.implied_volatility(call.price, spot, strike, t_years, r, "CE")
    assert np.isclose(recovered_iv, iv, atol=0.005)

    # Test Straddle
    straddle, upper, lower = options.atm_straddle(call.price, put.price, strike)
    assert straddle == round(call.price + put.price, 2)
    assert upper == strike + straddle
    assert lower == strike - straddle


# ─────────────────────────────────────────────────────────────────────────────
# 7. SPEED & LATENCY BENCHMARKS (AC-1.8, AC-1.9)
# ─────────────────────────────────────────────────────────────────────────────

def test_incremental_execution_speed():
    """AC-1.8: Sub-15 microsecond per-tick calculation benchmark."""
    # Pre-warm JIT functions
    ta.ema_step(24100.0, 24105.0, 21)
    ta.rsi_step(1.5, 0.8, 24105.0, 24100.0, 14)
    ta.supertrend_step(1, 24120.0, 24080.0, 24100.0, 24110.0, 24090.0, 24105.0, 15.0, 3.0)

    # State variables
    e21 = 24100.0
    e50 = 24080.0
    avg_gain = 1.2
    avg_loss = 0.9
    st_dir = 1
    st_upper = 24150.0
    st_lower = 24050.0
    st_line = 24050.0
    prev_close = 24100.0
    atr_val = 14.5

    # Benchmark 10,000 live tick updates
    num_ticks = 10000
    start = time.perf_counter()
    for i in range(num_ticks):
        ltp = 24100.0 + (i % 20) * 0.5
        # Incremental tick calculations
        e21 = ta.ema_step(e21, ltp, 21)
        e50 = ta.ema_step(e50, ltp, 50)
        rsi_val, avg_gain, avg_loss = ta.rsi_step(avg_gain, avg_loss, ltp, prev_close, 14)
        st_dir, st_line, st_upper, st_lower = ta.supertrend_step(
            st_dir, st_upper, st_lower, prev_close, ltp + 1.0, ltp - 1.0, ltp, atr_val, 3.0
        )
        prev_close = ltp
    elapsed = time.perf_counter() - start

    avg_latency_us = (elapsed / num_ticks) * 1e6
    print(f"\n[BENCHMARK] Average Live Tick Calculation Latency: {avg_latency_us:.3f} µs")
    # Must be <= 15 microseconds per tick (typically ~0.2 µs!)
    assert avg_latency_us <= 15.0, f"Latency {avg_latency_us:.2f}µs exceeded 15µs limit!"


def test_vectorized_warmup_speed():
    """AC-1.9: Evaluating 1,000 historical bars completes in <= 5 milliseconds."""
    n = 1000
    prices = np.random.normal(24000.0, 50.0, n)
    high = prices + 5.0
    low = prices - 5.0
    vol = np.full(n, 1000.0)

    start = time.perf_counter()
    ta.ema(prices, 21)
    ta.ema(prices, 50)
    ta.rsi(prices, 14)
    ta.supertrend(high, low, prices, 10, 3.0)
    structure.dynamic_sr(prices, high, low, prices)
    order_flow.buyer_seller_pressure(prices, high, low, prices, vol, 14)
    elapsed_ms = (time.perf_counter() - start) * 1000.0

    print(f"\n[BENCHMARK] 1,000 Historical Bars Warmup Time: {elapsed_ms:.2f} ms")
    assert elapsed_ms <= 5.0, f"Warmup {elapsed_ms:.2f}ms exceeded 5.0ms limit!"
