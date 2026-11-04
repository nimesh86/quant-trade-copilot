"""
Trading Engine Application & Global Configuration Manager.
─────────────────────────────────────────────────────────
Provides a unified, type-safe, and persistent configuration system
for the entire application (General, Live, Paper Trading, and Backtest).
"""
import os
import json
import logging
from pathlib import Path
from dataclasses import dataclass, field, asdict
from typing import Dict, Any, Optional

logger = logging.getLogger("AppConfigManager")

CONFIG_DIR = Path(__file__).resolve().parent.parent.parent / "config"
CONFIG_FILE = CONFIG_DIR / "app_config.json"


@dataclass
class GeneralConfig:
    """General & multi-broker data provider settings."""
    provider: str = "kite"  # "kite" or "breeze"
    hist_source: str = "vps"  # "vps" (Cloud VPS), "local", "broker", "custom"
    hist_endpoint: str = "https://kesarisoft.com"
    live_feed_url: str = "wss://kesarisoft.com/ws"


@dataclass
class LiveConfig:
    """Authoritative live execution & indicator plug-in settings."""
    indicator_adapter: str = "bss_pro_v5"
    freeze_lots: int = 30
    slice_delay_ms: int = 250
    auto_square_off_time: str = "15:15"


@dataclass
class PaperConfig:
    """Paper trading capital allocation and risk management."""
    initial_capital: float = 500000.0
    risk_per_trade_pct: float = 7.0
    consecutive_losses_for_cooldown: int = 2
    loss_cooldown_mins: int = 15


@dataclass
class BacktestConfig:
    """Default simulated execution parameters for backtesting."""
    slippage_per_side: float = 0.50
    depth_slip_per_slice: float = 0.25


@dataclass
class AppConfig:
    """Master application configuration hierarchy."""
    general: GeneralConfig = field(default_factory=GeneralConfig)
    live: LiveConfig = field(default_factory=LiveConfig)
    paper: PaperConfig = field(default_factory=PaperConfig)
    backtest: BacktestConfig = field(default_factory=BacktestConfig)

    def to_dict(self) -> Dict[str, Any]:
        """Serializes configuration to nested dictionary."""
        return {
            "general": asdict(self.general),
            "live": asdict(self.live),
            "paper": asdict(self.paper),
            "backtest": asdict(self.backtest)
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "AppConfig":
        """Instantiates AppConfig from dictionary with validation."""
        if not isinstance(data, dict):
            return cls()

        # Handle flat payload from frontend if provided
        gen_data = data.get("general", {})
        live_data = data.get("live", {})
        paper_data = data.get("paper", {})
        bt_data = data.get("backtest", {})

        # Also support flat keys from UI form submit
        if not gen_data and "histSource" in data:
            gen_data = {
                "provider": data.get("provider", "kite"),
                "hist_source": data.get("histSource", "vps"),
                "hist_endpoint": data.get("histEndpoint", "https://kesarisoft.com"),
                "live_feed_url": data.get("liveFeedUrl", "wss://kesarisoft.com/ws")
            }
        if not live_data and "liveAdapter" in data:
            live_data = {
                "indicator_adapter": data.get("liveAdapter", "bss_pro_v5"),
                "freeze_lots": int(data.get("liveFreeze", 30)),
                "slice_delay_ms": int(data.get("liveDelay", 250)),
                "auto_square_off_time": data.get("liveSquareOff", "15:15")
            }
        if not paper_data and "paperCapital" in data:
            paper_data = {
                "initial_capital": float(data.get("paperCapital", 500000.0)),
                "risk_per_trade_pct": float(data.get("paperRisk", 7.0)),
                "consecutive_losses_for_cooldown": int(data.get("paperConsecLoss", 2)),
                "loss_cooldown_mins": int(data.get("paperCooldown", 15))
            }
        if not bt_data and "btSlip" in data:
            bt_data = {
                "slippage_per_side": float(data.get("btSlip", 0.50))
            }

        return cls(
            general=GeneralConfig(**{k: v for k, v in gen_data.items() if k in GeneralConfig.__annotations__}),
            live=LiveConfig(**{k: v for k, v in live_data.items() if k in LiveConfig.__annotations__}),
            paper=PaperConfig(**{k: v for k, v in paper_data.items() if k in PaperConfig.__annotations__}),
            backtest=BacktestConfig(**{k: v for k, v in bt_data.items() if k in BacktestConfig.__annotations__})
        )


class AppConfigManager:
    """
    Central thread-safe configuration manager singleton.
    Manages loading, updating, and saving configuration to disk.
    """
    _instance: Optional["AppConfigManager"] = None

    def __new__(cls, *args, **kwargs):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._config = AppConfig()
            cls._instance.load()
        return cls._instance

    def load(self) -> AppConfig:
        """Loads configuration from persistent JSON file if present."""
        if CONFIG_FILE.exists():
            try:
                raw_text = CONFIG_FILE.read_text(encoding="utf-8")
                data = json.loads(raw_text)
                self._config = AppConfig.from_dict(data)
                logger.info(f"Loaded application configuration from {CONFIG_FILE}")
            except Exception as e:
                logger.warning(f"Failed to parse {CONFIG_FILE}, using defaults: {e}")
                self._config = AppConfig()
        else:
            self._config = AppConfig()
            self.save()
        return self._config

    def save(self) -> bool:
        """Persists current configuration to JSON file."""
        try:
            CONFIG_DIR.mkdir(parents=True, exist_ok=True)
            data = self._config.to_dict()
            CONFIG_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")
            logger.info(f"Saved application configuration to {CONFIG_FILE}")
            return True
        except Exception as e:
            logger.error(f"Failed to save {CONFIG_FILE}: {e}")
            return False

    def get_config(self) -> AppConfig:
        """Returns the active AppConfig instance."""
        return self._config

    def update(self, data: Dict[str, Any]) -> AppConfig:
        """Updates configuration fields and persists to disk."""
        self._config = AppConfig.from_dict(data)
        self.save()
        return self._config

    def reset_defaults(self) -> AppConfig:
        """Resets configuration to default factory values."""
        self._config = AppConfig()
        self.save()
        return self._config

    def get_hist_source(self) -> tuple[str, str]:
        """Returns (hist_source, hist_endpoint)."""
        gen = self._config.general
        return gen.hist_source, gen.hist_endpoint


# Global singleton instance
app_config_manager = AppConfigManager()
