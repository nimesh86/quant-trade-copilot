"""
VPS Gateway Client for Quant Studio.
Connects to remote production VPS via API Key authentication for:
1. Historical synchronized candle range queries (cached in local DuckDB).
2. Live WebSocket tick pass-through stream with auto-reconnection.
Prevents direct broker API contact from the local development environment.
"""
import asyncio
import json
import logging
from typing import Dict, Any, List, Optional, Callable, Union
from urllib.parse import urlencode

import aiohttp
import pandas as pd

from trading_engine.data.duckdb_store import LocalDataStore

logger = logging.getLogger("VPSGatewayClient")


class VPSGatewayClient:
    """
    Client for communication with VPS data hub.
    Supports local DuckDB caching to eliminate redundant network roundtrips.
    """

    def __init__(
        self,
        base_url: str,
        api_key: str,
        ws_url: Optional[str] = None,
        data_store: Optional[LocalDataStore] = None
    ):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        if ws_url:
            self.ws_url = ws_url
        else:
            base_ws = self.base_url.replace("http://", "ws://").replace("https://", "wss://")
            self.ws_url = f"{base_ws}/ws/live-feed"

        self.data_store = data_store
        self._ws_running = False
        self._ws_task: Optional[asyncio.Task] = None
        self._tick_listeners: List[Callable[[Dict[str, Any]], None]] = []
        self.network_call_count = 0  # Instrumented to verify caching behavior

    def register_tick_listener(self, callback: Callable[[Dict[str, Any]], None]):
        """Registers a callback to receive incoming live market ticks."""
        if callback not in self._tick_listeners:
            self._tick_listeners.append(callback)

    def unregister_tick_listener(self, callback: Callable[[Dict[str, Any]], None]):
        """Unregisters a previously registered tick listener."""
        if callback in self._tick_listeners:
            self._tick_listeners.remove(callback)

    async def fetch_historical_candles(
        self,
        symbol: str,
        contract: str,
        from_date: str,
        to_date: str,
        timeframe: str = "1m",
        force_refresh: bool = False
    ) -> pd.DataFrame:
        """
        Fetches synchronized Spot and Option candles.
        If local cache contains the range and force_refresh is False, serves directly
        from DuckDB with 0 network calls (AC-3.3).
        Otherwise queries the VPS REST API and caches the result locally.
        """
        if not force_refresh and self.data_store is not None:
            if self.data_store.has_date_range(symbol, contract, from_date, to_date, timeframe):
                cached_df = self.data_store.query_candles(symbol, contract, from_date, to_date, timeframe)
                if not cached_df.empty:
                    return cached_df

        # Cache miss or forced refresh: query VPS Gateway
        self.network_call_count += 1
        params = {
            "symbol": symbol,
            "contract": contract,
            "from": from_date,
            "to": to_date,
            "timeframe": timeframe
        }
        headers = {"X-API-Key": self.api_key}
        url = f"{self.base_url}/api/v1/history/candles?{urlencode(params)}"

        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                if resp.status == 200:
                    payload = await resp.json()
                    bars_data = payload.get("bars", []) if isinstance(payload, dict) else payload
                    if not bars_data:
                        return pd.DataFrame()
                    df = pd.DataFrame(bars_data)
                    df["timestamp"] = pd.to_datetime(df["timestamp"])
                    if "symbol" not in df.columns:
                        df["symbol"] = symbol
                    if "contract" not in df.columns:
                        df["contract"] = contract
                    if "timeframe" not in df.columns:
                        df["timeframe"] = timeframe

                    # Insert into local DuckDB store for future offline queries
                    if self.data_store is not None:
                        self.data_store.insert_candles(df)

                    return df
                elif resp.status == 401:
                    raise PermissionError("VPS Gateway authentication failed: Invalid API Key")
                else:
                    text = await resp.text()
                    raise RuntimeError(f"VPS Gateway error ({resp.status}): {text}")

    async def start_live_feed_socket(self, max_reconnect_attempts: Optional[int] = None):
        """
        Maintains continuous WebSocket pass-through stream with auto-reconnection.
        AC-3.6: Reconnects upon dropout.
        """
        self._ws_running = True
        reconnect_attempts = 0

        while self._ws_running:
            try:
                headers = {"X-API-Key": self.api_key}
                connector_url = f"{self.ws_url}?api_key={self.api_key}"
                async with aiohttp.ClientSession() as session:
                    async with session.ws_connect(
                        connector_url,
                        headers=headers,
                        timeout=aiohttp.ClientTimeout(connect=5.0)
                    ) as ws:
                        reconnect_attempts = 0  # reset on successful connection
                        async for msg in ws:
                            if not self._ws_running:
                                await ws.close()
                                break
                            if msg.type == aiohttp.WSMsgType.TEXT:
                                try:
                                    tick_data = json.loads(msg.data)
                                    for cb in self._tick_listeners:
                                        cb(tick_data)
                                except Exception as parse_err:
                                    logger.error(f"Error parsing tick JSON: {parse_err}")
                            elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                                break
            except asyncio.CancelledError:
                self._ws_running = False
                break
            except Exception as e:
                logger.warning(f"WebSocket disconnected ({e}). Reconnecting in 0.5s...")
                reconnect_attempts += 1
                if max_reconnect_attempts is not None and reconnect_attempts >= max_reconnect_attempts:
                    logger.error(f"Max reconnect attempts ({max_reconnect_attempts}) reached. Stopping live feed.")
                    break
                await asyncio.sleep(0.5)

    def stop_live_feed(self):
        """Stops the live feed WebSocket loop."""
        self._ws_running = False
        if self._ws_task and not self._ws_task.done():
            self._ws_task.cancel()
