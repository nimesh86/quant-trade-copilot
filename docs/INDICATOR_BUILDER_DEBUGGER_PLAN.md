# Quant Studio: Indicator Builder & Real-Time Debugger
## Comprehensive Technical Specification & Task Acceptance Criteria

> **Document Version:** 2.0.0 (Master Execution Blueprint)  
> **Target System:** Local Split-Screen Quant Studio with Sub-20µs Backend Execution  
> **Deployment Topology:** Decoupled Architecture (Local Studio ➔ VPS Master Data Hub)  
> **Context Preservation:** Authoritative specification for all implementation phases and automated tests

---

## 1. System Architecture Overview & Invariants

```
┌─────────────────────────────────────────────────────────────────────────────────────────┐
│ PRODUCTION VPS (Headless Master Data Hub & Execution Engine)                            │
│ • Live Kite / Breeze WebSocket Tick Streams                                             │
│ • Historical Candle & Tick Store (Postgres / TimescaleDB)                               │
│ • Gateway Service: HistoricalDataAPI (REST) + LiveFeedSocket (WebSocket Pass-Through)   │
└────────────────────────────────────────────┬────────────────────────────────────────────┘
                                             │ Secure HTTPS / WSS
                                             ▼
┌─────────────────────────────────────────────────────────────────────────────────────────┐
│ LOCAL QUANT STUDIO (Your Mac / Local Machine)                                           │
│ ┌─────────────────────────────────────────────────────────────────────────────────────┐ │
│ │ TOP PANEL: High-Performance Canvas Chart (Lightweight Charts)                       │ │
│ │ • Synchronized Dual View: Spot Index (NIFTY/BANKNIFTY) + Option Contract (CE / PE)  │ │
│ │ • Indicator Overlays, Dynamic S/R Levels, Breakout Channels, and Signal Markers     │ │
│ │ • Replay Controls (Scrubber, Play/Pause, Step +1, Break on Signal)                  │ │
│ ├─────────────────────────────────────────────────────────────────────────────────────┤ │
│ │ BOTTOM PANEL: Monaco Python Studio & Debugger                                       │ │
│ │ • Standardized Indicator Template (Pure Native Python 3.10+)                        │ │
│ │ • Pre-Defined Quant Mathematical Library (quant_lib)                                │ │
│ │ • Real-Time Variable Watch Table & Diagnostics Console                              │ │
│ └─────────────────────────────────────────────────────────────────────────────────────┘ │
│                                            │                                            │
│                      ┌─────────────────────┴─────────────────────┐                      │
│                      ▼                                           ▼                      │
│   ┌─────────────────────────────────────┐     ┌─────────────────────────────────────┐   │
│   │ Local Storage Layer (DuckDB)        │     │ Backend Execution Kernel (In-Memory)│   │
│   │ • Spot + Option Candle Parquet cache│     │ • Microsecond (< 15µs) tick runtime │   │
│   │ • Offline 24/7 Replay & Backtest    │     │ • Zero-downtime hot-swap (< 1ms)    │   │
│   └─────────────────────────────────────┘     └─────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────────────────────────┘
```

### Core System Invariants (Must NEVER be violated):
1. **Zero UI Latency:** Indicator logic executes entirely on the backend in RAM. Browser only renders visual coordinates streamed from the backend.
2. **Zero Lookahead Bias:** In backtest and replay modes, the indicator must strictly only access historical bars up to the active timestamp $T$.
3. **Decoupled Safety:** The local studio never connects directly to broker APIs (saving rate limits and enabling 24/7 off-hours testing); it interfaces exclusively with the VPS Data Gateway and local DuckDB cache.
4. **Plug-and-Play Contract:** Every compiled indicator must adhere to the standard `BaseIndicator` interface, allowing hot-swapping into live tick feeds in $< 1\text{ms}$ with zero server restart.

---

## 2. Granular Task Breakdown & Acceptance Criteria

---

### TASK 1: Quant Mathematical Library (`quant_lib`)

