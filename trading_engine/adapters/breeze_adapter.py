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
from typing import Dict, List, Optional, Any, Callable

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

    def reset(self):
        """Resets cached client instance for hot-reload on credential update."""
        global _breeze_client
        _breeze_client = None
        self.client = None
        self.is_connected = False


default_breeze_adapter = BreezeBrokerAdapter()
BreezeAdapter = BreezeBrokerAdapter
