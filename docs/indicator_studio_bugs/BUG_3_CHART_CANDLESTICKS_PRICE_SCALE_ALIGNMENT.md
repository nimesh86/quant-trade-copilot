# BUG 3: Chart Displaying Horizontal Lines with Missing Candlesticks & Price Scale Disparity

> **Status:** Documented (Pending Implementation)  
> **Severity:** Critical (Core Charting Defect)  
> **Affected Files:** `trading_engine/web/static/studio_chart.js`, `trading_engine/web/static/studio.js`, `trading_engine/web/static/studio.html`

---

## 1. Problem Description
As shown in the user's screenshot:
1. The TradingView Lightweight Chart displays flat horizontal lines (e.g. `upper_box: 152.79`, `lower_box: 142.33`, `atr: 4.52`, and a green horizontal baseline around 100).
2. **No candlesticks are visible on the chart.**
3. The right-hand price scale displays `-40.00` to `200.00`, whereas the top legend displays NIFTY Spot OHLC around `24,198.50 to 24,218.40`.
4. The candles at ~24,200 are scaled far off-screen, or the candlestick series was suppressed / not populated properly when loading the dataset, or indicator series are plotted onto the primary price scale without multi-scale separation.

## 2. Root Cause Analysis
1. **Price Scale Collision:**
   In TradingView Lightweight Charts, if an indicator outputs bounded or low-magnitude metrics (like `atr: 4.52` or `rsi: 54.0` or option prices `100.00 - 150.00`) and they are added to the chart without specifying `priceScaleId: 'overlay'` or a separate pane, Lightweight Charts forces all series onto the default `right` price scale.
   When a line series with values 4.52 and 150.0 is on the same scale as NIFTY candles (24,200), either:
   - The auto-scale fits 4.52 to 200, making the 24,200 candles exist 20,000 points above the viewport; OR
   - The candlestick series received Option strike data (100-150) while the legend was bound to Spot data, but the candlestick data formatting was malformed (`time` format mismatch: unix seconds vs YYYY-MM-DD string, or NaN open/high/low/close values).
2. **Oscillator vs Overlay Handling:**
   Indicators return both overlay values (EMAs, Supertrend, S/R levels) and sub-window oscillator values (RSI, ATR, Buyer %).
   In `studio_chart.js`, all plots in `frame.indicator_plots` were created as line series on the main price scale without separating price overlays from sub-pane oscillators.
3. **Missing `chart.timeScale().fitContent()` / AutoScale Call:**
   When the dataset initializes or changes, `fitContent()` was not invoked, or the series data was set before the container size was computed.

## 3. Solution Architecture & Technical Changes
1. **Multi-Scale Architecture in `studio_chart.js`:**
   - Detect whether the active view is **"Index Spot"** (NIFTY ~24,200) or **"Option Strike"** (e.g. 24200 CE ~100-200).
   - Format candlestick data strictly according to Lightweight Charts specification:
     ```javascript
     {
       time: Number(candle.time), // Unix timestamp in seconds
       open: Number(candle.open),
       high: Number(candle.high),
       low: Number(candle.low),
       close: Number(candle.close)
     }
     ```
   - Only overlay price-scale compatible indicators on the main candlestick axis:
     - Check plot name: if name is `rsi`, `buyer_pct`, `seller_pct`, or `atr`, assign to a separate pane or scale (`priceScaleId: 'oscillator'`, or create a secondary oscillator pane at the bottom of the chart).
     - Overlay lines (`ema_fast`, `ema_slow`, `supertrend`, `upper_box`, `lower_box`, `nearest_res`, `nearest_sup`) must scale relative to the selected asset's price range.
2. **Explicit Series Autoscale:**
   - Configure candlestick series with:
     ```javascript
     candlestickSeries.applyOptions({
       priceFormat: { type: 'price', precision: 2, minMove: 0.05 },
       scaleMargins: { top: 0.1, bottom: 0.2 }
     });
     chart.priceScale('right').applyOptions({
       autoScale: true,
       mode: 0 // Normal scale mode
     });
     chart.timeScale().fitContent();
     ```
3. **Asset Switching Toggle:**
   - Clicking "Index Spot" switches the candle series to Spot OHLC and overlays spot-scale indicators.
   - Clicking "Option Strike" switches the candle series to Option OHLC and overlays strike-scale indicators.

## 4. Acceptance Criteria
- [ ] Candlestick bodies are clearly rendered and visible with proper green/red colors.
- [ ] Price scale correctly matches the active asset: ~24,200 for Spot, ~100-200 for Option Strike.
- [ ] Oscillators (ATR, RSI, Volume Pressure) do not crush the candlestick price scale.
- [ ] Horizontal lines (S/R, Box Breakouts) align perfectly with candle highs/lows.
