# BUG 12: Price Scale Collision Between Spot Overlays and Option Candles

> **Status:** Ready for Implementation  
> **Severity:** Critical (Core Chart Visualization Defect)  
> **Affected Files:** `trading_engine/web/static/studio_chart.js`, `trading_engine/web/static/studio.js`

---

## 1. Problem Description
As captured in the user's screenshot:
1. When viewing the **`24200 CE`** option strike tab, the price scale on the right axis shows values ranging from `-8,000.00` to `24,000.00`, even though option strike prices are only around `100.00` to `200.00`.
2. The option candlesticks are crushed into a single flat horizontal band near 100.00.
3. Overlays at the top right show `ema_fast: 24282.85`, `supertrend: 24280.30`, `ema_slow: 24261.70`, which are Spot index price levels (~24,200), not Option strike price levels (~150).

## 2. Root Cause Analysis
1. In `BSSProV5Indicator`, technical indicators (`ema_fast`, `ema_slow`, `supertrend`, S/R) are computed on the Spot index (`history["spot_close"]`), giving values around `24,200`.
2. In `studio_chart.js`, `updateIndicatorPlots` added these Spot lines (`24,282`) directly onto the **right price scale**.
3. When the user switches the chart tab to **`⚪ 24200 CE`**, the candlestick series is populated with Option candles (~100 to 200). Because BOTH the Option candles (~150) and Spot indicators (~24,282) share the same `rightPriceScale`, Lightweight Charts is forced to expand the vertical scale to encompass both 100 and 24,282!
4. Consequently, the 150-point option candles occupy less than 0.5% of the chart height, appearing completely flattened.

## 3. Solution Architecture & Technical Changes
1. **Asset-Aware Indicator Scale Filtering (`studio_chart.js`):**
   - Detect the price magnitude of the active candlestick series:
     - If current asset is **Index Spot** (prices $> 10,000$):
       - Only plot indicators whose values match Spot magnitude ($> 1,000$) on `rightPriceScale`.
       - Hide Option-scale lines ($< 1,000$) or place them in an overlay pane.
     - If current asset is **Option Strike** (`ce` or `pe`, prices $< 1,000$):
       - **Do NOT plot Spot-scale indicators ($> 1,000$) on `rightPriceScale`!**
       - Only plot Option-scale indicators (e.g. `opt_close`, option targets, option stops, or option EMAs).
       - This ensures the option candles always occupy 100% of the visible chart height with proper dynamic autoscale!
2. **Explicit Series Autoscale on Tab Switch:**
   - Whenever the user clicks `Index Spot`, `24200 CE`, or `24200 PE`, re-filter visible plot lines, reset the price scale, and invoke `chart.priceScale('right').applyOptions({ autoScale: true })`.

## 4. Acceptance Criteria
- [ ] On `24200 CE` and `24200 PE`, the right price scale tightly hugs option prices (e.g. 80.00 to 220.00).
- [ ] Option candlesticks are clearly rendered with tall, distinct green and red bodies.
- [ ] On `Index Spot`, the right price scale hugs Spot prices (e.g. 24,000 to 24,500) and displays Spot EMAs.
- [ ] No price scale ever displays distorted spans from 0 to 24,000.
