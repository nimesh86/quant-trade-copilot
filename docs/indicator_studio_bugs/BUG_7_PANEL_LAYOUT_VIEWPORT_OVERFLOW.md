# BUG 7: Panel Layout Viewport Overflow Cutting Off Console & Backtest Panels

> **Status:** Documented (Pending Implementation)  
> **Severity:** High (Layout / Usability)  
> **Affected Files:** `trading_engine/web/static/studio.html`, `trading_engine/web/static/studio.css`, `trading_engine/web/static/studio.js`

---

## 1. Problem Description
As visible in the user's screenshot:
1. The entire bottom portion of the Indicator Studio (including the "Console & Logs" panel, "Backtest Scorecard" drawer, and bottom tab bar) is pushed completely below the bottom edge of the browser viewport.
2. The Monaco Editor itself is truncated at line 18, with no visible scrollbar or bottom dock.
3. Users cannot view runtime logs, compiler diagnostics, backtest results, or console outputs without scrolling the entire window or resizing the browser.

## 2. Root Cause Analysis
1. In `studio.css`, `body` and `.studio-container` do not enforce `height: 100vh; overflow: hidden;`.
2. The main vertical stack consists of:
   - Header (`~54px`)
   - Chart Panel (`~380px` or unbounded)
   - Replay Toolbar (`~42px`)
   - Split Gutter (`~6px`)
   - Editor Panel (`~400px`)
   - Bottom Console / Tabs (`~180px`)
   Total height = `> 1000px`, which exceeds standard laptop viewports (`800px - 900px`).
3. Because the layout uses standard block flow rather than a strict `height: 100vh; display: flex; flex-direction: column; overflow: hidden;` layout with proportional flex panels, the bottom panels overflow the window and disappear.

## 3. Solution Architecture & Technical Changes
1. **Strict 100vh Viewport Containment (`studio.css`):**
   ```css
   html, body {
     height: 100vh;
     max-height: 100vh;
     overflow: hidden;
     margin: 0;
     padding: 0;
   }

   .studio-app {
     display: flex;
     flex-direction: column;
     height: 100vh;
     max-height: 100vh;
     overflow: hidden;
   }

   .studio-header {
     height: 50px;
     flex-shrink: 0;
   }

   .studio-main-split {
     flex: 1;
     display: flex;
     flex-direction: column;
     min-height: 0; /* CRITICAL for nested flexbox scrolling */
     overflow: hidden;
   }

   .studio-chart-panel {
     height: 50%;
     min-height: 120px;
     flex-shrink: 0;
     position: relative;
   }

   .studio-editor-panel {
     flex: 1;
     min-height: 120px;
     display: flex;
     flex-direction: column;
     overflow: hidden;
   }
   ```

2. **Dockable / Collapsible Bottom Tab Bar:**
   - Integrate the bottom tabs ("Console & Logs", "Backtest Scorecard", "Variables Watch", "Inputs") as a sticky dock at the bottom of the editor panel or in a toggleable tray:
     ```html
     <div class="editor-bottom-dock">
       <div class="dock-tab-buttons">
         <button class="dock-tab active" data-tab="editor">Code Editor</button>
         <button class="dock-tab" data-tab="inputs">Inputs & Settings</button>
         <button class="dock-tab" data-tab="watch">Variable Watch</button>
         <button class="dock-tab" data-tab="console">Console & Logs (<span id="log-count">0</span>)</button>
         <button class="dock-tab" data-tab="scorecard">Backtest Scorecard</button>
       </div>
       <div class="dock-content" id="dock-panel-content">...</div>
     </div>
     ```
   - Clicking a tab opens that view without expanding the page beyond the viewport.
   - When "Backtest" is executed, automatically switch to the "Backtest Scorecard" tab and scroll into view smoothly within the viewport.

## 4. Acceptance Criteria
- [ ] The entire studio layout fits perfectly within 100vh of any browser window without page-level vertical scrollbars.
- [ ] Console & Logs and Backtest Scorecard are permanently accessible via the bottom dock.
- [ ] Running a backtest displays the scorecard cleanly without hiding or overflowing the editor.
