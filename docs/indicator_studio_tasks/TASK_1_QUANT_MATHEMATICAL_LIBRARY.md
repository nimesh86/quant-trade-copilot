# Task 1: Quant Mathematical Library (`quant_lib`)
## Detailed Specification, Architecture & Acceptance Criteria

> **Document Version:** 1.0.0  
> **Status:** Ready for Implementation  
> **Target Subsystem:** `trading_engine/quant_lib/`  
> **Test Suite:** `tests/test_quant_lib.py`

---

## 1. Objective & Architectural Scope

The goal of Task 1 is to build an institutional-grade, pre-defined quantitative mathematical library (`quant_lib`) in pure Python/NumPy/Numba. 

This library serves as the mathematical foundation for all user indicators. It eliminates manual formula errors, guarantees numerical accuracy against industry benchmarks (TradingView), and provides dual execution:
1. **Vectorized Batch Mode:** For ultra-fast historical warm-up (1,000 bars in $< 5$ milliseconds).
2. **Incremental Streaming Mode:** For sub-microsecond single-tick updates on live WebSocket data ($< 15\mu s$ per tick).

---

## 2. Decoupled, Scalable Backend Design Principles

1. **Pure Functional & Stateless Math:**
   All core calculations in `ta.py`, `structure.py`, `order_flow.py`, and `options.py` are pure numerical functions operating on NumPy arrays or scalar primitives. They do NOT depend on external server state, database connections, or network sockets.
2. **Zero Garbage Collection Overhead in Live Ticks:**
   Streaming updates reuse pre-allocated circular buffers (`RingBuffer`). No large arrays or dictionaries are allocated on each incoming tick.
3. **Hardware Acceleration:**
   Recurrence loops (EMA, RMA, Supertrend) use Numba `@njit(fastmath=True, cache=True)` to execute in raw machine code via LLVM.
4. **Pine-Identical Naming Convention:**
   Function names match standard quantitative conventions (`ta.ema`, `ta.rsi`, `ta.dynamic_sr`) so traders familiar with TradingView or standard quant books have zero friction.

---

## 3. Complete Function & Class Inventory

### 3.1 Base Architecture (`quant_lib/base.py`)

```python
from dataclasses import dataclass, field
from typing import Dict, Any, Optional, List, Union
from datetime import datetime
import numpy as np
import pandas as pd

@dataclass
class IndicatorSignal:
    action: str                 # "BUY_CE", "BUY_PE", "EXIT_CE", "EXIT_PE", "HOLD"
    trigger_price: float        # Price at which signal fired
    stop_loss: float = 0.0      # Invalidation level
    target: float = 0.0         # Primary profit target
    target_2: float = 0.0       # Secondary profit target
    risk_points: float = 0.0    # abs(trigger_price - stop_loss)
    reward_points: float = 0.0  # abs(target - trigger_price)
    rr_ratio: float = 0.0       # reward_points / risk_points
    confidence: float = 1.0     # 0.0 to 1.0 score
    tag: str = "SIGNAL"         # e.g. "BSS_BREAKOUT", "PULLBACK_EMA21"
    timestamp: str = ""
    extra: Dict[str, Any] = field(default_factory=dict)

class InputDescriptor:
    def __init__(self, default: Any, title: str, input_type: str, minval=None, maxval=None, step=None, options=None, group="General", tooltip=""):
        self.default = default
        self.title = title
        self.input_type = input_type
        self.minval = minval
        self.maxval = maxval
        self.step = step
        self.options = options or []
        self.group = group
        self.tooltip = tooltip
        self.value = default

class input:
    @staticmethod
    def int(default: int, title: str, minval: int = 1, maxval: int = 10000, step: int = 1, group="General", tooltip="") -> int:
        return InputDescriptor(default, title, "int", minval=minval, maxval=maxval, step=step, group=group, tooltip=tooltip)

    @staticmethod
    def float(default: float, title: str, minval: float = 0.0, maxval: float = 10000.0, step: float = 0.1, group="General", tooltip="") -> float:
        return InputDescriptor(default, title, "float", minval=minval, maxval=maxval, step=step, group=group, tooltip=tooltip)

    @staticmethod
    def bool(default: bool, title: str, group="General", tooltip="") -> bool:
        return InputDescriptor(default, title, "bool", group=group, tooltip=tooltip)

    @staticmethod
    def string(default: str, title: str, options: List[str] = None, group="General", tooltip="") -> str:
        return InputDescriptor(default, title, "string", options=options, group=group, tooltip=tooltip)

    @staticmethod
    def color(default: str, title: str, group="Appearance", tooltip="") -> str:
        return InputDescriptor(default, title, "color", group=group, tooltip=tooltip)

class Indicator:
    def __init__(self, **kwargs):
        self.inputs = {}
        self.init()

    def init(self):
        """Override to declare input parameters."""
        pass

    def warm_up(self, historical_bars: pd.DataFrame):
        """Executes vectorized warmup across past candles."""
        pass

    def on_bar(self, bar: Dict[str, Any], history: Any) -> Optional[IndicatorSignal]:
        """Called upon candle close (or historical replay step)."""
        pass

    def on_tick(self, ltp: float, volume: int, timestamp: str) -> Optional[IndicatorSignal]:
        """Called upon sub-second live tick update."""
        pass

    def signal(self, action: str, trigger_price: float, stop_loss: float = 0.0, target: float = 0.0, **kwargs) -> IndicatorSignal:
        return IndicatorSignal(action=action, trigger_price=trigger_price, stop_loss=stop_loss, target=target, **kwargs)
```

