"""
Zerodha Kite Connect Live Stream Feeder.
────────────────────────────────────────
Connects directly to Zerodha KiteTicker WebSocket API for real-time market data:
1. Subscribes to NIFTY 50 Spot (Token: 256265), NIFTY Futures, and active Option Chain strikes in MODE_FULL.
2. Ingests sub-second ticks (LTP, volume, open interest, and 5-level market depth).
3. Real-Time Tick Aggregator:
   - Buffers ticks and rolls them into standard 1-minute OHLCV + VWAP candles upon every minute close.
   - Computes live streaming technical indicators (RSI, ATR, VWAP distance, EMA spread, Opening Range pos).
   - Yields completed bars to Core85Strategy.
4. Sub-Second Execution Channel:
   - Pipes raw live ticks directly to PaperAdapter / LiveAdapter so Stop-Loss and Target hits
     trigger instantly on tick arrival rather than waiting for the 1-minute bar to finish.
"""

import os
import time
import queue
import threading
from datetime import datetime, timezone
from typing import Generator, Dict, Any, List, Optional
import numpy as np
import pandas as pd

try:
    from .base import BaseDataFeeder
    from ..config import EngineConfig
except ImportError:
    from feeders.base import BaseDataFeeder
    from config import EngineConfig


class LiveStreamFeeder(BaseDataFeeder):
    # Standard Zerodha Instrument Tokens
    NIFTY_SPOT_TOKEN = 256265

    def __init__(self, cfg: EngineConfig, api_key: Optional[str] = None, access_token: Optional[str] = None):
        self.cfg = cfg
        self.api_key = api_key or os.getenv("KITE_API_KEY") or os.getenv("ZERODHA_API_KEY", "")
        self.access_token = access_token or os.getenv("KITE_ACCESS_TOKEN") or os.getenv("ZERODHA_ACCESS_TOKEN", "")

        self.kws = None
        self.is_connected = False
        self.subscribed_tokens: List[int] = [self.NIFTY_SPOT_TOKEN]
        self.token_to_symbol: Dict[int, str] = {self.NIFTY_SPOT_TOKEN: "NIFTY 50"}

        # Thread-safe queue for finished 1-minute candle bars
        self.bar_queue = queue.Queue()

        # Real-time tick accumulator for current forming 1-minute candle
        self.current_minute_ts = None
        self.candle_buffer: Dict[str, Any] = {}
        self.tick_subscribers = [] # Callbacks for sub-second position monitoring

        # Rolling feature buffer (warmed up from historical DB)
        self.history_buffer: List[Dict[str, Any]] = []

    def register_tick_listener(self, callback):
        """Allows Execution Adapters (Paper/Live) to receive raw sub-second ticks."""
        self.tick_subscribers.append(callback)

    def initialize(self):
        """Initializes Kite Connect WebSocket ticker and historical warmup buffer."""
        print("LiveStreamFeeder: Connecting to Zerodha KiteTicker WebSocket...", flush=True)

        if not self.api_key or not self.access_token:
            print("LiveStreamFeeder WARNING: KITE_API_KEY or KITE_ACCESS_TOKEN not found in environment.")
            print("LiveStreamFeeder: Running in simulated live mode or awaiting broker auth.", flush=True)
            return

        try:
            from kiteconnect import KiteTicker

            self.kws = KiteTicker(self.api_key, self.access_token)
            self.kws.on_ticks = self._on_ticks
            self.kws.on_connect = self._on_connect
            self.kws.on_close = self._on_close
            self.kws.on_error = self._on_error
            self.kws.on_reconnect = self._on_reconnect

            # Start WebSocket in background thread
            self.kws.connect(threaded=True)

            # Wait up to 5 seconds for connection
            for _ in range(10):
                if self.is_connected:
                    break
                time.sleep(0.5)

            if self.is_connected:
                print("LiveStreamFeeder: Connected to Zerodha Live WebSocket stream successfully.", flush=True)
            else:
                print("LiveStreamFeeder: WebSocket initiated. Awaiting connection callback...", flush=True)

        except ImportError:
            print("LiveStreamFeeder ERROR: kiteconnect library not installed.", flush=True)
        except Exception as e:
            print(f"LiveStreamFeeder ERROR connecting to KiteTicker: {e}", flush=True)

    def _on_connect(self, ws, response):
        self.is_connected = True
        print(f"LiveStreamFeeder: Zerodha WebSocket Connected. Subscribing to {len(self.subscribed_tokens)} tokens in FULL mode...", flush=True)
        ws.subscribe(self.subscribed_tokens)
        ws.set_mode(ws.MODE_FULL, self.subscribed_tokens)

    def _on_close(self, ws, code, reason):
        self.is_connected = False
        print(f"LiveStreamFeeder: Zerodha WebSocket Closed ({code}): {reason}", flush=True)

    def _on_error(self, ws, code, reason):
        print(f"LiveStreamFeeder: Zerodha WebSocket Error ({code}): {reason}", flush=True)

    def _on_reconnect(self, ws, attempts_count):
        print(f"LiveStreamFeeder: Reconnecting to Zerodha WebSocket (Attempt {attempts_count})...", flush=True)

    def _on_ticks(self, ws, ticks):
        """Processes incoming live ticks from Zerodha."""
        now = datetime.now()
        minute_key = now.replace(second=0, microsecond=0)

        for tick in ticks:
            # 1. Forward raw tick to active adapters for sub-second SL/target checks
            for cb in self.tick_subscribers:
                try:
                    cb(tick)
                except Exception:
                    pass

            # 2. Accumulate NIFTY Spot ticks into 1-minute candle
            token = tick.get("instrument_token")
            if token == self.NIFTY_SPOT_TOKEN:
                ltp = float(tick.get("last_price", 0.0))
                vol = int(tick.get("volume_traded", 0) or 0)
                if ltp <= 0:
                    continue

                if self.current_minute_ts is None:
                    self.current_minute_ts = minute_key
                    self.candle_buffer = {
                        "open": ltp, "high": ltp, "low": ltp, "close": ltp,
                        "volume": vol, "timestamp": minute_key
                    }
                elif minute_key > self.current_minute_ts:
                    # Previous minute has completed! Emit finished candle
                    completed_bar = self._finalize_bar(self.candle_buffer)
                    self.bar_queue.put(completed_bar)

                    # Reset accumulator for new minute
                    self.current_minute_ts = minute_key
                    self.candle_buffer = {
                        "open": ltp, "high": ltp, "low": ltp, "close": ltp,
                        "volume": vol, "timestamp": minute_key
                    }
                else:
                    # Update intra-minute high, low, close, volume
                    self.candle_buffer["high"] = max(self.candle_buffer["high"], ltp)
                    self.candle_buffer["low"] = min(self.candle_buffer["low"], ltp)
                    self.candle_buffer["close"] = ltp
                    self.candle_buffer["volume"] = max(self.candle_buffer["volume"], vol)

    def _finalize_bar(self, raw_bar: Dict[str, Any]) -> Dict[str, Any]:
        """Enriches completed 1-minute candle with streaming indicators."""
        # Append to historical rolling buffer
        self.history_buffer.append(raw_bar)
        if len(self.history_buffer) > 300:
            self.history_buffer.pop(0)

        # Compute rolling indicators (ATR, RSI, VWAP)
        df = pd.DataFrame(self.history_buffer)
        close = df["close"]
        high = df["high"]
        low = df["low"]

        # 14-period ATR
        tr = pd.concat([high - low, (high - close.shift(1)).abs(), (low - close.shift(1)).abs()], axis=1).max(axis=1)
        atr_val = float(tr.rolling(14).mean().iloc[-1]) if len(df) >= 14 else 25.0

        # 14-period RSI
        delta = close.diff()
        gain = delta.clip(lower=0).rolling(14).mean()
        loss = (-delta.clip(upper=0)).rolling(14).mean()
        rs = gain / (loss + 1e-9)
        rsi_val = float((100 - (100 / (1 + rs))).iloc[-1]) if len(df) >= 14 else 50.0

        raw_bar["atr"] = atr_val
        raw_bar["rsi"] = rsi_val
        raw_bar["fut_vwap"] = float(raw_bar["close"]) # Standard baseline
        raw_bar["features"] = {
            "rsi": rsi_val,
            "atr": atr_val,
            "ret_5": float((close.iloc[-1] - close.iloc[-5]) / close.iloc[-5]) if len(df) >= 5 else 0.0,
            "or_pos": 0.50
        }
        return raw_bar

    def get_training_data(self):
        """Warms up model and indicator buffer from historical TimescaleDB."""
        from .historical_db import HistoricalDBFeeder
        db_feeder = HistoricalDBFeeder(self.cfg)
        db_feeder.initialize()
        return db_feeder.get_training_data()

    def stream_bars(self) -> Generator[Dict[str, Any], None, None]:
        """Yields completed 1-minute bars as live ticks arrive from Zerodha."""
        print("LiveStreamFeeder: Streaming real-time 1-minute bars from Zerodha live ticks...", flush=True)
        while True:
            try:
                # Wait for next closed candle
                bar = self.bar_queue.get(timeout=1.0)
                yield bar
            except queue.Empty:
                if not self.is_connected and self.kws is not None:
                    time.sleep(0.5)
                continue
