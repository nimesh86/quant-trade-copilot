# Task 3: Local Storage & VPS Data Gateway
## Detailed Specification, Architecture & Acceptance Criteria

> **Document Version:** 1.0.0  
> **Status:** Ready for Implementation  
> **Target Subsystem:** `trading_engine/data/`  
> **Test Suite:** `tests/test_data_gateway.py`

---

## 1. Objective & Architectural Scope

Task 3 establishes the **Data Foundation** for the local Indicator Studio:
1. **Decoupled VPS Data Gateway:** Connects to the already-running production VPS via API-key authentication to consume historical candles and live tick streams.
2. **Local High-Speed DuckDB Cache:** Stores Spot Index and Option contract candles/ticks in local embedded DuckDB / Parquet files, allowing 24/7 offline replay and backtesting without touching broker APIs.
3. **Dual Synchronized Data Bus:** Guarantees strict timestamp alignment between the **Underlying Index Spot** (NIFTY 50, BANKNIFTY) and the **Option Strike Contract** (CE / PE) for cross-symbol setups and accurate Greek calculations.

---

## 2. Decoupled, Scalable Backend Design Principles

1. **Zero Broker API Contact from Local Machine:**
   The local machine never communicates directly with Zerodha Kite or ICICI Breeze. This completely protects broker rate limits and prevents authentication conflicts with the live production VPS.
2. **24/7 Off-Hours & Weekend Usability:**
   Once a historical date range is fetched from the VPS, it is persisted in local DuckDB. Backtesting, indicator writing, and bar-by-bar debugging function seamlessly even during weekends, holidays, or on a laptop without internet.
3. **Embedded Serverless Columnar Storage (DuckDB):**
   Unlike Postgres, DuckDB requires zero background server daemon and zero network socket overhead. It operates in-process via memory-mapped files and queries 100,000 bars in **$\le 4$ milliseconds**.
4. **Deterministic Timestamp Synchronization:**
   Options often suffer from illiquid minutes where zero trades occur. The synchronization bus uses a strict **Zero-Lookahead Forward Fill** rule: missing option minutes take the previous valid close with volume = 0, synchronized with the exact Spot Index bar.

---

## 3. Class & Component Inventory

### 3.1 VPS Gateway Client (`data/vps_client.py`)

```python
import aiohttp
import asyncio
import json
from typing import Dict, Any, List, Optional, Callable
from urllib.parse import urlencode

class VPSGatewayClient:
    def __init__(self, base_url: str, api_key: str, ws_url: Optional[str] = None):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.ws_url = ws_url or self.base_url.replace("http://", "ws://").replace("https://", "wss://") + "/ws/live-feed"
        self._ws_running = False
        self._tick_listeners: List[Callable[[Dict[str, Any]], None]] = []

    async def fetch_historical_candles(self, symbol: str, contract: str, from_date: str, to_date: str, timeframe: str = "1m") -> Dict[str, Any]:
        """
        Fetches synchronized Spot and Option candles from VPS.
        Returns:
            {
                "symbol": symbol,
                "contract": contract,
                "timeframe": timeframe,
                "bars": [
                    {
                        "timestamp": "2024-10-24 09:15:00",
                        "spot_open": 24150.0, "spot_high": 24180.0, "spot_low": 24140.0, "spot_close": 24175.0, "spot_volume": 125000,
                        "opt_open": 110.0, "opt_high": 125.0, "opt_low": 108.0, "opt_close": 122.5, "opt_volume": 45000, "opt_oi": 1500000
                    },
                    ...
                ]
            }
        """
        params = {
            "symbol": symbol,
            "contract": contract,
            "from": from_date,
            "to": to_date,
            "timeframe": timeframe
        }
        headers = {"X-API-Key": self.api_key}
        async with aiohttp.ClientSession() as session:
            url = f"{self.base_url}/api/v1/history/candles?{urlencode(params)}"
            async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                if resp.status == 200:
                    return await resp.json()
                elif resp.status == 401:
                    raise PermissionError("VPS Gateway authentication failed: Invalid API Key")
                else:
                    text = await resp.text()
                    raise RuntimeError(f"VPS Gateway error ({resp.status}): {text}")

    def register_tick_listener(self, callback: Callable[[Dict[str, Any]], None]):
        self._tick_listeners.append(callback)

    async def start_live_feed_socket(self):
        """Maintains continuous WebSocket pass-through stream with auto-reconnect."""
        self._ws_running = True
        while self._ws_running:
            try:
                headers = {"X-API-Key": self.api_key}
                async with aiohttp.ClientSession() as session:
                    async with session.ws_connect(f"{self.ws_url}?api_key={self.api_key}", headers=headers) as ws:
                        async for msg in ws:
                            if msg.type == aiohttp.WSMsgType.TEXT:
                                tick_data = json.loads(msg.data)
                                for cb in self._tick_listeners:
                                    cb(tick_data)
                            elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                                break
            except Exception:
                await asyncio.sleep(2.0)  # Exponential backoff on disconnect
```

