"""
Abstract Base Execution Adapter.
Defines unified interface for order execution, position tracking, and portfolio accounting.
"""
from abc import ABC, abstractmethod
from typing import Dict, Any, Optional

class BaseAdapter(ABC):
    @abstractmethod
    def open_position(self, symbol: str, side: str, price: float, atr: float, timestamp: Any) -> Optional[Dict[str, Any]]:
        """Open a new market position."""
        pass

    @abstractmethod
    def update_position(self, bar: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Update trailing stops, breakeven ladder, and check exits."""
        pass

    @abstractmethod
    def close_position(self, reason: str, price: float, timestamp: Any) -> Dict[str, Any]:
        """Close an active position."""
        pass

    @abstractmethod
    def get_portfolio_summary(self) -> Dict[str, Any]:
        """Return capital, total PnL, winrate, and trade list."""
        pass
