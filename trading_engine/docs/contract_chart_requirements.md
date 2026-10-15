# Product & Technical Requirements Specification: Dedicated Contract Analysis Page

**Document Version:** 1.0.0  
**Author:** AI Trading Architecture Team  
**Date:** October 15, 2026  
**Status:** Approved for Implementation (Frozen Checkpoint: `v1.0-checkpoint`)  

---

## 1. Executive Summary & Objective

The objective of this module is to deliver a dedicated, high-performance **Contract Chart & Live Analysis Page** separate from the main autonomous trading engine dashboard. 

Traders require the ability to search for **any** NIFTY index derivative contract (Call/Put options, weekly/monthly expiries, futures) or individual stock equity/derivative, visualize its intraday price action exclusively for today's active session, view the **Smart Money Flow (SMF) Cloud** indicator with multi-timeframe aggregation, store intraday ticks until the end of the day, and perform automatic mid-session catch-up downloads.

```mermaid
flowchart TD
    User([User in Browser]) -->|Types 'NIFTY 23000'| SearchUI[Contract Search Box]
    SearchUI -->|Debounced Query| SearchAPI[/api/contracts/search]
    SearchAPI -->|Autocomplete Matches| SearchUI
    User -->|Selects Contract| ChartEngine[Dedicated Chart View]
    
    ChartEngine -->|1. Morning Catch-Up Request| HistoryAPI[/api/contracts/candles?symbol=...&catchup=true]
    HistoryAPI -->|Fetch 09:15 to Current Time| KiteAPI[Zerodha Kite Historical API]
    KiteAPI -->|1-Min Candles| ChartEngine
    
    ChartEngine -->|2. Real-Time Stream Subscription| WS[/ws/contracts]
    WS -->|Live Ticks 100ms| TransientBuffer[(In-Memory Ring Buffer)]
    TransientBuffer -->|Forming Candle & Tick| ChartEngine
    ChartEngine -->|Canvas Render| SMFCloud[BOSWaves SMF Cloud Overlay]
    
    TransientBuffer -->|15:30 IST Market Close| DailyFlush[Flush / Prune Ticks]
```

---

## 2. Scope & Core Requirements Breakdown

| Req # | Requirement Name | Description & Acceptance Criteria |
| :--- | :--- | :--- |
| **REQ-1** | **Dedicated Page & Routing** | An independent HTML/JS view (`/contract.html` or `/chart`) completely decoupled from the main engine dashboard. Clean layout focused 100% on chart analytics. |
| **REQ-2** | **Full SMF Cloud Chart Integration** | High-performance candlestick chart utilizing TradingView Lightweight Charts and the custom HTML5 Canvas overlay for the **Smart Money Flow Cloud (BOSWaves)** indicator, electric blue/hot pink color grading, trend gauge, and multi-timeframe controls (`1m`, `3m`, `5m`, `15m`). |
| **REQ-3** | **Universal Contract Search Autocomplete** | Top-level search input supporting fuzzy search and autocomplete for NIFTY weekly/monthly options (CE/PE), futures contracts (`NIFTY-I`), and NSE equity stocks (`RELIANCE`, `HDFCBANK`, etc.) sourced from `symbol_master`. |
| **REQ-4** | **Strict Current Market Session Isolation** | The chart displays data strictly for **today's session** (09:15 AM to 15:30 IST). Before 09:15 AM, the chart remains blank in waiting mode with no prior-day candles prepended. |
| **REQ-5** | **Transient Daily Tick Storage** | In-memory ring buffer (and optional transient database table) to buffer ticks for active contracts during market hours. Automatically discarded/flushed at market close (15:30 IST). |
| **REQ-6** | **Automatic Mid-Session Catch-Up Ingestion** | If opened mid-session (e.g. at 10:00 AM or 13:30 IST), the backend automatically downloads historical 1-minute bars from **09:15 AM up to the current minute**, populates the chart, and then seamlessly hands over to live ticks. |

---

## 3. Detailed Functional Specifications