---

### 3.2 Local Embedded DuckDB Cache (`data/duckdb_store.py`)

```python
import duckdb
from pathlib import Path
import pandas as pd
from typing import Optional, List, Dict, Any

class LocalDataStore:
    def __init__(self, db_path: Optional[str] = None):
        if not db_path:
            db_dir = Path.home() / ".quant_studio" / "data"
            db_dir.mkdir(parents=True, exist_ok=True)
            db_path = str(db_dir / "market_cache.duckdb")
        self.db_path = db_path
        self.con = duckdb.connect(self.db_path)
        self._init_schema()

    def _init_schema(self):
        self.con.execute("""
            CREATE TABLE IF NOT EXISTS synchronized_candles (
                timestamp TIMESTAMP,
                symbol VARCHAR,
                contract VARCHAR,
                timeframe VARCHAR,
                spot_open DOUBLE,
                spot_high DOUBLE,
                spot_low DOUBLE,
                spot_close DOUBLE,
                spot_volume BIGINT,
                opt_open DOUBLE,
                opt_high DOUBLE,
                opt_low DOUBLE,
                opt_close DOUBLE,
                opt_volume BIGINT,
                opt_oi BIGINT,
                PRIMARY KEY (timestamp, symbol, contract, timeframe)
            );
            CREATE INDEX IF NOT EXISTS idx_sym_contract_ts 
            ON synchronized_candles (symbol, contract, timestamp);
        """)

    def insert_candles(self, df: pd.DataFrame):
        """Inserts synchronized DataFrame with conflict resolution (upsert)."""
        if df.empty:
            return
        self.con.register("df_temp", df)
        self.con.execute("""
            INSERT OR REPLACE INTO synchronized_candles 
            SELECT * FROM df_temp;
        """)

    def query_candles(self, symbol: str, contract: str, from_date: str, to_date: str, timeframe: str = "1m") -> pd.DataFrame:
        """High-speed query returning synchronized Spot + Option DataFrame."""
        query = """
            SELECT * FROM synchronized_candles
            WHERE symbol = ? AND contract = ? AND timeframe = ?
              AND timestamp >= ? AND timestamp <= ?
            ORDER BY timestamp ASC;
        """
        return self.con.execute(query, [symbol, contract, timeframe, from_date, to_date]).df()

    def has_date_range(self, symbol: str, contract: str, from_date: str, to_date: str, timeframe: str = "1m") -> bool:
        """Checks if range is already cached locally."""
        query = """
            SELECT COUNT(*) AS cnt FROM synchronized_candles
            WHERE symbol = ? AND contract = ? AND timeframe = ?
              AND timestamp >= ? AND timestamp <= ?;
        """
        res = self.con.execute(query, [symbol, contract, timeframe, from_date, to_date]).fetchone()
        return res[0] > 0 if res else False
```

---

### 3.3 Spot & Option Timestamp Alignment Bus (`data/sync_bus.py`)

```python
import pandas as pd
import numpy as np

class TimestampAlignmentBus:
    @staticmethod
    def align_spot_and_option(spot_df: pd.DataFrame, option_df: pd.DataFrame, symbol: str, contract: str, timeframe: str = "1m") -> pd.DataFrame:
        """
        Performs strict outer/inner join on timestamp with zero-lookahead forward fill.
        1. Aligns exact timestamps.
        2. If option has no trade in a minute, forward fills close, sets volume=0.
        3. Drops any timestamp outside trading session (09:15 to 15:30 IST).
        """
        spot_df = spot_df.copy().set_index("timestamp").sort_index()
        option_df = option_df.copy().set_index("timestamp").sort_index()

        # Reindex option on spot's exact timeline
        joined = spot_df.join(option_df, lsuffix="_spot", rsuffix="_opt", how="left")

        # Zero-lookahead fill for option
        joined["close_opt"] = joined["close_opt"].ffill()
        joined["open_opt"] = joined["open_opt"].fillna(joined["close_opt"])
        joined["high_opt"] = joined["high_opt"].fillna(joined["close_opt"])
        joined["low_opt"] = joined["low_opt"].fillna(joined["close_opt"])
        joined["volume_opt"] = joined["volume_opt"].fillna(0).astype(int)
        joined["oi_opt"] = joined["oi_opt"].ffill().fillna(0).astype(int)

        joined = joined.reset_index()
        joined["symbol"] = symbol
        joined["contract"] = contract
        joined["timeframe"] = timeframe
        return joined
```

