# BUG 9: Organic Intraday Market Dynamics (Eliminating Staircase Artifacts)

> **Status:** Ready for Implementation  
> **Severity:** High (Simulation Realism)  
> **Affected Files:** `trading_engine/web/studio_router.py`

---

## 1. Problem Description
On `2026-10-30`, the market was volatile, moving up and down with genuine swings, pullbacks, and breakouts. However, the chart rendered candles in a monotonic, linear "staircase" ramp without natural market noise, consolidations, or realistic swing structure.

## 2. Root Cause Analysis
In `_generate_synthetic_candles`, the price generator applied a fixed linear drift:
```python
drift = 1.8 if i < 150 else -0.8
spot_cur += drift + float(np.random.normal(0, 1.2))
opt_cur += (drift * 0.5) + float(np.random.normal(0, 0.6))
```
A static upward drift of `+1.8` points across 150 consecutive bars forces 150 consecutive green candles without intermediate swing highs, swing lows, or pullbacks, resulting in a continuous diagonal staircase.

## 3. Solution Architecture & Technical Changes
1. **Multi-Wave Intraday Structural Generator (`studio_router.py`):**
   - Model the 375-minute intraday session with 3 distinct regimes:
     1. **Opening Session (09:15 - 10:30):** High-volatility price discovery with wide candle ranges and opening range establishment.
     2. **Mid-Day Session (10:30 - 13:30):** Mean-reverting consolidation with swing highs, swing lows, and false breakouts.
     3. **Afternoon Session (13:30 - 15:30):** Trend expansion with institutional momentum bursts, pullbacks to moving averages, and dynamic closes.
   - Use multi-frequency sinusoids + mean-reverting random walk:
     ```python
     macro_wave = np.sin(np.linspace(0, 3 * np.pi, n_bars)) * 120.0
     micro_wave = np.sin(np.linspace(0, 12 * np.pi, n_bars)) * 25.0
     intraday_path = spot_base + macro_wave + micro_wave + np.cumsum(np.random.normal(0, 2.5, n_bars))
     ```
   - Option strike prices must scale dynamically with intrinsic delta and implied volatility decay.
   - Candlestick OHLC must feature authentic wicks (upper and lower shadows) and variable body sizes, with realistic volume spikes on swing reversals.

## 4. Acceptance Criteria
- [ ] Candlesticks feature natural two-way market action (red and green bars alternating naturally with realistic trends and consolidations).
- [ ] No monotonic staircase ramps.
- [ ] Indicator calculations (EMAs, Supertrend, Breakout channels) react cleanly to real swing structure.
