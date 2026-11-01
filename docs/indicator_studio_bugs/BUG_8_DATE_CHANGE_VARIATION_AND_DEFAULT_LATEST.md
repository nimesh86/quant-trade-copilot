# BUG 8: Date Change Candle Variation & Default to Latest Trading Session

> **Status:** Ready for Implementation  
> **Severity:** High (Data & Session Accuracy)  
> **Affected Files:** `trading_engine/web/studio_router.py`, `trading_engine/web/static/studio.js`, `trading_engine/web/static/studio.html`

---

## 1. Problem Description
1. When the user changes the date in the top bar (e.g. from Oct 24 to Oct 30), the candles on the chart remain identical because the random seed was hardcoded to `np.random.seed(42)` across all dates.
2. By default, the application should load the **most recent available trading session** (e.g. `2026-10-30` or yesterday if today's market is closed), rather than an arbitrary historical day.

## 2. Root Cause Analysis
1. In `studio_router.py`, `_generate_synthetic_candles` used a static seed `np.random.seed(42)`. Without date-dependent seeding, every date produces identical price vectors.
2. The default date in `studio.html` and `studio.js` was statically set to `2024-10-24`.
3. In `/api/replay/available-dates`, dates were hardcoded rather than dynamically sorting descending and providing the latest active trading day.

## 3. Solution Architecture & Technical Changes
1. **Date-Dependent Deterministic Seeding (`studio_router.py`):**
   ```python
   # Derive unique seed from date and symbol
   seed_int = int(hashlib.md5(f"{symbol}_{contract}_{date_str}".encode()).hexdigest()[:8], 16)
   np.random.seed(seed_int)
   ```
2. **Dynamic Available Dates Endpoint (`studio_router.py`):**
   - Provide the latest actual trading date as `default_date` (e.g. `2026-10-30`).
   - Query `minute_candles` and `tick_data` for available dates, fallback to standardized recent sessions `["2026-10-30", "2026-10-29", "2026-10-28", "2026-10-27", "2026-10-24"]`.
3. **Frontend Default Selection (`studio.js`):**
   - On application load, fetch `/api/replay/available-dates`.
   - Populate `#select-replay-date` and `#select-date-preset` with the returned list and select `default_date`.
   - Initialize the chart and replay session with this latest date automatically.

## 4. Acceptance Criteria
- [ ] By default, the studio loads the most recent trading session (`2026-10-30`).
- [ ] Changing the date immediately refreshes the chart with distinct, authentic market candles specific to that date.
- [ ] Test cases in `test_studio_api.py` verify date distinctness and latest date defaulting.
