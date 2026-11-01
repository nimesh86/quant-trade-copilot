# Bug Report 15: Indicator Studio Chart Fidelity, Timezone, Dynamic Legend, and Debugger Sync

**Status:** Fixed  
**Component:** `trading_engine/web/static/studio_chart.js`, `trading_engine/web/static/studio.js`, `trading_engine/web/studio_router.py`, `trading_engine/replay/session.py`  
**Test Suite:** `tests/test_studio_api.py`

---

## 1. Description of Issues Identified from User Screenshot

In the screenshot provided by the user, several critical issues were apparent:

1. **Dual / Overlapping Price Scales (40–240 Left vs 100–170 Right):**
   - Oscillators configured on `leftPriceScale` forced a visible second Y-axis spanning 40 to 240 on the left of the canvas. The option candle values (at ~150–200) conflicted with the right price scale, which topped out at 170.
2. **Candles Rendering as Flat Dashes / Crosses (`+ + + - - -`):**
   - The synthetic option generator generated minute deltas of only 0.1–0.2 points, rendering dojis / plus signs instead of clear green and red candle bodies.
3. **Time Scale Timezone Bug (03:45 UTC vs 09:15 IST):**
   - Lightweight Charts formatted raw timestamps with negative UTC offset, displaying `03:45`, `04:00`, `04:15` instead of Indian market trading hours (`09:15` to `15:30` IST).
4. **Variable Watch Inspector Missing Variables:**
   - The table displayed only 1 variable: `last_signal_bar: 55.00`. Crucial OHLC values, volume, spot values, indicator plots (`rsi`, `buyer_pct`, `seller_pct`), and internal state variables were absent.
5. **Static Hardcoded Header Legend:**
   - Chart toolbar legend remained stuck on static text `NIFTY 50 O: 24,198.50 H: 24,215.00 ... Fast EMA: 24,205.20`, never updating for Option contracts (`24200 CE`) or changing bars.
6. **Signal Marker Disappeared on Tab Switch:**
   - Switching between `Index Spot` and `24200 CE` called `candleSeries.setData()`, which cleared Lightweight Charts markers without reapplying them.
7. **Breakpoints Set in Monaco Were Not Sent to Replay Session:**
   - Setting a breakpoint on line 102 updated a local JS Set but never synchronized with `sess.line_breakpoints` over the WebSocket. Signal trigger lines were also paused at line 74 instead of the actual return line.
8. **Redundant Dual Date Controls:**
   - Header displayed both a native `<input type="date">` and a `<select>` dropdown next to each other.

---

## 2. Root Cause Analysis

- **Chart Scales:** Lightweight Charts default `leftPriceScale.visible: true` creates a second visible axis whenever any series targets `left`. By setting `leftPriceScale.visible: false` and binding `candleSeries` explicitly to `right`, oscillators plot cleanly without an unsightly duplicate axis.
- **Option Candle Volatility:** In `_generate_synthetic_market_data`, minute increments used tiny Gaussian noise. Implementing sequential walk with ATR-weighted body sizes (average body ~2.2 pts, min body ~0.5, max ~7.5) with realistic wicks creates vivid green/red bodies.
- **Timezone Alignment:** Parsing ISO time strings with `Date.UTC(y, m-1, d, h, min, s)` and setting `tickMarkFormatter` and `timeFormatter` to format UTC hours and minutes preserves Indian market hours (`09:15` to `15:30`).
- **Variable Watch Extraction:** `ReplaySession._extract_watch_variables` only captured `self.indicator.__dict__`. Updating it to include `bar_index`, `open`, `high`, `low`, `close`, `volume`, `spot_open`, `spot_close`, and `self.indicator.plots` populates the full inspector.
- **WebSocket Breakpoint Sync:** Added `UPDATE_BREAKPOINTS` action handler in `studio_websocket` and synced breakpoints on click and on WebSocket connection.

---

## 3. Resolution & Verification

1. **Lightweight Charts Options:**
   - `leftPriceScale: { visible: false }`
   - `candleSeries` bound to `priceScaleId: "right"`
   - `tickMarkFormatter` & `localization.timeFormatter` format `HH:MM` using UTC methods
   - `setCandles()` reapplies `this.markers` and invokes `timeScale().fitContent()` and `priceScale("right").applyOptions({ autoScale: true })`.
2. **WebSocket Breakpoint Synchronization:**
   - Gutter clicks send `UPDATE_BREAKPOINTS` action over WebSocket.
   - Replay engine sets `frame.breakpoint_line = 167` or `182` on signal fires.
3. **Dynamic Legend:**
   - Added `updateChartLegend()` in `studio.js` to dynamically update symbol, live OHLC, and indicator plots on hover and frame updates.
4. **Automated Testing:**
   - `tests/test_studio_api.py`: 13/13 passed.
   - Core test suites: 40/40 passed (53/53 total).
