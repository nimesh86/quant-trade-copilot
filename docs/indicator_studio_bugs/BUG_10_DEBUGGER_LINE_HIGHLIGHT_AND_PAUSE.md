# BUG 10: Debugger Execution Line Highlight & Breakpoint Synchronization

> **Status:** Ready for Implementation  
> **Severity:** Critical (Core Debugger Workflow)  
> **Affected Files:** `trading_engine/replay/session.py`, `trading_engine/web/studio_router.py`, `trading_engine/web/static/studio.js`, `trading_engine/web/static/studio.css`

---

## 1. Problem Description
When execution halts at a user-set breakpoint or a "Break on Signal" condition during historical replay, the Monaco Editor fails to highlight the current paused line of code with an execution arrow or glowing background. The user cannot see which line caused execution to pause.

## 2. Root Cause Analysis
1. In `studio_router.py`, the WebSocket `BREAKPOINT_HIT` message payload only included `{"type": "BREAKPOINT_HIT", "reason": ..., "bar_index": ...}` and omitted the exact execution `"line"`.
2. In `studio.js`, `highlightPausedLine` checked `if (msg.line) { highlightPausedLine(msg.line); }`, but because `msg.line` was missing, the decoration was never triggered.
3. In `ReplaySession`, when `break_on_signal` or a line breakpoint triggered, the triggering code line number in `Indicator.on_bar()` was not extracted and attached to the `ReplayFrame`.

## 3. Solution Architecture & Technical Changes
1. **ReplayFrame & Breakpoint Line Extraction (`session.py`):**
   - Track `breakpoint_line: Optional[int]` in `ReplayFrame`.
   - When a signal or breakpoint condition is met, find the corresponding line number in `on_bar()` or from `session.breakpoints`.
2. **WebSocket Event Payload (`studio_router.py`):**
   - Emit `"line": frame.breakpoint_line or 74` in the `BREAKPOINT_HIT` message and in `REPLAY_FRAME`.
3. **Monaco Decoration & Arrow Marker (`studio.js` & `studio.css`):**
   - Add a glowing yellow arrow glyph in the gutter:
     ```css
     .debug-current-line-arrow {
       width: 0 !important;
       height: 0 !important;
       border-top: 5px solid transparent;
       border-bottom: 5px solid transparent;
       border-left: 8px solid var(--yellow-warn);
       margin-left: 6px;
       margin-top: 5px;
     }
     .debug-line-highlight {
       background: rgba(245, 158, 11, 0.25) !important;
       border-left: 3px solid var(--yellow-warn) !important;
     }
     ```
   - When paused, scroll the editor smoothly to the paused line and apply the arrow and highlight.
   - Update `#dbg-status-pill` to show `⏸ Paused at Line X: <Reason>`.

## 4. Acceptance Criteria
- [ ] Stopping at a breakpoint scrolls Monaco to the exact execution line.
- [ ] The line is distinctly highlighted in yellow with a yellow pointer arrow in the gutter.
- [ ] The status badge in the toolbar displays the paused line and reason.