#### 1. Scope & Deliverable Files
* `trading_engine/quant_lib/__init__.py`: Package entrypoint exporting clean namespace.
* `trading_engine/quant_lib/base.py`: `Indicator` base class, `Signal` dataclass, and `input` descriptors.
* `trading_engine/quant_lib/ta.py`: Pre-defined mathematical kernels (Moving averages, oscillators, volatility).
* `trading_engine/quant_lib/structure.py`: Market structure (dynamic S/R pivot clustering, breakout boxes).
* `trading_engine/quant_lib/order_flow.py`: Buyer/Seller strength volume pressure & cumulative delta.
* `trading_engine/quant_lib/options.py`: Black-Scholes Greeks ($\Delta, \Gamma, \Theta, \mathcal{V}$) & IV solver.
* `tests/test_quant_lib.py`: Comprehensive test suite.

#### 2. Input / Output Data Contracts
* **Vectorized Input:** `pd.Series` or 1D `np.ndarray` of floats (historical warm-up).
* **Streaming Input:** Incremental floats (`ltp`, `volume`, `timestamp`) for live tick / bar evaluation.
* **Output:** Pre-allocated NumPy arrays (vectorized) or single scalar values / tuple of scalars (streaming).

#### 3. Detailed Acceptance Criteria
* [ ] **AC-1.1 (Moving Averages):** 
  - `ta.ema()`, `ta.sma()`, `ta.rma()`, `ta.wma()`, `ta.hma()`, `ta.vwma()`.
  - Recurrence: $EMA_t = \alpha P_t + (1-\alpha)EMA_{t-1}$ where $\alpha = \frac{2}{length + 1}$.
  - Numerical precision: Within $1\times 10^{-5}$ of TradingView standard outputs.
* [ ] **AC-1.2 (Oscillators & Momentum):**
  - `ta.rsi(series, length=14)`: Uses Wilder's smoothing (`ta.rma`) for upward and downward price changes.
  - `ta.macd(series, fast=12, slow=26, signal=9)`: Returns `(macd_line, signal_line, histogram)`.
  - `ta.crossover(series_a, series_b)`: Returns `True` iff $a_{t-1} \le b_{t-1}$ and $a_t > b_t$.
  - `ta.crossunder(series_a, series_b)`: Returns `True` iff $a_{t-1} \ge b_{t-1}$ and $a_t < b_t$.
* [ ] **AC-1.3 (Volatility & Trend):**
  - `ta.atr(high, low, close, length=14)`: True Range = $\max(H-L, |H-C_{t-1}|, |L-C_{t-1}|)$ smoothed with RMA.
  - `ta.supertrend(high, low, close, length=10, mult=3.0)`: Trailing stop band that flips direction strictly on confirmed close. Returns `(trend_direction, supertrend_line)`.
* [ ] **AC-1.4 (Dynamic S/R & Market Structure):**
  - `ta.dynamic_sr(high, low, close, left=4, right=4, max_levels=5)`:
    - Calculates fractal swing highs and lows.
    - Uses **candle bodies** ($\max(O, C)$ and $\min(O, C)$) rather than wicks to cluster levels and filter out wick sweeps.
    - Clusters pivot points within a threshold of $0.3\times \text{ATR}$ into a single unified support/resistance line.
    - Returns `(resistance_levels, support_levels, nearest_resistance, nearest_support)`.
* [ ] **AC-1.5 (Buyer/Seller Strength Pressure):**
  - `ta.buyer_seller_pressure(high, low, close, volume, lookback=14)`:
    - Intra-candle volume allocation:
      $$\text{Buyer Volume} = \text{Volume} \times \frac{\text{Close} - \text{Low}}{\text{High} - \text{Low}}$$
      $$\text{Seller Volume} = \text{Volume} \times \frac{\text{High} - \text{Close}}{\text{High} - \text{Low}}$$
    - Returns rolling `(buyer_pct, seller_pct, dominant_side)`.
* [ ] **AC-1.6 (Option Greeks):**
  - `opt.greeks(spot, strike, t_years, r, iv, opt_type)`: Analytical Black-Scholes returning Delta, Gamma, Theta, Vega.
  - Delta for ATM Call must equal $\approx 0.50 \pm 0.05$.
* [ ] **AC-1.7 (Execution Speed Benchmark):**
  - Single-step incremental tick calculation for a standard indicator setup (2 EMAs, 1 RSI, 1 Supertrend, 1 S/R) must execute in **$\le 15$ microseconds** per tick on macOS (Apple Silicon).
* [ ] **AC-1.8 (Dual-Execution Compatibility):**
  - Every formula must support both fast vectorized batch execution (for 1,000 historical warmup bars in $< 5\text{ms}$) and incremental single-step updates.