### REQ-1: Dedicated Page Architecture
- **Route / URL:** Accessible via `/contract.html` (or `/contract`).
- **Layout & Structure:**
  - **Header Bar:** Symbol Search Box, Selected Contract Badge (Symbol, Expiry, Strike, Spot/LTP, Change %), Timeframe Selector (`1m`, `3m`, `5m`, `15m`), SMF Cloud Toggle button, and Connection Health pill.
  - **Main Viewport:** Full-width, responsive TradingView Candlestick Chart.
  - **Canvas Layer:** Hardware-accelerated `#contract-smf-canvas` overlay for rendering dynamic gradient ribbons.
  - **Bottom Center Bar:** Pine Script-styled Trend Strength Gauge (0% to 100% Bull/Bear) and session range summary (Session High, Session Low, Day Open, VWAP).

---

### REQ-2: Smart Money Flow Cloud Chart Engine
- **Candlestick Series:** Electric Cyan (`#00c8ff`) for bullish candles and Hot Pink / Neon Red (`#ff005d`) for bearish candles.
- **SMF Adaptive Bands:**
  - Fast Period: 9 (adaptive EMA/RSI momentum filter).
  - Slow Period: 21 (trend baseline).
  - Upper & Lower Boundary Bands with custom polynomial interpolation.
- **Canvas Ribbon Overlays:**
  - Dual linear gradients filling the ribbon between Upper and Lower bands.
  - Cyan-to-transparent for Bullish cloud regime; Red-to-transparent for Bearish regime.
- **Multi-Timeframe Aggregation:**
  - Base resolution is 1-minute.
  - Client-side candle aggregation engine dynamically groups bars into `3m`, `5m`, and `15m` without requiring separate backend queries.

---

### REQ-3: Universal Contract Search & Autocomplete
- **Search Experience:**
  - Keyboard shortcut: Press `/` or `Ctrl + K` / `Cmd + K` to focus the search box.
  - Typeahead with 150ms debounce.
  - Dropdown displays categorized results:
    - **Indices:** `NIFTY 50`, `BANKNIFTY`, `FINNIFTY`
    - **Options:** `NIFTY 24 OCT 23000 CE`, `NIFTY 24 OCT 22800 PE`, etc. (showing Strike, Option Type, Expiry)
    - **Futures:** `NIFTY-I (Current Month)`, `NIFTY-II (Next Month)`
    - **Equities:** `RELIANCE`, `TCS`, `INFY`, `HDFCBANK`, `ICICIBANK`, etc.
- **Data Source:**
  - Indexed PostgreSQL `symbol_master` table (contains 14,600+ instruments) synced with Zerodha Kite Connect instruments registry.

---

### REQ-4: Current Session Only (09:15 - 15:30 IST)
- **Pre-Market Behavior (< 09:15 AM IST):**
  - Displays a clean status badge: *"Market opens at 09:15 AM IST"*.
  - Chart canvas remains blank with grid lines visible; no candles from prior trading days are loaded or displayed.
- **Market Hours (09:15 - 15:30 IST):**
  - Displays today's session timeline starting cleanly at `09:15`.
  - Forms the current live candle in real time from incoming ticks.
- **Post-Market (> 15:30 IST):**
  - Freezes the complete day's session (375 bars for index/equity) for post-market review.

---

### REQ-5: Transient Daily Tick Storage & Memory Ring Buffer
- **Storage Strategy:**
  - High-throughput in-memory circular buffer (`collections.deque(maxlen=100_000)` per active symbol) to hold sub-second ticks.
  - Low disk I/O: raw tick data does not bloat permanent database tables unless explicitly flagged for backtesting.
- **Lifecycle Management:**
  - **Retention:** From 09:00 AM IST to 16:00 IST on the same day.
  - **Pruning Routine:** Automatic end-of-day garbage collection at 16:00 IST flushes the memory buffer, keeping memory footprint < 150MB.

---

