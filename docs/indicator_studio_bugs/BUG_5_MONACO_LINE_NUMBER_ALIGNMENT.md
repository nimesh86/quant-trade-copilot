# BUG 5: Monaco Editor Line Number and Selection Desynchronization

> **Status:** Documented (Pending Implementation)  
> **Severity:** Medium (Code Editing Usability)  
> **Affected Files:** `trading_engine/web/static/studio.css`, `trading_engine/web/static/studio.js`

---

## 1. Problem Description
When clicking or selecting a line in the Monaco Editor, the text is selected accurately, but the highlighted line number in the gutter corresponds to the following line (off by 1 line) or shifts out of vertical alignment as scrolling occurs.

## 2. Root Cause Analysis
1. **Font Metric / Line-Height Mismatch:**
   Monaco Editor calculates line geometry in JavaScript using fixed integer line heights (e.g. `lineHeight: 20` or `19`). If CSS defines `line-height` on parent elements or on `*` with a relative or unitless value (e.g. `line-height: 1.5` or `line-height: 1.6`), the DOM rendering of the text content drifts by a fraction of a pixel per line relative to the gutter margin.
2. **Web Font Loading Drift:**
   If a custom font (such as `'JetBrains Mono'`, `'Fira Code'`, or `'SF Mono'`) loads after Monaco has initialized, Monaco's initial character measurement cache becomes invalid, causing selection boxes, line numbers, and cursor vertical coordinates to misalign.
3. **Missing `monaco.editor.remeasureFonts()`:**
   After font stylesheets are loaded or container dimensions change, Monaco requires `monaco.editor.remeasureFonts()` to synchronize the glyph margin, line numbers, and view lines.

## 3. Solution Architecture & Technical Changes
1. **Explicit Monaco Editor Geometry Options (`studio.js`):**
   ```javascript
   const editorOptions = {
     fontSize: 13,
     lineHeight: 20,
     fontFamily: "'JetBrains Mono', 'Fira Code', 'Menlo', 'Monaco', 'Courier New', monospace",
     fontLigatures: true,
     lineNumbers: 'on',
     lineNumbersMinChars: 4,
     glyphMargin: true,
     renderLineHighlight: 'all',
     scrollBeyondLastLine: false,
     automaticLayout: true
   };
   ```
2. **Font Load Listener (`studio.js`):**
   ```javascript
   document.fonts.ready.then(() => {
     monaco.editor.remeasureFonts();
   });
   ```
3. **CSS Reset for Monaco Container (`studio.css`):**
   Ensure `.monaco-editor`, `.monaco-editor .view-line`, and `.monaco-editor .line-numbers` are isolated from global typographic styles:
   ```css
   .monaco-editor,
   .monaco-editor * {
     box-sizing: content-box !important;
   }
   #monaco-container {
     line-height: normal !important;
     letter-spacing: normal !important;
   }
   ```

## 4. Acceptance Criteria
- [ ] Selecting any line in the Monaco Editor highlights the exact corresponding line number in the gutter.
- [ ] No vertical drift occurs between line numbers and code text, regardless of document length or scroll position.
- [ ] Font ligatures and line numbers align with pixel precision.
