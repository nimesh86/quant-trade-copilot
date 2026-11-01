# Task 5: Split-Screen Web Studio UI (Monaco + Canvas Chart)
## Detailed Specification, Architecture & Acceptance Criteria

> **Document Version:** 1.0.0  
> **Status:** Ready for Implementation  
> **Target Subsystem:** `trading_engine/web/static/` & `trading_engine/web/server.py`  
> **Key Files:** `studio.html`, `studio.css`, `studio.js`, `studio_chart.js`

---

## 1. Objective & UI/UX Vision

Task 5 builds the **complete, interactive user interface** for the Indicator Studio. 

It provides a responsive, dark-mode, dual-panel workspace:
* **Top Panel (Canvas Chart):** High-performance TradingView Lightweight Charts displaying synchronized dual views of the **Underlying Spot Index** and **Option Strike (CE / PE)** with real-time indicator overlays, dynamic support/resistance zones, and buy/sell signal markers.
* **Bottom Panel (Monaco Studio):** The world-class VS Code editor core (Monaco Editor) with native Python syntax highlighting, custom autocompletion for `quant_lib`, inline error squigglies, an interactive Variable Watch Table, and real-time execution telemetry.

---

## 2. Visual Layout & ASCII Wireframe

```
┌────────────────────────────────────────────────────────────────────────────────────────┐
│ MASTER HEADER BAR                                                                      │
│ [← Back] [Symbol: NIFTY 50 ▼] [Strike: 24200 CE/PE] [1m / 5m] │ ⚡ Latency: 12µs       │
├────────────────────────────────────────────────────────────────────────────────────────┤
│ TOP PANEL: High-Performance Canvas Chart (TradingView Lightweight Charts)              │
│ ┌────────────────────────────────────────────────────────────────────────────────────┐ │
│ │ [Chart Selector: 🔘 NIFTY SPOT | ⚪ 24200 CE | ⚪ 24200 PE]                        │ │
│ │                                                                                    │ │
│ │     /\    /\            [▲ BUY CALL @ 182.40 | SL: 168.00 | T1: 210.00]            │ │
│ │    /  \  /  \──Upper Resistance (ta.dynamic_sr: 24,200.00)                         │ │
│ │───/────\/────\─────────────────────────────────────────────                        │ │
│ │  /            \──Fast EMA (ta.ema 21: 24,175.50)                                   │ │
│ │                                                                                    │ │
│ ├────────────────────────────────────────────────────────────────────────────────────┤ │
│ │ Sub-Pane: RSI (14) = 64.2 [Normal] │ Buyer/Seller Strength Pressure = 68% BUYER    │ │
│ └────────────────────────────────────────────────────────────────────────────────────┘ │
├──────────────────────────────[ ═══ DRAGGABLE RESIZER BAR ═══ ]─────────────────────────┤
│ REPLAY & CONTROLS TOOLBAR                                                              │
│ [⏪ Rewind] [▶ Play / ⏸ Pause] [Step +1 Bar ⏩] [Speed: 2x ▼] [☑ Break on Signal]     │
│ [Timeline Scrubber: ━━━━━━●━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ 10:14:00 AM (Bar 245 / 375)] │
├────────────────────────────────────────────────────────────────────────────────────────┤
│ BOTTOM PANEL: Monaco Python Studio & Debugger Console                                  │
│ ┌───────────────────────────┬────────────────────────────────────────────────────────┐ │
│ │ Action Bar:               │ Monaco Code Editor (Python 3.10+)                      │ │
│ │ [⚡ Mount Live] [💾 Save] │ 1  from quant_lib import Indicator, ta, input          │ │
│ │ [Preset: BSS_Pro_v5 ▼]    │ 2                                                      │ │
│ ├───────────────────────────┤ 3  class BSSProScalper(Indicator):                     │ │
│ │ Script Inputs (Dynamic):  │ 4      def init(self):                                 │ │
│ │ Fast EMA:   [ 21 ] ━━━●━  │ 5          self.fast_ema = input.int(21, "Fast EMA")   │ │
│ │ Slow EMA:   [ 50 ] ━━━●━  │ 6          self.rsi_len  = input.int(14, "RSI Period") │ │
│ │ S/R Pivots: [  4 ] ━●━━━  │ 7      def on_bar(self, bar, history):                 │ │
│ ├───────────────────────────┤ 8          # Autocomplete triggers for quant_lib.ta.*  │ │
│ │ Live Variable Watch:      │ 9          fast_val = ta.ema(history.close, self.fast) │ │
│ │ • bar_index: 245          │ 10         sr = ta.dynamic_sr(history.high, ...)       │ │
│ │ • close: 182.40           │ 11         if ta.crossover(history.close, sr.res):     │ │
│ │ • fast_ema: 176.10        │ 12             return self.signal("BUY_CE", bar.close) │ │
│ │ • sr.resistance: 180.00   ├────────────────────────────────────────────────────────┤ │
│ │ • dominant: BUYER (68.4%) │ Console & Diagnostics Output:                          │ │
│ │ • rsi_val: 64.20          │ [10:14:00] AST Validation Passed. Compilation 1.2ms.   │ │
│ │                           │ [10:14:00] Replay Paused on Signal: BUY_CE @ 182.40    │ │
│ └───────────────────────────┴────────────────────────────────────────────────────────┘ │
└────────────────────────────────────────────────────────────────────────────────────────┘
```