### REQ-6: Automatic Morning Catch-Up Historical Ingestion
- **Scenario:** Trader opens the contract page at 10:45 AM IST, or searches for a newly selected stock option at 13:15 IST.
- **Workflow:**
  1. Frontend sends initial request to `/api/contracts/candles?symbol={SYMBOL}&session=today`.
  2. Backend checks if today's 1-minute bars are already stored locally in `minute_candles`:
     - If local bars exist up to `now - 1 min`, backend returns the local database bars.
     - If bars are missing or incomplete (e.g. newly selected option contract), backend triggers an on-demand KiteConnect call:
       ```python
       kite.historical_data(
           instrument_token=token,
           from_date=today_open_0915,
           to_date=now_datetime,
           interval="minute"
       )
       ```
     - Inserts the morning bars into database/cache.
  3. Returns the complete 09:15 → Current Time 1-minute candle sequence.
  4. Frontend charts the morning history, executes `applySMFCloud()`, and subscribes to the live WebSocket feed for sub-second tick updates thereafter.

---

## 4. API & Technical Interface Specifications

### 4.1. Contract Search API
```http
GET /api/contracts/search?q={query}&limit=15
```
**Response Format:**
```json
{
  "query": "NIFTY 23000",
  "total_matches": 2,
  "results": [
    {
      "symbol": "NIFTY26O2423000CE",
      "display_name": "NIFTY 24 OCT 23000 CE",
      "underlying": "NIFTY",
      "strike": 23000.0,
      "option_type": "CE",
      "expiry": "2026-10-24",
      "segment": "NFO-OPT",
      "instrument_token": 12894210
    },
    {
      "symbol": "NIFTY26O2423000PE",
      "display_name": "NIFTY 24 OCT 23000 PE",
      "underlying": "NIFTY",
      "strike": 23000.0,
      "option_type": "PE",
      "expiry": "2026-10-24",
      "segment": "NFO-OPT",
      "instrument_token": 12894466
    }
  ]
}
```

---

### 4.2. Current Session Candle Catch-Up API
```http
GET /api/contracts/candles?symbol={symbol}&date=today
```
**Response Format:**
```json
{
  "symbol": "NIFTY26O2423000CE",
  "date": "2026-10-15",
  "market_status": "OPEN",
  "num_candles": 75,
  "candles": [
    {
      "time": "2026-10-15 09:15",
      "timestamp": 1792035900,
      "open": 142.5,
      "high": 148.0,
      "low": 139.2,
      "close": 146.8,
      "volume": 28400
    }
  ]
}
```

---

### 4.3. Contract WebSocket Tick Stream
```http
WebSocket: /ws/contracts?symbol={symbol}
```
**Packet Schema (`CONTRACT_TICK`):**
```json
{
  "type": "CONTRACT_TICK",
  "symbol": "NIFTY26O2423000CE",
  "ltp": 146.8,
  "high": 152.0,
  "low": 138.5,
  "open": 142.5,
  "volume": 312000,
  "timestamp": "2026-10-15T10:30:15.120+05:30",
  "forming_candle": {
    "time": 1792040400,
    "open": 146.0,
    "high": 147.2,
    "low": 145.8,
    "close": 146.8,
    "volume": 4200
  }
}
```

---

## 5. File Structure Plan

```text
trading_engine/
├── web/
│   ├── server.py                   # Mounts /contract route & catch-up endpoints
│   ├── contract_data_manager.py     # In-memory transient tick ring buffer & EOD cleanup
│   └── static/
│       ├── contract.html           # Dedicated Contract Analysis HTML page
│       ├── contract.js             # Contract search, chart & WebSocket controller
│       ├── contract.css            # Focused full-screen charting styles
│       ├── app.js                  # Preserved existing trading engine frontend
│       └── index.html              # Preserved existing trading engine dashboard
```

---

## 6. Verification & Quality Gates

1. **Pre-Market Cleanliness:**
   - Loading `/contract.html` before 09:15 AM must render a clean blank chart with a pre-market status notice (zero prior day candles).
2. **Search Speed:**
   - Autocomplete query responses under 30ms against the 14,600+ contract index in `symbol_master`.
3. **Mid-Day Catch-Up Test:**
   - Switching contracts at 11:30 AM must load all 135 1-minute candles from 09:15 to 11:30 in under 400ms, followed immediately by live tick continuation.
4. **Zero Main Engine Interference:**
   - The contract analysis page operates on isolated routes and memory buffers, with zero side effects on the autonomous paper trading engine or main dashboard.
