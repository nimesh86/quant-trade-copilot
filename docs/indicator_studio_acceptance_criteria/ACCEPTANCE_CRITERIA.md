# Indicator Studio & Replay Debugger: Acceptance Criteria & Specification

This specification defines the rigorous Acceptance Criteria for the Indicator Studio & Replay Debugger, addressing all 12 core functional areas.

---

## Acceptance Criteria Checklist

### AC-01: Breakpoint & Editor Cursor Execution Line
- **AC-01.1:** Clicking the editor gutter next to any line number (e.g. Line 89 inside `on_tick` or Line 102 in `on_bar`) places a crisp red breakpoint glyph (`.debug-breakpoint-glyph`).
- **AC-01.2:** The active breakpoint line number is added to the Breakpoints drawer list in the left inspector.
- **AC-01.3:** Breakpoints are synchronized to the backend `ReplaySession` via WebSocket immediately.
- **AC-01.4:** When execution pauses at a breakpoint, Monaco editor highlights the paused line with a yellow bar (`.debug-line-highlight`) and displays the golden execution arrow (`▶` / `.debug-current-line-arrow`) in the glyph gutter.
- **AC-01.5:** Clicking a breakpoint in the drawer centers and scrolls Monaco to that line.

### AC-02: Tick-Wise Stepping & Watch Inspector
- **AC-02.1:** The replay engine supports sub-second tick granularity. Stepping forward with `+1 Tick` advances by exactly ONE TICK (not an entire 1-minute candle).
- **AC-02.2:** Each tick triggers `indicator.on_tick(ltp, volume, timestamp)`.
- **AC-02.3:** The active candle on the chart morphs in real time on each tick (open, high, low, close, volume updated incrementally).
- **AC-02.4:** The Variable Watch (Inspector) table updates on every single tick, displaying `ltp`, `tick_volume`, `tick_index`, `candle_open`, `candle_high`, `candle_low`, `candle_close`, `_cur_ema_fast`, `_cur_ema_slow`, and any user watch expressions.
- **AC-02.5:** Stepping backward (`-1 Tick`) safely rewinds the indicator state and candle to the preceding tick without loss of temporal isolation.

### AC-03: Chart Candlestick Fidelity & Indicator Overlays
- **AC-03.1:** Candlesticks render with clear green (`#10b981`) and red (`#ef4444`) bodies and distinct wicks (no flat dashes or doji crosses).
- **AC-03.2:** Price-based indicator overlays (`ema_fast`, `ema_slow`, `supertrend`, `dynamic_sr` resistance/support lines) are plotted directly on the candle series using the right price scale.
- **AC-03.3:** Normalized oscillators (`rsi`, `buyer_pct`, `seller_pct`) do NOT corrupt or overlap the candlestick price scale. Their values are displayed in the dynamic legend and watch table, or in a dedicated sub-pane.
- **AC-03.4:** The right price scale auto-fits the active instrument range (e.g. 100–250 for options, 24,000–24,500 for spot index).

### AC-04: Navigation & Playback Controls
- **AC-04.1:** `Step +1 Tick` / `Step -1 Tick` advances/rewinds 1 tick.
- **AC-04.2:** `Step +1 Candle` / `Step -1 Candle` jumps to the next/previous minute candle close.
- **AC-04.3:** `▶ Play` streams ticks at selected speed (1x, 2x, 5x, 10x) and updates the forming candle smoothly.
- **AC-04.4:** `⏸ Pause` halts playback immediately and retains exact state.
- **AC-04.5:** `⏮ Rewind / Restart` resets replay to market open (`09:15:00 IST`) with cleared markers and reset indicator state.
- **AC-04.6:** Timeline scrubber allows seeking forward and backward to any bar/tick in the session.

### AC-05: Contract Selection & Tab Synchronization
- **AC-05.1:** Header provides underlying selector (NIFTY, BANKNIFTY, FINNIFTY, SENSEX) and Master Strike Selector (e.g. 24,200 ATM, 24,100 ITM, 24,300 OTM).
- **AC-05.2:** Chart toolbar tabs provide `🔘 Index Spot`, `⚪ Call Option (CE)`, `⚪ Put Option (PE)`.
- **AC-05.3:** The contract name on the CE/PE tabs dynamically displays the selected strike (e.g. `24200 CE`, `24200 PE`), eliminating redundancy between the header dropdown and chart tabs.
- **AC-05.4:** Switching tabs rescales the chart immediately, updates the dynamic legend, and preserves buy/sell signal markers.

### AC-06: Date Range Selector
- **AC-06.1:** Single unified date dropdown `#select-replay-date` lists available historical trading days.
- **AC-06.2:** Defaults to the latest available trading date (`2026-10-30 (Latest)`).
- **AC-06.3:** Changing the date produces distinct, organic session price action and re-initializes tick replay.

### AC-07: Underlying Index Switching
- **AC-07.1:** Switching underlying (e.g. from NIFTY to BANK NIFTY or SENSEX) updates spot base price (~24,200 vs ~52,000 vs ~79,000).
- **AC-07.2:** Strike options automatically update to match the selected index's ATM strike step (50 for NIFTY, 100 for BANKNIFTY).

### AC-08: Real-Time Console & Logs
- **AC-08.1:** All actions (breakpoints set/removed, compilation success, signals triggered, step operations, date changes) are logged with timestamps in the Console & Logs drawer.
- **AC-08.2:** The console logs panel remains persistent and visible without disappearing on layout adjustments.

### AC-09: Vectorized Backtest Scorecard
- **AC-09.1:** Clicking `🚀 Quick Backtest` runs vectorized backtest in RAM.
- **AC-09.2:** Scorecard tab displays total trades, win rate %, profit factor, net PnL (₹), max drawdown, and Sharpe ratio.
