"""
ICICI Direct Breeze Broker Adapter
───────────────────────────────────
Full-featured broker integration for ICICI Direct Breeze API.
Handles authentication, 1-minute historical candle catch-up, quotes, and live feeds.
"""

import os
import sys
import logging
from datetime import datetime, date
from typing import Dict, List, Optional, Any, Callable, Tuple, Set

from trading_engine.core.keyvault import get_secret
from trading_engine.adapters.symbol_translator import to_breeze_params

logger = logging.getLogger("breeze_adapter")

_breeze_client = None


class BreezeBrokerAdapter:
    """
    Adapter encapsulating ICICI Direct BreezeConnect SDK.
    """

    def __init__(self):
        self.client = None
        self.is_connected = False
        self._subscribed_symbols = set()
        self._tick_callbacks: List[Callable[[Dict[str, Any]], None]] = []
        self._token_to_symbol: Dict[str, str] = {}
        self._symbol_to_tokens: Dict[str, Tuple[str, str]] = {}
        self.ws_connected = False


    def is_configured(self) -> bool:
        """Returns True if all required Breeze credentials exist in KeyVault."""
        api_key = get_secret("BREEZE_API_KEY")
        api_secret = get_secret("BREEZE_API_SECRET")
        session_token = get_secret("BREEZE_SESSION_TOKEN")
        return bool(api_key and api_secret and session_token and len(api_key.strip()) > 3 and len(session_token.strip()) > 3)

    def connect(self) -> Optional[Any]:
        """Initializes BreezeConnect and generates daily session."""
        global _breeze_client
        if _breeze_client is not None:
            self.client = _breeze_client
            self.is_connected = True
            return _breeze_client

        if not self.is_configured():
            logger.warning("BreezeBrokerAdapter: Incomplete credentials in KeyVault.")
            return None

        try:
            from breeze_connect import BreezeConnect
        except ImportError:
            logger.error("BreezeBrokerAdapter: 'breeze-connect' package not installed.")
            return None

        api_key = get_secret("BREEZE_API_KEY")
        api_secret = get_secret("BREEZE_API_SECRET")
        session_token = get_secret("BREEZE_SESSION_TOKEN")

        try:
            breeze = BreezeConnect(api_key=api_key.strip())
            res = breeze.generate_session(api_secret=api_secret.strip(), session_token=session_token.strip())
            logger.info(f"BreezeBrokerAdapter: Session generated: {res}")
            _breeze_client = breeze
            self.client = breeze
            self.is_connected = True
            return breeze
        except Exception as e:
            logger.error(f"BreezeBrokerAdapter: Failed to generate session: {e}")
            self.is_connected = False
            return None

    def get_historical_candles(
        self,
        symbol: str,
        from_date: str,
        to_date: str,
        interval: str = "1minute"
    ) -> List[Dict[str, Any]]:
        """
        Fetches historical candles from Breeze API and converts to canonical format:
        [{ "time": unix_seconds, "open": float, "high": float, "low": float, "close": float, "volume": int }]
        """
        breeze = self.connect()
        if not breeze:
            return []

        params = to_breeze_params(symbol)
        if not params:
            logger.warning(f"BreezeBrokerAdapter: Could not parse symbol {symbol}")
            return []

        try:
            # Format dates to Breeze ISO format if needed
            from_iso = from_date if "T" in from_date else f"{from_date}T09:15:00.000Z"
            to_iso = to_date if "T" in to_date else f"{to_date}T15:30:00.000Z"

            raw_res = breeze.get_historical_data_v2(
                interval=interval,
                from_date=from_iso,
                to_date=to_iso,
                stock_code=params["stock_code"],
                exchange_code=params["exchange_code"],
                product_type=params["product_type"],
                strike_price=params["strike_price"],
                right=params["right"],
                expiry_date=params["expiry_date"]
            )

            if not raw_res or "Success" not in raw_res or not raw_res["Success"]:
                return []

            standardized = []
            for row in raw_res["Success"]:
                dt_str = row.get("datetime")
                if not dt_str:
                    continue
                # Parse datetime e.g. "2026-10-22 09:15:00"
                try:
                    dt = datetime.strptime(dt_str, "%Y-%m-%d %H:%M:%S")
                    ts = int(dt.timestamp())
                except Exception:
                    continue

                standardized.append({
                    "time": ts,
                    "open": float(row.get("open", 0.0)),
                    "high": float(row.get("high", 0.0)),
                    "low": float(row.get("low", 0.0)),
                    "close": float(row.get("close", 0.0)),
                    "volume": int(row.get("volume", 0)),
                })

            standardized.sort(key=lambda x: x["time"])
            return standardized
        except Exception as e:
            logger.error(f"BreezeBrokerAdapter: Historical candle fetch error for {symbol}: {e}")
            return []

    def get_quote(self, symbol: str) -> Optional[Dict[str, Any]]:
        """Fetches live quote snapshot for a contract."""
        breeze = self.connect()
        if not breeze:
            return None

        params = to_breeze_params(symbol)
        if not params:
            return None

        try:
            raw_quote = breeze.get_quotes(
                stock_code=params["stock_code"],
                exchange_code=params["exchange_code"],
                product_type=params["product_type"],
                strike_price=params["strike_price"],
                right=params["right"],
                expiry_date=params["expiry_date"]
            )

            if not raw_quote or "Success" not in raw_quote or not raw_quote["Success"]:
                # Fallback: Query live option chain quotes for this strike if expiry date mismatched
                if params.get("product_type") == "options" and params.get("strike_price"):
                    chain = breeze.get_option_chain_quotes(
                        stock_code=params["stock_code"],
                        exchange_code=params["exchange_code"],
                        product_type=params["product_type"],
                        strike_price=str(params["strike_price"]),
                        right=params["right"]
                    )
                    if chain and chain.get("Success"):
                        raw_quote = chain

            if not raw_quote or "Success" not in raw_quote or not raw_quote["Success"]:
                return None

            q = raw_quote["Success"][0]
            ltp = float(q.get("ltp") or q.get("last_traded_price") or 0.0)
            return {
                "symbol": symbol,
                "price": ltp,
                "high": float(q.get("high", 0.0) or ltp),
                "low": float(q.get("low", 0.0) or ltp),
                "open": float(q.get("open", 0.0) or ltp),
                "close": float(q.get("close", 0.0) or ltp),
                "volume": int(q.get("total_quantity_traded", 0) or 0),
                "timestamp": datetime.now().isoformat()
            }
        except Exception as e:
            logger.error(f"BreezeBrokerAdapter: Quote error for {symbol}: {e}")
            return None

    def register_tick_callback(self, callback: Callable[[Dict[str, Any]], None]):
        """Registers a callback function to receive normalized live ticks from broker WebSocket."""
        if callback not in self._tick_callbacks:
            self._tick_callbacks.append(callback)

    def unregister_tick_callback(self, callback: Callable[[Dict[str, Any]], None]):
        """Unregisters a previously registered callback."""
        if callback in self._tick_callbacks:
            self._tick_callbacks.remove(callback)

    def start_websocket(self, on_tick_callback: Optional[Callable[[Dict[str, Any]], None]] = None) -> bool:
        """
        Connects to ICICI Direct Breeze streaming WebSocket.
        Subscribes to live price refresh feeds with ZERO REST calls.
        """
        breeze = self.connect()
        if not breeze:
            logger.warning("BreezeBrokerAdapter: Cannot start WebSocket, client not connected.")
            return False

        if on_tick_callback:
            self.register_tick_callback(on_tick_callback)

        try:
            # Ensure SecurityMaster token maps are loaded
            if not getattr(breeze, "stock_script_dict_list", None) or not breeze.stock_script_dict_list[0]:
                logger.info("BreezeBrokerAdapter: Loading security master script mappings...")
                breeze.get_stock_script_list()

            breeze.interval = ""
            breeze.on_ticks = self._handle_incoming_breeze_tick

            if not self.ws_connected:
                logger.info("BreezeBrokerAdapter: Initializing ws_connect() streaming socket...")
                breeze.ws_connect()
                self.ws_connected = True
                logger.info("BreezeBrokerAdapter: Broker WebSocket connected successfully.")

            # Re-subscribe any previously registered symbols
            for sym in list(self._subscribed_symbols):
                self.subscribe_feeds(sym)

            return True
        except Exception as e:
            logger.error(f"BreezeBrokerAdapter: Failed to start WebSocket stream: {e}")
            self.ws_connected = False
            return False

    def subscribe_feeds(self, symbol: str) -> bool:
        """
        Subscribes to live feeds for a canonical symbol using WebSocket.
        Uses 0 REST API calls.
        """
        breeze = self.connect()
        if not breeze:
            return False

        symbol = symbol.strip().upper()
        if symbol in self._subscribed_symbols and symbol in self._symbol_to_tokens:
            return True

        # Fast-path for spot indices
        if symbol in ("SENSEX", "BSESEN", "BSE:SENSEX", "BSE SENSEX"):
            quotes_tok = "1.1!SENSEX"
            depth_tok = "1.2!SENSEX"
            self._symbol_to_tokens[symbol] = (quotes_tok, depth_tok)
            self._token_to_symbol[quotes_tok] = "SENSEX"
            self._token_to_symbol["1.1!SENSEX"] = "SENSEX"
            self._token_to_symbol["1.2!SENSEX"] = "SENSEX"
            self._token_to_symbol["SENSEX"] = "SENSEX"
            self._token_to_symbol["BSE SENSEX"] = "SENSEX"
            if hasattr(breeze, "sio_rate_refresh_handler") and breeze.sio_rate_refresh_handler:
                breeze.sio_rate_refresh_handler.watch(quotes_tok)
            self._subscribed_symbols.add(symbol)
            logger.info(f"BreezeBrokerAdapter: Subscribed SENSEX spot feed (token={quotes_tok})")
            return True

        params = to_breeze_params(symbol)
        if not params:
            logger.warning(f"BreezeBrokerAdapter: Could not parse symbol {symbol} for subscription.")
            return False

        try:
            # Format expiry date for security master: DD-Mon-YYYY (e.g. 29-Oct-2026)
            expiry_str = ""
            from trading_engine.adapters.symbol_translator import parse_canonical_symbol
            parsed = parse_canonical_symbol(symbol)
            if parsed and parsed.get("expiry_date"):
                expiry_str = parsed["expiry_date"].strftime("%d-%b-%Y")

            breeze.interval = ""
            quotes_tok, depth_tok = breeze.get_stock_token_value(
                exchange_code=params["exchange_code"],
                stock_code=params["stock_code"],
                product_type=params["product_type"],
                expiry_date=expiry_str,
                strike_price=str(params["strike_price"]),
                right=params["right"],
                get_exchange_quotes=True,
                get_market_depth=True
            )

            if isinstance(quotes_tok, Exception) or not quotes_tok or "False" in str(quotes_tok):
                # Resilient fallback from breeze.stock_script_dict_list
                exc = params.get("exchange_code", "NFO").upper()
                idx = 5 if exc == "BFO" else 4
                prefix_code = "8." if exc == "BFO" else "4."
                stk = params["stock_code"]
                strike_val = int(float(params["strike_price"])) if params.get("strike_price") else 0
                r_code = "CE" if "call" in str(params.get("right", "")).lower() else "PE"
                
                script_dict = getattr(breeze, "stock_script_dict_list", [{},{},{},{},{},{}])[idx]
                prefix = f"OPT-{stk}-"
                suffix = f"-{strike_val}-{r_code}"
                matches = [(k, v) for k, v in script_dict.items() if k.startswith(prefix) and k.endswith(suffix)]
                
                chosen_token = None
                if matches:
                    if expiry_str:
                        for k, v in matches:
                            if expiry_str.lower() in k.lower():
                                chosen_token = v
                                break
                    if not chosen_token:
                        chosen_token = matches[0][1]
                
                if chosen_token:
                    quotes_tok = f"{prefix_code}1!{chosen_token}"
                    depth_tok = f"{prefix_code}2!{chosen_token}"
                    logger.info(f"BreezeBrokerAdapter: Resiliently matched token for {symbol} -> {quotes_tok}")
                else:
                    logger.warning(f"BreezeBrokerAdapter: get_stock_token_value error for {symbol}: {quotes_tok}")
                    return False

            # Store bidirectional mapping
            self._symbol_to_tokens[symbol] = (str(quotes_tok), str(depth_tok))
            self._token_to_symbol[str(quotes_tok)] = symbol
            if depth_tok:
                self._token_to_symbol[str(depth_tok)] = symbol
            if "!" in str(quotes_tok):
                self._token_to_symbol[str(quotes_tok).split("!")[-1]] = symbol

            if hasattr(breeze, "sio_rate_refresh_handler") and breeze.sio_rate_refresh_handler:
                breeze.sio_rate_refresh_handler.watch(quotes_tok)
                if depth_tok:
                    breeze.sio_rate_refresh_handler.watch(depth_tok)

            self._subscribed_symbols.add(symbol)
            logger.info(f"BreezeBrokerAdapter: Subscribed to WebSocket feeds for {symbol} (token={quotes_tok})")
            return True
        except Exception as e:
            logger.error(f"BreezeBrokerAdapter: Exception subscribing to {symbol}: {e}")
            return False

    def unsubscribe_feeds(self, symbol: str) -> bool:
        """Unsubscribes from WebSocket feeds for a canonical symbol."""
        breeze = self.connect()
        if not breeze:
            return False

        symbol = symbol.strip().upper()
        tokens = self._symbol_to_tokens.pop(symbol, None)
        self._subscribed_symbols.discard(symbol)

        if tokens and hasattr(breeze, "sio_rate_refresh_handler") and breeze.sio_rate_refresh_handler:
            quotes_tok, depth_tok = tokens
            try:
                breeze.sio_rate_refresh_handler.unwatch(quotes_tok)
                if depth_tok:
                    breeze.sio_rate_refresh_handler.unwatch(depth_tok)
                self._token_to_symbol.pop(str(quotes_tok), None)
                if depth_tok:
                    self._token_to_symbol.pop(str(depth_tok), None)
            except Exception as e:
                logger.error(f"BreezeBrokerAdapter: Error unwatching {symbol}: {e}")

        logger.info(f"BreezeBrokerAdapter: Unsubscribed from {symbol}")
        return True

    def _handle_incoming_breeze_tick(self, data: Dict[str, Any]):
        """
        Receives real-time streaming tick dict from Breeze socket.io client.
        Normalizes tick and dispatches immediately to all registered callbacks with 0 latency.
        """
        if not data or not isinstance(data, dict):
            return

        # 1. Resolve canonical symbol
        sym_token = str(data.get("symbol") or "")
        token_num = sym_token.split("!")[-1] if "!" in sym_token else sym_token
        canonical_symbol = self._token_to_symbol.get(sym_token) or self._token_to_symbol.get(token_num)

        if not canonical_symbol:
            if sym_token in ("BSE SENSEX", "SENSEX", "BSESEN"):
                canonical_symbol = "SENSEX"
            elif sym_token in ("NSE NIFTY", "NIFTY 50", "NIFTY"):
                canonical_symbol = "NIFTY"
            elif sym_token in ("NIFTY BANK", "BANKNIFTY"):
                canonical_symbol = "BANKNIFTY"
            else:
                stk_name = data.get("stock_name") or data.get("stock_code")
                strike = data.get("strike_price")
                right = data.get("right")
                exp = data.get("expiry_date")
                if stk_name and strike and right:
                    from trading_engine.adapters.symbol_translator import to_canonical_symbol
                    canonical_symbol = to_canonical_symbol(stk_name, strike, right, exp)
                elif stk_name:
                    canonical_symbol = stk_name

        if not canonical_symbol:
            return

        # 2. Extract price
        ltp = float(data.get("last") or data.get("last_trade_price") or data.get("close") or 0.0)
        if ltp <= 0:
            return

        vol = int(data.get("ttq") or data.get("total_traded_volume") or data.get("volume") or 0)
        oi = int(data.get("OI") or data.get("open_interest_value") or data.get("open_interest") or 0)
        high = float(data.get("high") or ltp)
        low = float(data.get("low") or ltp)
        open_px = float(data.get("open") or ltp)
        close_px = float(data.get("close") or ltp)
        ltt = data.get("ltt") or data.get("last_traded_time") or datetime.now().isoformat()

        tick_packet = {
            "symbol": canonical_symbol,
            "price": round(ltp, 2),
            "volume": vol,
            "oi": oi,
            "high": round(high, 2),
            "low": round(low, 2),
            "open": round(open_px, 2),
            "close": round(close_px, 2),
            "timestamp": str(ltt),
            "source": "breeze_websocket"
        }

        # 3. Zero-latency dispatch to listeners
        for cb in list(self._tick_callbacks):
            try:
                cb(tick_packet)
            except Exception as e:
                logger.error(f"BreezeBrokerAdapter: Error in tick listener callback: {e}")

    def reset(self):
        """Resets cached client instance for hot-reload on credential update."""
        global _breeze_client
        if _breeze_client and hasattr(_breeze_client, "ws_disconnect"):
            try:
                _breeze_client.ws_disconnect()
            except Exception:
                pass
        _breeze_client = None
        self.client = None
        self.is_connected = False
        self.ws_connected = False
        self._subscribed_symbols.clear()
        self._token_to_symbol.clear()
        self._symbol_to_tokens.clear()



default_breeze_adapter = BreezeBrokerAdapter()
BreezeAdapter = BreezeBrokerAdapter
