# BUG 1: Split Panel Resize and Chart Window Minimum Height Constraint

> **Status:** Documented (Pending Implementation)  
> **Severity:** High (UI/UX)  
> **Affected Files:** `trading_engine/web/static/studio.css`, `trading_engine/web/static/studio.js`, `trading_engine/web/static/studio_chart.js`

---

## 1. Problem Description
When dragging the split gutter (`#split-gutter`) upwards to give more space to the bottom Monaco editor / debugger panel, the resize bar visually moves up, but the top chart container remains stuck at a fixed minimum height (or fails to shrink below a large hardcoded min-height threshold), causing visual clipping, dragging desynchronization, and failure of the chart to dynamically resize to smaller heights.

## 2. Root Cause Analysis
1. In `studio.css`, `.studio-chart-panel` or its parent `.studio-main-split` has a rigid `min-height: 350px` or `min-height: 400px` CSS rule, or `flex-shrink: 0`.
2. In `studio.js`, the gutter dragging mouse event listener calculates percentages or pixel heights, but either clamps to an overly large minimum height or does not trigger `window.studioChart.resize()` on every micro-frame of the drag.
3. Lightweight Charts (`createChart`) requires explicit `chart.resize(width, height)` or `autoSize: true` with a flex container that has `min-height: 0` and `overflow: hidden`.

## 3. Solution Architecture & Technical Changes
1. **CSS Adjustments (`studio.css`):**
   - Set `.studio-chart-panel` `min-height: 120px` (low sensible minimum allowing wide range of split adjustments).
   - Set `.studio-editor-panel` `min-height: 120px`.
   - Ensure `#chart-viewport` has `width: 100%`, `height: 100%`, `min-height: 0`, and `overflow: hidden`.
   - Ensure `.studio-main-split` uses `display: flex; flex-direction: column; height: calc(100vh - var(--header-height)); overflow: hidden;`.

2. **JavaScript Dragging Logic (`studio.js`):**
   - Update mousemove handler during gutter drag:
     ```javascript
     const minChartHeight = 120;
     const minEditorHeight = 120;
     const totalHeight = container.getBoundingClientRect().height;
     const newChartHeight = Math.max(minChartHeight, Math.min(e.clientY - containerTop, totalHeight - minEditorHeight));
     chartPanel.style.height = `${newChartHeight}px`;
     editorPanel.style.height = `${totalHeight - newChartHeight - gutterHeight}px`;
     window.studioChart?.resize();
     window.studioEditor?.layout();
     ```
   - Ensure `ResizeObserver` is attached to `#chart-viewport` and calls `chart.applyOptions({ width, height })`.

## 4. Acceptance Criteria
- [ ] Dragging the split gutter smoothly resizes both the chart panel and editor panel from 120px up to full screen.
- [ ] Chart does not get clipped or stuck at 350px/400px minimum height.
- [ ] Both Monaco Editor and Lightweight Charts re-render their canvas/layout synchronously during and after drag.