---

### TASK 2: Dynamic Python Runtime & Hot-Reload Engine

#### 1. Scope & Deliverable Files
* `trading_engine/runtime/compiler.py`: Code validator (`ast.parse()`), sandboxed execution, and class loader.
* `trading_engine/runtime/registry.py`: Thread-safe `DynamicIndicatorRegistry` with atomic swaps.
* `trading_engine/runtime/warmup.py`: Historical warm-up pipeline that primes rolling indicator states.
* `tests/test_indicator_runtime.py`: Unit and stress test suite.

#### 2. Input / Output Data Contracts
* **Input:** Raw Python source string submitted by the user.
* **Output:** Runnable instance of `Indicator` initialized with validated user parameters, or structured syntax/runtime error diagnostics with exact line numbers.

#### 3. Detailed Acceptance Criteria
* [ ] **AC-2.1 (Syntax Validation & Diagnostics):**
  - Uses `ast.parse()` to validate code before execution.
  - If syntax error exists, returns structured payload: `{valid: False, line: 14, column: 8, message: "SyntaxError: invalid syntax"}`.
  - Rejects dangerous built-ins (e.g. `os.system`, `subprocess`, file writing outside sandbox).
* [ ] **AC-2.2 (Dynamic Class Extraction):**
  - Discovers any class inheriting from `quant_lib.Indicator` in the module namespace.
  - Auto-extracts input parameters defined in `init()` (`input.int`, `input.float`, `input.bool`, etc.) into a JSON schema for UI rendering.
* [ ] **AC-2.3 (Historical Warm-Up):**
  - Accepts a historical DataFrame (e.g., 500 bars of Spot and Option data).
  - Iterates historical bars through `on_bar()` to prime all rolling EMAs, RSIs, and swing pivots.
  - Warm-up of 500 bars must complete in **$\le 10$ milliseconds**.
* [ ] **AC-2.4 (Atomic Thread-Safe Hot Swap):**
  - Replaces the active indicator instance in the live symbol tick bus under a thread-safe lock.
  - Swap latency must be **$< 1$ millisecond**.
  - No ticks dropped during the swap; the incoming tick immediately evaluates against the newly warmed-up instance.
* [ ] **AC-2.5 (Signal Propagation):**
  - When `on_bar()` or `on_tick()` returns a `Signal`, the runtime instantly validates the signal contract (`action`, `trigger_price`, `stop_loss`, `target`, `timestamp`) and routes it to the execution callback.

---

### TASK 3: Local Data Storage & VPS Data Gateway Client

#### 1. Scope & Deliverable Files
* `trading_engine/data/vps_client.py`: API-key authenticated client for `HistoricalDataAPI` and `LiveFeedSocket`.
* `trading_engine/data/duckdb_store.py`: Local DuckDB / Parquet embedded database manager.
* `trading_engine/data/sync_bus.py`: Synchronized timestamp alignment bus (Spot bar + Option bar).
* `tests/test_data_gateway.py`: Test suite with mock VPS server and real DuckDB queries.

#### 2. Input / Output Data Contracts
* **VPS Request:** `GET /api/v1/history/candles?symbol=NIFTY&strike=24200CE&from=2024-10-01&to=2024-10-25`
* **VPS Response:** Compressed JSON or Feather/Arrow buffer containing paired timestamps, Spot OHLCV, and Option OHLCV + Ticks.
* **Local Storage Schema (DuckDB):** Table `market_candles(timestamp TIMESTAMP, symbol VARCHAR, open DOUBLE, high DOUBLE, low DOUBLE, close DOUBLE, volume BIGINT, oi BIGINT, spot_close DOUBLE)`.

#### 3. Detailed Acceptance Criteria
* [ ] **AC-3.1 (VPS Gateway Authentication):**
  - All REST and WebSocket requests to the VPS pass `X-API-Key` or `?api_key=SECRET`.
  - Graceful connection retry with exponential backoff if VPS connection drops.
* [ ] **AC-3.2 (DuckDB High-Speed Local Caching):**
  - First-time query fetches from VPS and inserts into local DuckDB.
  - Second-time query for the same date range serves 100% locally from DuckDB with **zero network requests**.
  - Querying 1 month of 1-minute data (approx 7,500 bars) from local DuckDB must take **$\le 5$ milliseconds**.
