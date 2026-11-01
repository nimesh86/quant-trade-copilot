# BUG 11: Monaco Editor Line Selection Gutter Highlighting Drift (Line 74 vs 75)

> **Status:** Ready for Implementation  
> **Severity:** Medium (Code Editor Fidelity)  
> **Affected Files:** `trading_engine/web/static/studio.css`, `trading_engine/web/static/studio.js`

---

## 1. Problem Description
As shown in the user's screenshot, selecting line 74:
`closes = historical_bars["spot_close"].values if "spot_close" in historical_bars else historical_bars["close"].values`
causes line number **75** to be highlighted in blue in the gutter. The line text is selected correctly, but the highlighted gutter line number is off by 1.

## 2. Root Cause Analysis
1. In `studio.css`, the rule `.monaco-editor, .monaco-editor * { box-sizing: content-box !important; }` broke Monaco's internal pixel measurement. Monaco computes line coordinates assuming standard browser `box-sizing: border-box`. Applying `content-box !important` adds extra padding/border height to internal margin rows, creating vertical drift between the line-numbers column and the view-lines column.
2. Global styles on `line-height` or font-family caused fractional pixel rounding differences across 70+ lines of code, accumulating an entire 20px line offset by line 74.

## 3. Solution Architecture & Technical Changes
1. **Remove `content-box !important` (`studio.css`):**
   - Completely remove the override on `.monaco-editor *`.
   - Let Monaco manage its own internal elements using its default `border-box` layout.
2. **Explicit Line Height & Font Options in Monaco (`studio.js`):**
   - Ensure Monaco options have:
     ```javascript
     fontSize: 13,
     lineHeight: 20,
     renderLineHighlight: 'all',
     renderLineHighlightOnlyWhenFocus: false
     ```
   - Call `monaco.editor.remeasureFonts()` as soon as web fonts are ready:
     ```javascript
     document.fonts.ready.then(() => {
       monaco.editor.remeasureFonts();
     });
     ```

## 4. Acceptance Criteria
- [ ] Selecting line 74 highlights line number 74 in the gutter.
- [ ] No vertical drift occurs between code lines and gutter line numbers at any scroll position.
