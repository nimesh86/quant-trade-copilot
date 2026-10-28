"""
Adapter encapsulating Zerodha Kite Connect SDK and KiteTicker WebSocket.
Provides unified interface matching BreezeBrokerAdapter for zero-friction switching.
"""
import os
import time
import threading
from typing import Optional, Dict, Any, Callable, List, Tuple
from datetime import datetime

from trading_engine.core.keyvault import get_secret

_kite_client = None


class KiteBrokerAdapter:
    """
    Adapter encapsulating Zerodha KiteConnect & KiteTicker.
    """

    def __init__(self):
        self.client = None
        self.kws = None
        self.is_connected = False
        self.ws_connected = False
        self._subscribed_tokens: set = set()
        self._subscribed_symbols: set = set()
        self._token_to_symbol: Dict[int, str] = {}
        self._symbol_to_token: Dict[str, int] = {}
        self._tick_callbacks: List[Callable[[Dict[str, Any]], None]] = []
        self._last_connect_attempt = 0.0

    def reset(self):
        """Resets adapter and disconnects WebSocket."""
        global _kite_client
        _kite_client = None
        self.client = None
        self.is_connected = False
        self.ws_connected = False
        if self.kws:
            try:
                self.kws.close()
            except Exception:
                pass
            self.kws = None
        self._subscribed_tokens.clear()
        self._subscribed_symbols.clear()
        self._token_to_symbol.clear()
        self._symbol_to_token.clear()

    def is_configured(self) -> bool:
        """Returns True if Kite API Key and Access Token are configured."""
        api_key = get_secret("KITE_API_KEY") or os.getenv("KITE_API_KEY", "")
        access_token = get_secret("KITE_ACCESS_TOKEN") or os.getenv("KITE_ACCESS_TOKEN", "")
        return bool(api_key and access_token and len(api_key.strip()) > 3 and len(access_token.strip()) > 10)

    def connect(self) -> Optional[Any]:
        """Initializes KiteConnect client and validates session."""
        global _kite_client
        if _kite_client is not None and self.is_connected:
            self.client = _kite_client
            return self.client

        api_key = (get_secret("KITE_API_KEY") or os.getenv("KITE_API_KEY", "")).strip()
        access_token = (get_secret("KITE_ACCESS_TOKEN") or os.getenv("KITE_ACCESS_TOKEN", "")).strip()

        if not (api_key and access_token):
            print("KiteBrokerAdapter: Credentials missing in KeyVault.", flush=True)
            self.is_connected = False
            return None

        try:
            from kiteconnect import KiteConnect
            kc = KiteConnect(api_key=api_key)
            kc.set_access_token(access_token)

            # Test connection with profile call
            profile = kc.profile()
            user_name = profile.get("user_name") or profile.get("user_id") or "Trader"
            print(f"KiteBrokerAdapter: Connected successfully to Zerodha Kite ({user_name}).", flush=True)

            self.client = kc
            _kite_client = kc
            self.is_connected = True
            return self.client
        except Exception as e:
            print(f"KiteBrokerAdapter: Failed to connect to Kite: {e}", flush=True)
            self.is_connected = False
            return None

    def start_websocket(self, on_tick_callback: Callable[[Dict[str, Any]], None]) -> bool:
        """Initializes KiteTicker and connects in threaded background mode."""
        if not self.is_connected:
            self.connect()
            if not self.is_connected:
                print("KiteBrokerAdapter: Cannot start WebSocket without authenticated connection.", flush=True)
                return False

        if on_tick_callback not in self._tick_callbacks:
            self._tick_callbacks.append(on_tick_callback)

        if self.kws and self.ws_connected:
            return True

        api_key = (get_secret("KITE_API_KEY") or os.getenv("KITE_API_KEY", "")).strip()
        access_token = (get_secret("KITE_ACCESS_TOKEN") or os.getenv("KITE_ACCESS_TOKEN", "")).strip()

        try:
            from kiteconnect import KiteTicker
            self.kws = KiteTicker(api_key=api_key, access_token=access_token)

            self.kws.on_ticks = self._on_ticks
            self.kws.on_connect = self._on_connect
            self.kws.on_close = self._on_close
            self.kws.on_error = self._on_error
            self.kws.on_reconnect = self._on_reconnect

            self.kws.connect(threaded=True)

            # Wait briefly for connection
            for _ in range(10):
                if self.ws_connected:
                    break
                time.sleep(0.3)

            return self.ws_connected
        except Exception as e:
            print(f"KiteBrokerAdapter: Error starting KiteTicker WebSocket: {e}", flush=True)
            return False

    def _on_connect(self, ws, response):
        self.ws_connected = True
        print(f"KiteBrokerAdapter: KiteTicker WebSocket connected successfully.", flush=True)
        if self._subscribed_tokens:
            tokens_list = list(self._subscribed_tokens)
            ws.subscribe(tokens_list)
            ws.set_mode(ws.MODE_FULL, tokens_list)
            print(f"KiteBrokerAdapter: Subscribed {len(tokens_list)} tokens on connect.", flush=True)

    def _on_close(self, ws, code, reason):
        self.ws_connected = False
        print(f"KiteBrokerAdapter: KiteTicker WebSocket closed ({code}): {reason}", flush=True)

    def _on_error(self, ws, code, reason):
        print(f"KiteBrokerAdapter: KiteTicker WebSocket error ({code}): {reason}", flush=True)

    def _on_reconnect(self, ws, attempts_count):
        print(f"KiteBrokerAdapter: Reconnecting KiteTicker WebSocket (Attempt {attempts_count})...", flush=True)

    def _on_ticks(self, ws, ticks):
        for tick in ticks:
            tok = tick.get("instrument_token")
            symbol = self._token_to_symbol.get(tok)
            if not symbol:
                continue

            ltp = float(tick.get("last_price", 0.0) or 0.0)
            if ltp <= 0:
                continue

            vol = int(tick.get("volume_traded", 0) or tick.get("volume", 0) or 0)
            oi = int(tick.get("oi", 0) or 0)
            tick_data = {
                "symbol": symbol,
                "price": ltp,
                "last": ltp,
                "volume": vol,
                "oi": oi,
                "timestamp": datetime.now().isoformat(),
                "datetime": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            }

            for cb in self._tick_callbacks:
                try:
                    cb(tick_data)
                except Exception:
                    pass

    def _resolve_symbol_token(self, symbol: str) -> Optional[int]:
        """Resolves trading symbol or index to Zerodha instrument token."""
        sym_clean = symbol.strip()
        if sym_clean in self._symbol_to_token:
            return self._symbol_to_token[sym_clean]

        try:
            from trading_engine.web.contract_data_manager import resolve_token_for_kite_symbol, resolve_token
            tok, _ = resolve_token_for_kite_symbol(sym_clean)
            if tok:
                self._symbol_to_token[sym_clean] = tok
                self._token_to_symbol[tok] = sym_clean
                return tok
            tok2 = resolve_token(sym_clean)
            if tok2:
                self._symbol_to_token[sym_clean] = tok2
                self._token_to_symbol[tok2] = sym_clean
                return tok2
        except Exception as e:
            print(f"KiteBrokerAdapter: Token resolution failed for {symbol}: {e}", flush=True)

        return None

    def subscribe_feeds(self, symbol: str) -> bool:
        """Subscribes symbol on KiteTicker."""
        token = self._resolve_symbol_token(symbol)
        if not token:
            return False

        self._subscribed_tokens.add(token)
        self._subscribed_symbols.add(symbol)

        if self.kws and self.ws_connected:
            try:
                self.kws.subscribe([token])
                self.kws.set_mode(self.kws.MODE_FULL, [token])
                print(f"KiteBrokerAdapter: Subscribed {symbol} (token={token}) on live feed.", flush=True)
                return True
            except Exception as e:
                print(f"KiteBrokerAdapter: Subscribe failed for {symbol} (token={token}): {e}", flush=True)
                return False
        return True

    def unsubscribe_feeds(self, symbol: str) -> bool:
        """Unsubscribes symbol on KiteTicker."""
        token = self._symbol_to_token.get(symbol)
        if token:
            self._subscribed_tokens.discard(token)
            self._subscribed_symbols.discard(symbol)
            if self.kws and self.ws_connected:
                try:
                    self.kws.unsubscribe([token])
                    return True
                except Exception:
                    pass
        return False

    def get_spot_quote(self, symbol: str) -> Optional[float]:
        """Fetches spot LTP for symbol from Kite."""
        if not self.is_connected:
            self.connect()
            if not self.is_connected:
                return None

        sym_map = {
            "NIFTY": "NSE:NIFTY 50",
            "NIFTY 50": "NSE:NIFTY 50",
            "BANKNIFTY": "NSE:NIFTY BANK",
            "SENSEX": "BSE:SENSEX",
            "FINNIFTY": "NSE:NIFTY FIN SERVICE",
            "MIDCPNIFTY": "NSE:NIFTY MID SELECT",
        }
        kite_key = sym_map.get(symbol.upper(), f"NSE:{symbol.upper()}")
        try:
            q = self.client.quote([kite_key])
            if kite_key in q and q[kite_key].get("last_price"):
                return float(q[kite_key]["last_price"])
        except Exception:
            pass
        return None


default_kite_adapter = KiteBrokerAdapter()