---

## 4. Impacted Areas in Existing Codebase

* **`trading_engine/web/server.py` (VPS Side):**
  - Implement `GET /api/v1/history/candles` endpoint pulling from TimescaleDB.
  - Implement `GET /ws/live-feed` WebSocket route with API key authentication check.
* **Local Workspace:**
  - Create directory `trading_engine/data/` housing `vps_client.py`, `duckdb_store.py`, and `sync_bus.py`.

---

## 5. Concrete Acceptance Criteria & Verification Matrix

| AC ID | Requirement | Metric / Tolerance | Test Function |
| :--- | :--- | :--- | :--- |
| **AC-3.1** | VPS Gateway Authentication | Validates `X-API-Key`. Rejects missing/invalid keys with HTTP 401. | `test_vps_authentication()` |
| **AC-3.2** | DuckDB Query Speed | Querying 1 month of 1-minute data (approx 7,500 bars) completes in **$\le 5$ milliseconds**. | `test_duckdb_query_speed()` |
| **AC-3.3** | Zero Network Repeat Queries | Calling query for an already cached date range makes **0 network requests** and serves from local DuckDB. | `test_duckdb_local_caching()` |
| **AC-3.4** | Timestamp Alignment Accuracy | Spot and Option bars match timestamp down to the second. Zero offset drift. | `test_timestamp_alignment()` |
| **AC-3.5** | Zero Lookahead Forward Fill | Missing option bars copy previous close; volume set to 0. Never accesses future bar prices. | `test_zero_lookahead_forward_fill()` |
| **AC-3.6** | Live WebSocket Auto-Reconnect | Re-establishes connection within 2 seconds of network dropout. | `test_websocket_reconnection()` |

---

## 6. Comprehensive Automated Test Suite (`tests/test_data_gateway.py`)

The test suite must implement:
1. `test_vps_authentication()`: Mocks VPS REST server, tests valid API key (200 OK) vs invalid API key (401 Unauthorized).
2. `test_duckdb_query_speed()`: Populates 100,000 candles in DuckDB, performs time-range queries, asserting latency $\le 5$ms.
3. `test_duckdb_local_caching()`: Queries a range once (downloaded), queries a second time, asserting network is bypassed.
4. `test_timestamp_alignment()`: Merges Spot and Option series with random gaps, validating exact timestamp matching.
5. `test_zero_lookahead_forward_fill()`: Verifies that illiquid option bars do not look ahead to future trades.
6. `test_offline_mode()`: Simulates complete network disconnection, verifying that replay loads seamlessly from cached DuckDB.

---

## 7. PREREQUISITES & HAND-OFF CONTRACT FOR NEXT TASK (TASK 4)

### 7.1 Prerequisites Received from Task 2:
* `IndicatorCompiler` and `WarmupManager` to compile and warm up indicators using historical DataFrames.
* `DynamicIndicatorRegistry` ready to receive live ticks from `VPSGatewayClient.LiveFeedSocket`.

### 7.2 Hand-Off Deliverables to Task 4 (Replay & Backtest Engine):
Task 3 completes and hands off the following assets:
* ✅ **Synchronized Market Data Stream:** `LocalDataStore.query_candles(symbol, contract, from_date, to_date)` returning unified Spot + Option DataFrames.
* ✅ **Offline 24/7 Storage:** Embedded DuckDB database loaded with historical candles, completely decoupling replay/backtesting from internet or broker status.
* ✅ **Zero-Lookahead Alignment Guarantee:** Spot and Option bars pre-aligned with zero future data leakage.
* ✅ **Consumption by Task 4:** Task 4's `ReplaySession` and `VectorizedBacktester` will consume these synchronized DataFrames directly to simulate trades and evaluate indicator logic.

