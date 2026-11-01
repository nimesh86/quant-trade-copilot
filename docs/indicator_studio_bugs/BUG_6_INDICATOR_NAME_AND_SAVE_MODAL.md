# BUG 6: Missing Indicator Name Field, Save Dialog & Custom Indicator Management

> **Status:** Documented (Pending Implementation)  
> **Severity:** High (Workflow / Asset Management)  
> **Affected Files:** `trading_engine/web/static/studio.html`, `trading_engine/web/static/studio.css`, `trading_engine/web/static/studio.js`, `trading_engine/web/studio_router.py`

---

## 1. Problem Description
Clicking the "Save" button immediately dumps the raw editor code into the browser's localStorage under a hardcoded key and displays an alert "✅ Indicator script saved to local storage." without:
1. Allowing the user to name the indicator.
2. Prompting a modal/dialog with Name, Description, and Tags.
3. Storing multiple named indicators so users can switch between their own custom strategies.
4. Integrating saved indicators into the Template dropdown.

## 2. Root Cause Analysis
1. In `studio.js`, the `#btn-save` click listener had no modal prompt:
   ```javascript
   localStorage.setItem('studio_indicator_code', editor.getValue());
   ```
2. In `studio.html`, there is no `<input id="indicator-title-input">` in the editor toolbar, nor a `<div id="save-indicator-modal">`.
3. In `studio_router.py`, there was no backend endpoint for persistent server-side indicator saving to `trading_engine/indicators/user_indicators/`.

## 3. Solution Architecture & Technical Changes
1. **Indicator Name Header in Editor Toolbar (`studio.html`):**
   - Add an inline editable indicator title:
     ```html
     <div class="indicator-meta-group">
       <input type="text" id="indicator-name-input" class="indicator-title-input" value="Custom Indicator 1" placeholder="Indicator Name">
       <span class="save-status-pill" id="indicator-save-status">Saved</span>
     </div>
     ```
   - Add "Save" and "Save As..." buttons.

2. **Save Indicator Modal (`studio.html` & `studio.css`):**
   - Clean dark-glass modal with:
     - Indicator Name (`<input id="modal-indicator-name">`)
     - Description (`<textarea id="modal-indicator-desc">`)
     - Category Tag (Trend, Momentum, Volatility, Order Flow, Breakout)
     - Buttons: "Save Changes", "Save As New Copy", "Cancel"
   - Keyboard shortcut: `⌘S` / `Ctrl+S` triggers Save; `⇧⌘S` triggers Save As.

3. **Persistent Custom Indicators Storage (`studio.js` & `studio_router.py`):**
   - Maintain a list of user indicators: `indicator_manager.save(name, code, desc)`.
   - Update the Template dropdown to group templates into:
     - 📁 **System Presets** (BSS Pro v5, AlgoAlpha Breakout, Supertrend Cloud)
     - 📁 **My Custom Indicators** (User-saved scripts with delete [✕] action)
   - Store in `trading_engine/indicators/saved_indicators.json` via backend API (`POST /api/studio/indicators/save`, `GET /api/studio/indicators/list`, `DELETE /api/studio/indicators/{id}`).

## 4. Acceptance Criteria
- [ ] Users can edit the indicator name directly in the editor toolbar.
- [ ] Clicking "Save" or pressing `Ctrl+S` / `Cmd+S` opens the Save Modal (or auto-saves named indicators with status confirmation).
- [ ] Multiple indicators can be saved, renamed, deleted, and re-loaded from the Template dropdown.
- [ ] Indicators persist across browser refreshes and are saved to disk / server storage.