* [ ] **AC-3.3 (Spot & Option Timestamp Synchronization):**
  - Synchronizes index spot bars and option strike bars on exact timestamps.
  - If an option bar has zero volume or missing tick, forward-fills previous close with zero volume without misaligning timestamps.
* [ ] **AC-3.4 (Off-Hours Offline Mode):**
  - If no internet or VPS is unreachable, the system automatically runs in Offline Mode, allowing full replay and backtesting from existing cached DuckDB data.
* [ ] **AC-3.5 (Live WebSocket Feed Pass-Through):**
  - Connects to VPS `LiveFeedSocket` during market hours.
  - Dispatches ticks directly into local memory bus with **$< 2$ms network transit latency**.

---

### TASK 4: Replay & Backtest Engine Core

#### 1. Scope & Deliverable Files
* `trading_engine/replay/session.py`: `ReplaySession` supporting step-forward, step-backward, scrub, and pause.
* `trading_engine/replay/backtester.py`: Vectorized macro-backtester computing performance metrics.
* `trading_engine/replay/breakpoints.py`: "Break on Signal" and user condition breakpoint evaluator.
* `tests/test_replay_engine.py`: Test suite validating replay mechanics and backtest metrics.

#### 2. Input / Output Data Contracts
* **Input:** Historical dataset from DuckDB, active `Indicator` instance, replay parameters (start time, speed).
* **Step Output:** `ReplayFrame` containing `{bar_index, timestamp, spot_bar, option_bar, indicator_plots, active_signals, variable_watch_snapshot}`.
* **Backtest Output:** `BacktestReport` containing `{win_rate, total_trades, profit_factor, max_drawdown_pct, net_pnl, trade_list}`.

#### 3. Detailed Acceptance Criteria
* [ ] **AC-4.1 (Zero Lookahead Replay Stream):**
  - At step $N$, the indicator history buffer contains exactly bars $0 \dots N$.
  - Attempting to access bar $N+1$ raises an `IndexError`.
* [ ] **AC-4.2 (Interactive Stepping Controls):**
  - `step_forward(1)`: Emits exactly 1 candle and updates all indicator states.
  - `step_backward(1)`: Restores indicator state to bar $N-1$ (via state snapshots or deterministic rewind).
  - `play(speed)`: Streams frames smoothly at 1x (1s/bar), 2x (0.5s/bar), 5x (0.2s/bar), 10x (0.1s/bar).
* [ ] **AC-4.3 (Break on Signal):**
  - When enabled, replay iterates through bars at maximum non-blocking speed and **automatically pauses** the instant any `Signal` is emitted.
  - Emits event `BREAKPOINT_HIT` with the triggering candle index and complete variable dump.
* [ ] **AC-4.4 (Fast Vectorized Backtester):**
  - Executes 1 full year of 1-minute candles (approx 75,000 bars) in **$\le 500$ milliseconds**.
  - Simulates entries, stop loss, profit targets, and trailing stops with realistic slippage (0.5 pts) and transaction costs.
  - Generates full trade ledger with entry/exit timestamps, prices, and PnL.

---

### TASK 5: Split-Screen Web Studio UI (Monaco + Canvas Chart)

#### 1. Scope & Deliverable Files
* `trading_engine/web/static/studio.html`: Full-screen responsive split-screen layout.
* `trading_engine/web/static/studio.css`: Modern high-contrast dark theme styling.
* `trading_engine/web/static/studio.js`: UI controller, Monaco editor setup, and WebSocket client.
* `trading_engine/web/static/studio_chart.js`: Lightweight Charts multi-pane renderer.
* `trading_engine/web/server.py`: API routes for compilation, mounting, replay control, and symbol data.

#### 2. Input / Output Data Contracts
* **UI Action:** User edits code in Monaco ➔ hits `Cmd+S` / `⚡ Mount` ➔ sends `POST /api/studio/mount`.
* **Server Event:** Server emits WebSocket message `REPLAY_TICK` or `INDICATOR_STATE` ➔ UI updates chart and watch table.

#### 3. Detailed Acceptance Criteria
* [ ] **AC-5.1 (Split-Screen Layout & Resizer):**
  - Vertical split with draggable splitter bar.
  - Persists panel heights to `localStorage`.
  - Responsive: Top chart and bottom editor auto-resize on window resize without distortion.
