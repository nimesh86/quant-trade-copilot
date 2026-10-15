"""
Abstract Base Data Feeder.
Standardizes data delivery for both historical backtests and live streaming.
"""
from abc import ABC, abstractmethod
from typing import Generator, Dict, Any
import pandas as pd

class BaseDataFeeder(ABC):
    @abstractmethod
    def initialize(self):
        """Prepare database connection or WebSocket."""
        pass

    @abstractmethod
    def get_training_data(self) -> pd.DataFrame:
        """Return historical candles for model training."""
        pass

    @abstractmethod
    def stream_bars(self) -> Generator[Dict[str, Any], None, None]:
        """Stream bars one by one (either from historical replay or live ticks)."""
        pass
