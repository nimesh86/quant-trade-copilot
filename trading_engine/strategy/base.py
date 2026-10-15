"""
Base Strategy Interface & Signal Data Contracts.
Defines standard protocols for pluggable, modular strategies.
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, Any, Optional

try:
    from ..config import EngineConfig
except ImportError:
    from config import EngineConfig

@dataclass
class StrategySignal:
    strategy_name: str
    timestamp: datetime
    symbol: str
    side: str                          # 'CE' or 'PE'
    action: str = "BUY"
    trigger_price: float = 0.0         # Recommended entry / GTT trigger
    stop_loss: float = 0.0             # Invalidation price
    target: float = 0.0                # Profit target
    risk_points: float = 0.0           # abs(trigger_price - stop_loss)
    reward_points: float = 0.0         # abs(target - trigger_price)
    rr_ratio: float = 0.0              # reward_points / risk_points
    confidence_score: float = 0.50     # 0.0 to 1.0
    risk_rating: str = "MEDIUM"        # 'LOW', 'MEDIUM', 'HIGH'
    setup_details: Dict[str, Any] = field(default_factory=dict)
    gtt_payload: Dict[str, Any] = field(default_factory=dict)
    is_confirmed: bool = True

    def __post_init__(self):
        if self.risk_points > 0 and self.rr_ratio == 0.0:
            self.rr_ratio = round(self.reward_points / self.risk_points, 2)
        if not self.gtt_payload:
            atm_strike = int(round(self.trigger_price / 50.0) * 50)
            self.gtt_payload = {
                "strategy_name": self.strategy_name,
                "tradingsymbol": self.symbol,
                "action": f"BUY_{self.side}",
                "side": self.side,
                "strike": f"NIFTY {atm_strike} {self.side}",
                "trigger_price": round(self.trigger_price, 2),
                "stop_loss": round(self.stop_loss, 2),
                "target_price": round(self.target, 2),
                "risk_points": round(self.risk_points, 2),
                "reward_points": round(self.reward_points, 2),
                "rr_ratio": self.rr_ratio,
                "gtt_type": "TWO-WAY OCO",
                "lots": 10,
                "signal_type": self.setup_details.get("setup_type", f"{self.side}_SETUP"),
                "timestamp": self.timestamp.strftime("%Y-%m-%d %H:%M:%S") if hasattr(self.timestamp, "strftime") else str(self.timestamp)
            }

class BaseStrategy(ABC):
    def __init__(self, name: str, cfg: EngineConfig, strategy_cfg: Optional[Dict[str, Any]] = None):
        self.name = name
        self.cfg = cfg
        self.strategy_cfg = strategy_cfg or {}
        self.enabled = self.strategy_cfg.get("enabled", True)

    @abstractmethod
    def on_candle_close(self, bar: Dict[str, Any], session_context: Dict[str, Any]) -> Optional[StrategySignal]:
        """
        Invoked concurrently on every 1-minute candle close.
        Returns StrategySignal if an actionable setup is confirmed, else None.
        """
        pass

    def reset_session(self):
        """Resets daily intra-day states (e.g. at 09:15 IST)."""
        pass

    def get_risk_parameters(self) -> Dict[str, Any]:
        """Returns the specific target, stop-loss, and trailing configuration for this strategy."""
        return self.strategy_cfg