* [ ] **AC-5.2 (Top Panel Chart & Overlays):**
  - Integrates TradingView Lightweight Charts with dark theme.
  - Dual view toggle: Spot Index (NIFTY) or Option Strike (CE / PE).
  - Renders indicators as line overlays (EMAs, Supertrend), channel clouds (Breakout boxes), and sub-panes (RSI, Pressure gauge).
  - Displays signal markers: Green upward triangle for BUY CALL, Red downward triangle for BUY PUT.
* [ ] **AC-5.3 (Bottom Panel Monaco Editor):**
  - Embedded Monaco Editor with Python language configuration.
  - Full syntax highlighting, auto-indentation, and bracket matching.
  - Custom IntelliSense autocompletion for `quant_lib.*` (`ta.ema`, `ta.rsi`, `ta.dynamic_sr`, `self.signal`).
  - Real-time error markers (red squiggly lines) on syntax error with tooltip explanation.
* [ ] **AC-5.4 (Dynamic Inputs Configurator):**
  - Inspects `self.fast_ema_len = input.int(21, "Fast EMA")` and renders a clean slider/stepper toolbar.
  - Changing a value triggers an instant re-computation of the indicator without editing code.
* [ ] **AC-5.5 (Variable Watch Table & Cursor Hover):**
  - Live table showing variable values (`close`, `fast_ema`, `rsi`, `sr_resistance`, `dominant_side`).
  - Hovering crosshair over any candle on the top chart immediately syncs the table to show values for that specific bar.
* [ ] **AC-5.6 (Replay Toolbar):**
  - Play/Pause button, timeline slider scrubber, Speed selector (1x, 2x, 5x, 10x), Step Forward (+1), and "Break on Signal" toggle.

---

### TASK 6: Pre-Packaged Templates (BSS Pro v5 & AlgoAlpha Breakout)

#### 1. Scope & Deliverable Files
* `trading_engine/indicators/bss_pro_v5.py`: Production-grade native Python implementation of BSS Pro v5.
* `trading_engine/indicators/algoalpha_breakout.py`: Production-grade native Python implementation of Smart Money Breakout Channels.
* `tests/test_bss_pro_indicator.py`: Verification test suite against original TradingView behavior.

#### 2. Detailed Acceptance Criteria
* [ ] **AC-6.1 (BSS Pro v5 Feature Completeness):**
  - Dynamic pivot S/R calculated on candle bodies.
  - Fast EMA (21) & Slow EMA (50) trend filter with pullback touch zone.
  - Buyer/Seller volume pressure dominance filter ($> 58\%$).
  - Breakout channel detection with confirmation criteria (body outside channel, volume multiplier).
  - Stop Loss and Profit Target (T1, T2) calculation with dynamic trailing stop.
* [ ] **AC-6.2 (Template Preset Selector in IDE):**
  - A dropdown in Monaco studio allowing one-click loading of "BSS Pro v5" or "AlgoAlpha Breakout".
  - Code loads instantly with documentation comments and default parameters.
* [ ] **AC-6.3 (End-to-End Validation Run):**
  - Load `BSS_Pro_v5` in the local studio.
  - Run a 30-day replay on NIFTY CE/PE data.
  - Verify signals fire identically to expected strategy rules with zero crashes, zero memory leaks, and $< 15\mu s$ tick latency.

---

## 3. Verification & Sign-Off Matrix

| Task | Deliverable | Automated Test File | Sign-Off Criteria |
| :--- | :--- | :--- | :--- |
| **Task 1** | Quant Math Library | `tests/test_quant_lib.py` | 100% math accuracy vs TV, $< 15\mu s$/tick speed. |
| **Task 2** | Dynamic Python Runtime | `tests/test_indicator_runtime.py` | `< 1\text{ms}` hot-swap, `< 10\text{ms}` warm-up, error trapping. |
| **Task 3** | Local Data & VPS Gateway | `tests/test_data_gateway.py` | DuckDB caching, spot/option sync, offline replay. |
| **Task 4** | Replay & Backtest Core | `tests/test_replay_engine.py` | Zero-lookahead verified, "Break on Signal" halts accurately. |
| **Task 5** | Split-Screen Studio UI | Manual UI + Socket E2E | Resizable split view, Monaco autocompletion, chart sync. |
| **Task 6** | BSS Pro v5 Template | `tests/test_bss_pro_indicator.py` | BSS Pro v5 runs flawlessly in replay and live stream. |
