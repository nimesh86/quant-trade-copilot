# BUG 2: Missing Date Range & Historical Session Time Period Selector

> **Status:** Documented (Pending Implementation)  
> **Severity:** High (Feature Gap / Replay UX)  
> **Affected Files:** `trading_engine/web/static/studio.html`, `trading_engine/web/static/studio.css`, `trading_engine/web/static/studio.js`, `trading_engine/web/studio_router.py`

---

## 1. Problem Description
The top control header only displays static Symbol ("NIFTY 50"), Contract ("24,200 CE"), and Timeframe buttons ("1m", "5m"). There is no visual indication of the current trading date being replayed, no date picker to select historical trading dates (e.g. today, yesterday, specific expiry date), and no control to select the session time period (e.g. 09:15 - 15:30 intraday vs multi-day lookback).

## 2. Root Cause Analysis
1. In `studio.html`, the header contains `<select id="symbol-select">` and `<select id="contract-select">`, but no `<input type="date" id="replay-date-picker">` or quick preset selector ("Today", "Yesterday", "Last Expiry").
2. In `studio_router.py`, the `/api/replay/init` endpoint accepted `date` optionally, but `studio.js` defaulted to the current timestamp or dummy synthetic generation without sending the user-chosen historical date to DuckDB/local gateway.
3. The UI does not display a prominent badge showing the loaded session date range (e.g., `📅 2024-10-24 [09:15 - 15:30]`).

## 3. Solution Architecture & Technical Changes
1. **Header HTML Enhancements (`studio.html`):**
   - Add a Date & Range Control Group:
     ```html
     <div class="control-group date-selector-group">
       <span class="control-icon">📅</span>
       <input type="date" id="replay-date-picker" class="studio-date-input" title="Select Historical Trading Date">
       <select id="replay-preset-date" class="studio-select-sm" title="Quick Expiry / Session Select">
         <option value="today">Today</option>
         <option value="yesterday">Yesterday</option>
         <option value="last_expiry">Last Expiry</option>
         <option value="custom">Custom Date</option>
       </select>
       <span id="session-range-badge" class="session-badge">09:15 - 15:30 IST</span>
     </div>
     ```

2. **Backend API Enhancements (`studio_router.py`):**
   - Provide an endpoint `/api/replay/available-dates` returning distinct trading dates available in DuckDB.
   - Update `/api/replay/init` to query DuckDB for the exact selected date and time range:
     ```python
     bars = _data_store.query_bars(symbol=symbol, contract=contract, timeframe=timeframe, start_time=f"{date} 09:15:00", end_time=f"{date} 15:30:00")
     ```

3. **Frontend Integration (`studio.js`):**
   - Listen to `#replay-date-picker` `change` event.
   - When date changes, trigger `initReplaySession(date)` immediately and reload chart data and time axis.
   - Update `session-range-badge` to show the active date and total bar count (e.g. `2024-10-24 (375 bars)`).

## 4. Acceptance Criteria
- [ ] Header includes an interactive date picker and quick session selector.
- [ ] Selected date is displayed prominently with start/end time.
- [ ] Changing the date queries the exact historical session from DuckDB/cache and reloads the chart and replay engine seamlessly.
