"""
Thread-Safe Dynamic Indicator Registry.
Enables atomic, zero-downtime hot-swapping of indicator instances on live tick streams.
"""
import threading
from typing import Dict, List, Optional
from ..quant_lib.base import Indicator


class DynamicIndicatorRegistry:
    """
    In-memory registry maintaining active indicators mapped by symbol and indicator ID.
    Thread-safe under high-frequency tick updates.
    """
    def __init__(self):
        # Maps symbol (e.g. 'NIFTY24OCT24200CE') -> {indicator_id: instance}
        self._indicators: Dict[str, Dict[str, Indicator]] = {}
        self._lock = threading.RLock()

    def mount(self, symbol: str, indicator_id: str, instance: Indicator) -> None:
        """
        Atomically mounts an indicator instance for a symbol.
        Replaces existing instance with the same ID if present.
        """
        with self._lock:
            if symbol not in self._indicators:
                self._indicators[symbol] = {}
            self._indicators[symbol][indicator_id] = instance

    def register(self, symbol: str, instance: Indicator, indicator_id: str = "default") -> None:
        """Convenience alias for mount."""
        self.mount(symbol, indicator_id, instance)

    def unmount(self, symbol: str, indicator_id: str) -> bool:
        """Detaches an indicator from a symbol."""
        with self._lock:
            if symbol in self._indicators and indicator_id in self._indicators[symbol]:
                del self._indicators[symbol][indicator_id]
                if not self._indicators[symbol]:
                    del self._indicators[symbol]
                return True
            return False

    def get_indicators_for_symbol(self, symbol: str) -> List[Indicator]:
        """
        Returns a shallow copy of active indicator instances for a symbol.
        Thread-safe for sub-second tick iteration.
        """
        with self._lock:
            sub_dict = self._indicators.get(symbol)
            return list(sub_dict.values()) if sub_dict else []

    def get_all_symbols(self) -> List[str]:
        """Returns all symbols currently having active mounted indicators."""
        with self._lock:
            return list(self._indicators.keys())

    def clear(self) -> None:
        """Clears all registered indicators."""
        with self._lock:
            self._indicators.clear()
