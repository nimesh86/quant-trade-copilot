# BUG 14 / FEATURE: Interactive Compilation Error Banner & Click-to-Jump Navigation

> **Status:** Ready for Implementation  
> **Severity:** High (Developer Experience / Error Diagnostics)  
> **Affected Files:** `trading_engine/web/static/studio.html`, `trading_engine/web/static/studio.css`, `trading_engine/web/static/studio.js`

---

## 1. Problem Description / User Question
*"If there is any compilation error, will this editor show where is the problem?"*
Users need an unmistakable, prominent diagnostic feedback system that:
1. Shows an immediate red warning banner above the code editor when a syntax error or security sandbox violation occurs.
2. Clearly displays the exact error message, filename, line number, and column.
3. Provides an interactive **`[ Jump to Line X ]`** button that instantly focuses Monaco at the exact offending line and highlights the error.
4. Auto-opens the "Console & Logs" drawer to show the compiler diagnostic traceback.

## 2. Technical Solution Architecture
1. **Compilation Diagnostic Banner (`studio.html` & `studio.css`):**
   - Add a sticky diagnostics bar directly below `#editor-toolbar`:
     ```html
     <div id="compilation-error-banner" class="compilation-error-banner" style="display:none;">
       <span class="err-icon">⚠️</span>
       <span id="err-message-text" class="err-text">SyntaxError: unexpected EOF while parsing</span>
       <button id="btn-jump-to-error" class="btn-jump-error">Jump to Line 42 →</button>
     </div>
     ```
2. **Monaco Integration (`studio.js`):**
   - In `validateCode()`:
     - If `data.valid === false`:
       - Show `#compilation-error-banner`.
       - Populate `#err-message-text` with `data.errors[0].message`.
       - Set button text `Jump to Line ${data.errors[0].line}`.
       - Attach click handler to `btn-jump-to-error`:
         `editor.revealLineInCenter(line); editor.setPosition({ lineNumber: line, column: col }); editor.focus();`
     - If `data.valid === true`:
       - Hide `#compilation-error-banner`.
       - Clear Monaco error squigglies.

## 3. Acceptance Criteria
- [ ] Any syntax or AST error immediately triggers the red diagnostic banner.
- [ ] Clicking "Jump to Line X" scrolls and positions the Monaco cursor on the exact error line.
- [ ] Fixing the error auto-dismisses the banner and turns the badge green ("AST: OK").