---

## 3. Detailed UI Functional Specifications

### 3.1 Split-Screen Layout & Resizer Behavior
* **Vertical Split Gutter:** A 6px high draggable bar separating the top chart and bottom editor.
* **Double-Click to Reset:** Double-clicking the splitter snaps the layout to 50%/50%.
* **State Persistence:** Panel heights are saved to browser `localStorage.getItem("studio_split_ratio")` so page reloads maintain user preference.
* **Auto-Resize Observer:** A `ResizeObserver` monitors the chart container and calls `chart.resize(width, height)` immediately during drag to prevent canvas distortion.

### 3.2 Top Panel (Lightweight Charts Integration)
* **Underlying / Strike Switcher Tabs:**
  - Tab 1: **SPOT (NIFTY / BANKNIFTY)** — Shows index price action and master S/R levels.
  - Tab 2: **OPTION CE** — Shows Call option premium chart with strike-specific signals.
  - Tab 3: **OPTION PE** — Shows Put option premium chart with strike-specific signals.
* **Dynamic Overlays:**
  - Line series for moving averages (`fast_ema`, `slow_ema`, `vwap`).
  - Shaded price bands for `ta.supertrend` and `ta.breakout_channels`.
  - Horizontal rays for `ta.dynamic_sr` resistance (red) and support (green).
* **Signal Markers:**
  - `BUY_CE`: Green upward arrow marker below candle with label `"BUY CE @ 182.40"`.
  - `BUY_PE`: Red downward arrow marker above candle with label `"BUY PE @ 164.00"`.
  - Invalidation line (Stop Loss) drawn as a red dashed horizontal segment.
  - Target line (T1) drawn as a blue dashed horizontal segment.
* **Crosshair Synchronized Hover:**
  - Hovering crosshair over any past candle sends that bar's index to the bottom panel, which immediately populates the **Variable Watch Table** with the indicator variables computed for that specific candle!

### 3.3 Bottom Panel (Monaco Editor Integration)
* **Language & Theme:** Monaco Editor loaded in Python mode with a dark, high-contrast theme matching the platform (`#0b0f19` background).
* **IntelliSense Autocompletion:**
  - Injected custom completion provider for `quant_lib`, `ta.*`, `input.*`, and `self.signal()`.
  - Displays function signature, parameter docstrings, and examples when typing `ta.`.
* **Real-Time Inline Error Squigglies:**
  - When the user pauses typing for 500ms, a background worker calls `POST /api/studio/validate`.
  - If a syntax error is returned, Monaco sets markers (`monaco.editor.setModelMarkers`) highlighting the exact line with a red squiggly and tooltip explanation.
* **Dynamic Parameter Settings Drawer:**
  - Inspects `input.*` descriptors extracted by the backend.
  - Automatically renders sliders, steppers, and color pickers in the left sidebar.
  - Adjusting a slider fires an incremental re-render without editing the code.
* **Variable Watch Table:**
  - Formatted 2-column key-value table displaying internal variables (`bar_index`, `close`, `fast_ema`, `rsi`, `sr_levels`, `buyer_pct`).
* **Console Terminal:**
  - Formatted log stream showing compilation duration, warm-up status, tick latency, and `print()` outputs.

---

## 4. Frontend-Backend WebSocket & REST Communication Contracts

### 4.1 REST Endpoints (`trading_engine/web/server.py`)
| Endpoint | Method | Payload / Response |
| :--- | :--- | :--- |
| `/api/studio/validate` | `POST` | Input: `{code: str}` ➔ Output: `{valid: bool, errors: [...]}` |
| `/api/studio/mount` | `POST` | Input: `{symbol: str, contract: str, code: str, inputs: {...}}` ➔ Output: `{success: bool, schema: {...}, plots: {...}}` |
| `/api/studio/presets` | `GET` | Output: `[{id: "bss_pro_v5", name: "BSS Pro v5", code: "..."}, ...]` |
| `/api/replay/step` | `POST` | Input: `{direction: "+1" \| "-1"}` ➔ Output: `ReplayFrame` |
| `/api/replay/scrub` | `POST` | Input: `{bar_index: int}` ➔ Output: `ReplayFrame` |

