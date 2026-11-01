# BUG 4: VSCode-Style Interactive Debugger, Breakpoint Gutter, and Chart Synchronization

> **Status:** Documented (Pending Implementation)  
> **Severity:** Critical (Core Feature Alignment)  
> **Affected Files:** `trading_engine/web/static/studio.html`, `trading_engine/web/static/studio.css`, `trading_engine/web/static/studio.js`, `trading_engine/web/studio_router.py`, `trading_engine/replay/session.py`

---

## 1. Problem Description
The IDE was designed to provide a VSCode-like debugging experience, but currently lacks:
1. **Debugger Action Toolbar:** Standard VSCode debugging controls (Continue ▶, Pause ⏸, Step Over ↷, Step Into ⇲, Step Out ⇱, Restart ↺, Stop ⏹).
2. **Editor Breakpoints:** Users cannot click on the Monaco editor line number gutter to set a red breakpoint circle.
3. **Chart & Execution Synchronization:** When a breakpoint or conditional signal hits during replay / backtest, the execution does not halt at that exact line and candle, the editor does not highlight the paused line, and the chart does not pause synchronously.
4. **Watch & Call Stack Inspector:** While a minimal watch placeholder existed, there is no interactive Watch expression input (allowing user to type any variable or expression like `c - upper`, `trend_dir[-1]`, `press.dominant_side`) with real-time value evaluation when paused.

## 2. Root Cause Analysis
1. Monaco editor initialization in `studio.js` did not configure `glyphMargin: true` and did not register `editor.onMouseDown` handler for `target.type === monaco.editor.MouseTargetType.GUTTER_GLYPH_MARGIN`.
2. The UI lacked the dedicated floating or docked VSCode Debugger Toolbar.
3. Breakpoints were not sent to the replay engine session (`session.breakpoints = [line_numbers]`).
4. In `session.step_forward()`, the indicator evaluation was not wrapped with an execution hook to evaluate breakpoint line hits and return the paused line context and local variables snapshot.

## 3. Solution Architecture & Technical Changes
1. **Monaco Breakpoint Gutter (`studio.js`):**
   - Enable `glyphMargin: true` in Monaco options.
   - Maintain a `Set<number>` of active breakpoint line numbers.
   - Attach click listener to gutter:
     ```javascript
     editor.onMouseDown((e) => {
       if (e.target.type === monaco.editor.MouseTargetType.GUTTER_GLYPH_MARGIN ||
           e.target.type === monaco.editor.MouseTargetType.GUTTER_LINE_NUMBERS) {
         const line = e.target.position.lineNumber;
         toggleBreakpoint(line);
       }
     });
     ```
   - Render a glowing red circle CSS class `.breakpoint-glyph` via `editor.deltaDecorations()`.

2. **VSCode Debug Toolbar (`studio.html` & `studio.css`):**
   - Add a modern VSCode-style floating debugger bar above the editor:
     - **Continue / Resume (F5 / ▶)**
     - **Step Over (F10 / ↷)**
     - **Step Into (F11 / ⇲)**
     - **Step Out (⇧F11 / ⇱)**
     - **Restart Replay (⇧⌘F5 / ↺)**
     - **Stop (⇧F5 / ⏹)**

3. **Chart & Execution Synchronization:**
   - When replay reaches a bar that triggers a breakpoint (e.g. signal fired or line breakpoint condition satisfied):
     - Playback automatically halts (`is_playing = false`).
     - Monaco editor scrolls to and highlights the paused line with a yellow arrow and glowing background (`.debug-line-highlight`).
     - Chart moves crosshair to the exact candle timestamp and highlights it.
     - WebSocket broadcasts `DEBUG_BREAKPOINT_HIT` with line number, candle index, and variable dict.

4. **Interactive Variable Watch Window:**
   - Provide an "Add Expression" `[ + ]` input in the Watch panel.
   - Automatically display all indicator fields: `close`, `high`, `low`, `ema_fast`, `supertrend`, `buyer_pct`, `plots`, `signals`.
   - Update values dynamically as the user steps through bars.

## 4. Acceptance Criteria
- [ ] Clicking in the editor gutter toggles red breakpoint dots.
- [ ] Dedicated VSCode-style debugger toolbar with Play, Pause, Step (+1/-1), Restart, Stop.
- [ ] Hitting a breakpoint immediately halts replay, highlights the line in yellow in Monaco, and highlights the corresponding candle on the chart.
- [ ] Watch window displays live variables and allows user-defined expressions.