---

### 3.2 Technical Analysis Mathematical Kernels (`quant_lib/ta.py`)

All formulas must support both **1D NumPy array inputs (vectorized)** and **scalar streaming updates**.

#### 1. Moving Averages
* `ta.sma(series: np.ndarray, length: int) -> np.ndarray`: Simple moving average.
* `ta.ema(series: np.ndarray, length: int, alpha: Optional[float] = None) -> np.ndarray`: Exponential moving average using recurrence $EMA_t = \alpha P_t + (1-\alpha)EMA_{t-1}$.
* `ta.rma(series: np.ndarray, length: int) -> np.ndarray`: Running moving average (Wilder's smoothing with $\alpha = 1/\text{length}$).
* `ta.wma(series: np.ndarray, length: int) -> np.ndarray`: Weighted moving average with weights $1, 2, \dots, length$.
* `ta.hma(series: np.ndarray, length: int) -> np.ndarray`: Hull moving average: $WMA\left(2\times WMA(n/2) - WMA(n), \sqrt{n}\right)$.
* `ta.vwma(series: np.ndarray, volume: np.ndarray, length: int) -> np.ndarray`: Volume-weighted moving average: $\frac{\sum P_i V_i}{\sum V_i}$.
* `ta.dema(series: np.ndarray, length: int) -> np.ndarray`: Double EMA: $2\times EMA(n) - EMA(EMA(n), n)$.
* `ta.tema(series: np.ndarray, length: int) -> np.ndarray`: Triple EMA: $3\times EMA - 3\times EMA(EMA) + EMA(EMA(EMA))$.
* `ta.alma(series: np.ndarray, length: int = 9, offset: float = 0.85, sigma: float = 6.0) -> np.ndarray`: Arnaud Legoux Moving Average using Gaussian distribution.
* `ta.vwap(high: np.ndarray, low: np.ndarray, close: np.ndarray, volume: np.ndarray, session_breaks: np.ndarray) -> np.ndarray`: Intraday VWAP reset at 09:15 IST daily.

#### 2. Oscillators & Momentum
* `ta.rsi(series: np.ndarray, length: int = 14) -> np.ndarray`: Relative Strength Index using Wilder's RMA method.
* `ta.macd(series: np.ndarray, fast: int = 12, slow: int = 26, signal: int = 9) -> Tuple[np.ndarray, np.ndarray, np.ndarray]`: Returns `(macd_line, signal_line, histogram)`.
* `ta.stoch(close: np.ndarray, high: np.ndarray, low: np.ndarray, k_len: int = 14, d_len: int = 3, smooth_k: int = 3) -> Tuple[np.ndarray, np.ndarray]`: Fast & Slow Stochastic.
* `ta.stoch_rsi(series: np.ndarray, rsi_len: int = 14, stoch_len: int = 14, k_len: int = 3, d_len: int = 3) -> Tuple[np.ndarray, np.ndarray]`: Stochastic RSI.
* `ta.cci(high: np.ndarray, low: np.ndarray, close: np.ndarray, length: int = 20) -> np.ndarray`: Commodity Channel Index.
* `ta.mfi(high: np.ndarray, low: np.ndarray, close: np.ndarray, volume: np.ndarray, length: int = 14) -> np.ndarray`: Money Flow Index.
* `ta.roc(series: np.ndarray, length: int = 9) -> np.ndarray`: Rate of Change (% momentum).
* `ta.crossover(series_a: np.ndarray, series_b: Union[np.ndarray, float]) -> np.ndarray`: Boolean series where $A_{t-1} \le B_{t-1}$ and $A_t > B_t$.
* `ta.crossunder(series_a: np.ndarray, series_b: Union[np.ndarray, float]) -> np.ndarray`: Boolean series where $A_{t-1} \ge B_{t-1}$ and $A_t < B_t$.

#### 3. Volatility & Trend Channels
* `ta.true_range(high: np.ndarray, low: np.ndarray, close: np.ndarray) -> np.ndarray`: $\max(H-L, |H-C_{t-1}|, |L-C_{t-1}|)$.
* `ta.atr(high: np.ndarray, low: np.ndarray, close: np.ndarray, length: int = 14) -> np.ndarray`: Wilder's RMA of True Range.
* `ta.supertrend(high: np.ndarray, low: np.ndarray, close: np.ndarray, length: int = 10, multiplier: float = 3.0) -> Tuple[np.ndarray, np.ndarray]`: Returns `(trend_direction, supertrend_line)`. Trend direction is $+1$ (Bullish) or $-1$ (Bearish), flipping strictly on confirmed close.
* `ta.bollinger_bands(series: np.ndarray, length: int = 20, mult: float = 2.0) -> Tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]`: Returns `(upper_band, midline, lower_band, percent_b)`.
* `ta.keltner_channels(high: np.ndarray, low: np.ndarray, close: np.ndarray, length: int = 20, mult: float = 1.5) -> Tuple[np.ndarray, np.ndarray, np.ndarray]`: EMA center with ATR envelope.
* `ta.donchian_channels(high: np.ndarray, low: np.ndarray, length: int = 20) -> Tuple[np.ndarray, np.ndarray, np.ndarray]`: Highest high, lowest low, and midline over lookback.

---

### 3.3 Market Structure & Dynamic Support/Resistance (`quant_lib/structure.py`)

* `ta.pivot_high(high: np.ndarray, left: int = 4, right: int = 4) -> np.ndarray`: Peak where $High_t > High_{t-k}$ for all $k \in [-left, right], k \neq 0$.
* `ta.pivot_low(low: np.ndarray, left: int = 4, right: int = 4) -> np.ndarray`: Trough where $Low_t < Low_{t-k}$ for all $k \in [-left, right], k \neq 0$.
* `ta.dynamic_sr(open: np.ndarray, high: np.ndarray, low: np.ndarray, close: np.ndarray, left: int = 4, right: int = 4, max_levels: int = 5, cluster_threshold_atr: float = 0.3) -> DynamicSRResult`:
  - **Candle Body Clustering Rule:** Detects swing highs using $\max(Open, Close)$ and swing lows using $\min(Open, Close)$ to ignore wick sweeps.
  - Groups price levels within $\text{threshold} = \text{cluster_threshold_atr} \times ATR$ into unified horizontal support and resistance lines.
  - Returns `DynamicSRResult` containing:
    - `resistance_levels`: List of float price barriers.
    - `support_levels`: List of float price floors.
    - `nearest_resistance`: Closest resistance above current price.
    - `nearest_support`: Closest support below current price.
    - `resistance_distance_pts`: Current price distance to nearest resistance.
    - `support_distance_pts`: Current price distance to nearest support.
* `ta.breakout_channels(open: np.ndarray, high: np.ndarray, low: np.ndarray, close: np.ndarray, volume: np.ndarray, norm_len: int = 100, box_len: int = 14, min_duration: int = 10) -> BreakoutChannelResult`:
  - Normalizes price range over `norm_len` bars.
  - Detects consolidation box boundaries (`top`, `bottom`, `midline`).
  - Emits `breakout_up = True` when $> 50\%$ of the candle body closes above `top`.
  - Emits `breakout_down = True` when $> 50\%$ of the candle body closes below `bottom`.

---

### 3.4 Order Flow, Volume & Buyer/Seller Pressure (`quant_lib/order_flow.py`)

* `ta.buyer_seller_pressure(open: np.ndarray, high: np.ndarray, low: np.ndarray, close: np.ndarray, volume: np.ndarray, lookback: int = 14) -> BuyerSellerPressureResult`:
  - Intra-candle volume allocation formula:
    $$\text{Body Span} = High - Low$$
    $$\text{Buyer Ratio} = \begin{cases} \frac{Close - Low}{High - Low} & \text{if } High > Low \\ 0.5 & \text{if } High == Low \end{cases}$$
    $$\text{Seller Ratio} = 1.0 - \text{Buyer Ratio}$$
    $$\text{Buyer Volume} = Volume \times \text{Buyer Ratio}$$
    $$\text{Seller Volume} = Volume \times \text{Seller Ratio}$$
  - Computes rolling sum over `lookback` bars:
    $$\text{Buyer \%} = \frac{\sum \text{Buyer Vol}}{\sum \text{Total Vol}} \times 100$$
    $$\text{Seller \%} = \frac{\sum \text{Seller Vol}}{\sum \text{Total Vol}} \times 100$$
  - Returns `BuyerSellerPressureResult(buyer_pct, seller_pct, dominant_side, buyer_volume_series, seller_volume_series)`.
* `ta.volume_profile(close: np.ndarray, volume: np.ndarray, num_bins: int = 24) -> VolumeProfileResult`:
  - Bins volume by price intervals across the dataset.
  - Computes:
    - **Point of Control (POC):** Price level with the single largest traded volume.
    - **Value Area High (VAH):** Upper bound containing 70% of total volume around POC.
    - **Value Area Low (VAL):** Lower bound containing 70% of total volume around POC.
* `ta.cumulative_volume_delta(ticks_or_candles: List[Dict[str, Any]]) -> np.ndarray`:
  - Accumulates $(\text{Market Buy Volume} - \text{Market Sell Volume})$ continuously.

---

### 3.5 Options Analytics & Greeks (`quant_lib/options.py`)

* `opt.black_scholes_greeks(spot: float, strike: float, time_to_expiry_years: float, risk_free_rate: float, iv: float, option_type: str) -> GreeksResult`:
  - Analytical Black-Scholes-Merton model calculating:
    - `price`: Theoretical option price.
    - `delta`: Rate of change of option price with respect to spot ($\Delta = \mathcal{N}(d_1)$ for Call, $\mathcal{N}(d_1) - 1$ for Put).
    - `gamma`: Rate of change of Delta ($\Gamma = \frac{\mathcal{N}'(d_1)}{S \sigma \sqrt{T}}$).
    - `theta`: Time decay per calendar day ($\Theta$).
    - `vega`: Sensitivity to 1% change in implied volatility ($\mathcal{V}$).
    - `rho`: Sensitivity to interest rates ($\rho$).
* `opt.implied_volatility(market_price: float, spot: float, strike: float, time_to_expiry_years: float, risk_free_rate: float, option_type: str) -> float`:
  - High-precision Brent / Newton-Raphson numerical solver returning annualized IV (e.g. 0.155 for 15.5%).
* `opt.atm_straddle(ce_price: float, pe_price: float) -> Tuple[float, float, float]`:
  - Computes combined straddle premium: $\text{Straddle} = CE + PE$.
  - Upper breakeven: $\text{Strike} + \text{Straddle}$.
  - Lower breakeven: $\text{Strike} - \text{Straddle}$.
* `opt.put_call_ratio(pe_oi_or_vol: np.ndarray, ce_oi_or_vol: np.ndarray) -> float`:
  - $PCR = \frac{\sum PE}{\sum CE}$.

---

## 4. Impacted Areas in Existing Codebase

* **New Package:** `trading_engine/quant_lib/` (Zero disruption to existing live code).
* **Base Strategy Compatibility:** `quant_lib.base.IndicatorSignal` seamlessly converts to `trading_engine.strategy.base.StrategySignal` so any indicator can be plugged into `contract_engine.py` or `StrategyDetectorAdapter`.

---

## 5. Concrete Acceptance Criteria & Verification Matrix

| AC ID | Requirement | Metric / Tolerance | Test Function |
| :--- | :--- | :--- | :--- |
| **AC-1.1** | Moving Averages Precision (`ema`, `sma`, `rma`, `wma`, `hma`, `vwma`, `dema`, `tema`, `alma`, `vwap`) | Max absolute error $\le 1\times 10^{-4}$ vs. verified reference arrays. | `test_moving_averages_precision()` |
| **AC-1.2** | Oscillators Precision (`rsi`, `macd`, `stoch`, `cci`, `mfi`, `roc`) | RSI on standard test data matches Wilder's RMA within $0.01$ pts. MACD line and signal match within $1\times 10^{-4}$. | `test_oscillators_precision()` |
| **AC-1.3** | Volatility & Supertrend | Supertrend direction flips strictly when candle close crosses previous trailing line. Direction is $+1$ or $-1$. | `test_supertrend_reversal_logic()` |
| **AC-1.4** | Dynamic S/R Body Clustering | Must ignore extreme wick sweeps and cluster levels based on $\max(O,C)$ and $\min(O,C)$. Correctly identifies nearest support/resistance. | `test_dynamic_sr_body_clustering()` |
| **AC-1.5** | Breakout Channels | Accurately flags breakout when body $>50\%$ outside channel. | `test_breakout_channels()` |
| **AC-1.6** | Buyer/Seller Pressure | Buyer % + Seller % must strictly equal $100.0\% \pm 0.001\%$. Buyer % $>50\%$ when closes are in upper half of ranges. | `test_buyer_seller_pressure()` |
| **AC-1.7** | Black-Scholes Greeks | ATM Call Delta must be $0.50 \pm 0.04$ for $T=3\text{ days}, IV=15\%$. Put Delta must be $\text{Call Delta} - 1$. | `test_black_scholes_greeks()` |
| **AC-1.8** | Incremental Streaming Speed | Single-step tick update executing 2 EMAs, 1 RSI, 1 Supertrend, and 1 S/R lookup completes in **$\le 15$ microseconds**. | `test_incremental_execution_speed()` |
| **AC-1.9** | Vectorized Warm-Up Speed | Evaluating 1,000 historical bars across the entire indicator suite completes in **$\le 5$ milliseconds**. | `test_vectorized_warmup_speed()` |

---

## 6. Comprehensive Automated Test Suite (`tests/test_quant_lib.py`)

The test suite must implement:
1. `test_moving_averages_precision()`: Tests all 10 moving average variations against deterministic synthetic price series.
2. `test_oscillators_precision()`: Validates RSI, MACD, and Stochastic boundary behaviors (0 to 100).
3. `test_crossover_crossunder()`: Validates exact edge-triggered boolean flags without multi-bar persistence.
4. `test_supertrend_reversal_logic()`: Verifies trailing stop preservation during trends and direction flip on invalidation.
5. `test_dynamic_sr_body_clustering()`: Tests pivot detection and body clustering with noisy wick data.
6. `test_breakout_channels()`: Validates consolidation box detection and 50% body breakout confirmation.
7. `test_buyer_seller_pressure()`: Tests pressure ratio under pure bullish, pure bearish, and doji candles.
8. `test_black_scholes_greeks()`: Validates Delta, Gamma, Theta, Vega against standard textbook Black-Scholes benchmarks.
9. `test_benchmark_latency()`: Micro-benchmark executing 10,000 live ticks, asserting 99th percentile latency $\le 15\mu s$.

---

## 7. PREREQUISITES & HAND-OFF CONTRACT FOR NEXT TASK (TASK 2)

### 7.1 Prerequisites Received from Task 0:
* Verified directory `trading_engine/quant_lib/` with base class `Indicator` and `InputDescriptor` in `base.py`.
* Verified NumPy, SciPy, and Numba development dependencies.

### 7.2 Hand-Off Deliverables to Task 2 (Dynamic Runtime):
Task 1 completes and hands off the following assets:
* ✅ **Tested Mathematical Modules:** `quant_lib.ta`, `quant_lib.structure`, `quant_lib.order_flow`, and `quant_lib.options`.
* ✅ **Clean Namespace Export:** `trading_engine/quant_lib/__init__.py` exposes all indicators, math functions, and inputs so user scripts can write `from quant_lib import Indicator, ta, input`.
* ✅ **Dual-Mode Capability:** Functions accept both historical arrays (for Task 2's `WarmupManager`) and single-step scalars (for Task 2's live tick execution).
* ✅ **Performance Verification:** Passing `tests/test_quant_lib.py` guarantees $< 15\mu s$ execution latency before the runtime engine wraps it.