### 4.2 WebSocket Message Protocol (`/ws/studio`)
* **Client-to-Server:**
  - `{"action": "START_REPLAY", "speed": 2.0, "break_on_signal": true}`
  - `{"action": "PAUSE_REPLAY"}`
* **Server-to-Client:**
  - `{"type": "REPLAY_FRAME", "frame": {...}}` (Delivers candle update, indicator plot values, and watch table dump).
  - `{"type": "BREAKPOINT_HIT", "reason": "Signal Fired: BUY_CE @ 182.40", "bar_index": 245}`

---

## 5. Impacted Areas in Existing Codebase

* **Static Assets:**
  - Create `trading_engine/web/static/studio.html`, `studio.css`, `studio.js`, and `studio_chart.js`.
  - Add navigation link from [contract.html](file:///Users/admin/Documents/Nimesh/Projects/Self%20Training%20Engine/trading_engine/web/static/contract.html) header (`btn-master-tvi` ➔ opens Indicator Studio).
* **FastAPI Server (`trading_engine/web/server.py`):**
  - Mount `/studio` route serving `studio.html`.
  - Register `/api/studio/*` endpoints and `/ws/studio` WebSocket channel.

---

## 6. Concrete Acceptance Criteria & Verification Matrix

| AC ID | Requirement | Metric / Tolerance | Test Function / Check |
| :--- | :--- | :--- | :--- |
| **AC-5.1** | Resizable Split Screen | Splitter drags smoothly without jitter; saves ratio to `localStorage`. | Manual / Cypress UI test |
| **AC-5.2** | Dual Chart Rendering | Correctly toggles between Spot Index, CE Option, and PE Option charts. | UI Visual Inspection |
| **AC-5.3** | Monaco Syntax & IntelliSense | Autocomplete appears on typing `ta.` with complete parameter signatures. | Editor Marker & Completion Check |
| **AC-5.4** | Real-Time Error Squigglies | Syntax error produces red squiggly underline on the exact broken line. | Syntax Validation Test |
| **AC-5.5** | Dynamic Inputs Drawer | Declaring `self.len = input.int(21)` renders interactive slider in sidebar. | Dynamic Schema UI Test |
| **AC-5.6** | Crosshair Hover Sync | Hovering over past bar updates the Variable Watch Table with that bar's data. | Chart Event Listener Test |
| **AC-5.7** | Break on Signal Visual Pause | Replay freezes the chart with signal marker and pops alert message. | Replay Breakpoint Test |

---

## 7. Comprehensive UI Test Plan

1. **Test Splitter Dragging & Persistence:** Drag splitter to 30%/70%, refresh browser, verify 30%/70% layout is maintained.
2. **Test Monaco Autocompletion:** Type `ta.ema(` in editor, verify parameter tooltip `(series, length)` appears.
3. **Test Syntax Error Catching:** Type intentional typo `def on_bar(`, wait 500ms, verify red squiggly appears on that line.
4. **Test Dynamic Slider:** Adjust Fast EMA slider from 21 to 50, verify chart overlay recalculates instantly.
5. **Test Replay Stepping:** Click "Step +1 Bar", assert top chart adds exactly 1 candle and watch table updates.
6. **Test Break on Signal:** Start replay on historical breakout day, verify playback halts automatically when signal fires.

---

## 8. PREREQUISITES & HAND-OFF CONTRACT FOR NEXT TASK (TASK 6)

### 8.1 Prerequisites Received from Tasks 1–4:
* `quant_lib` mathematical functions and `Indicator` base class (Task 1).
* Dynamic compiler, validator, and hot-reload registry (Task 2).
* DuckDB offline data store and VPS live stream (Task 3).
* Replay stepping controller, "Break on Signal" engine, and backtester (Task 4).

### 8.2 Hand-Off Deliverables to Task 6 (Pre-Packaged Templates & Validation):
Task 5 completes and hands off the following assets:
* ✅ **Interactive Split-Screen UI:** Fully functional `/studio` web page running Monaco Editor on the bottom and Lightweight Charts on the top.
* ✅ **Real-Time Code Execution:** Ability to type Python code, validate syntax in real time, and mount to replay or live feeds.
* ✅ **Inspection Tools:** Synchronized crosshair Variable Watch Table and Replay Scrubber.
* ✅ **Consumption by Task 6:** Task 6 will plug its reference indicators (`BSS_Pro_v5`, `AlgoAlpha_Breakout`, `Supertrend_Cloud`) directly into this studio's preset loader for final end-to-end user verification.

