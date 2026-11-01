"""
Universal Indicator-to-Strategy Adapter.
=============================================================================
Enables any BaseIndicator / Indicator (e.g. BSSProV6Indicator) developed in the
Quant Studio Extension to plug directly into the main trading application
execution engine (StrategyDetectorAdapter, PaperAdapter, and Live Broker Router).
=============================================================================
"""
from typing import Dict, Any, Optional, List
from datetime import datetime
import pandas as pd

try:
    from ..config import EngineConfig
    from .base import BaseStrategy, StrategySignal
    from ..quant_lib.base import BaseIndicator, IndicatorSignal, SynchronizedBar
except (ImportError, ValueError):
    try:
        from trading_engine.config import EngineConfig
        from trading_engine.strategy.base import BaseStrategy, StrategySignal
        from trading_engine.quant_lib.base import BaseIndicator, IndicatorSignal, SynchronizedBar
    except (ImportError, ValueError):
        from config import EngineConfig
        from strategy.base import BaseStrategy, StrategySignal
        from quant_lib.base import BaseIndicator, IndicatorSignal, SynchronizedBar



class IndicatorStrategyAdapter(BaseStrategy):
    """
    Universal bridge wrapping any BaseIndicator into the institutional BaseStrategy interface.
    
    1. Collects and synchronizes incoming 1-minute bars into historical series.
    2. Runs indicator on_bar evaluation.
    3. Translates IndicatorSignal into StrategySignal with automated GTT / order execution payloads.
    """

    def __init__(
        self,
        indicator: BaseIndicator,
        cfg: EngineConfig,
        strategy_cfg: Optional[Dict[str, Any]] = None,
        name_override: Optional[str] = None
    ):
        name = name_override or indicator.name
        super().__init__(name, cfg, strategy_cfg)
        self.indicator = indicator
        self.bar_history: List[Dict[str, Any]] = []

        # Apply any strategy_cfg overrides to indicator inputs
        if strategy_cfg:
            for k, v in strategy_cfg.items():
                if hasattr(self.indicator, k):
                    inp = getattr(self.indicator, k)
                    if hasattr(inp, "value"):
                        inp.value = v

    def reset_session(self):
        """Resets indicator daily session state and bar buffer at market open."""
        self.bar_history.clear()
        if hasattr(self.indicator, "reset_session"):
            self.indicator.reset_session()
        elif hasattr(self.indicator, "init"):
            self.indicator.init()

    def on_candle_close(self, bar: Dict[str, Any], session_context: Dict[str, Any]) -> Optional[StrategySignal]:
        """
        Executes on every 1-minute closed candle from Kite/Breeze live stream or replay loop.
        Converts the closed bar into SynchronizedBar and evaluates the indicator.
        """
        if not self.enabled:
            return None

        self.bar_history.append(bar)

        # Convert dict bar to SynchronizedBar
        ts = bar.get("timestamp") or bar.get("date") or bar.get("time") or ""
        spot_close = float(bar.get("close", 0.0))
        spot_open = float(bar.get("open", spot_close))
        spot_high = float(bar.get("high", spot_close))
        spot_low = float(bar.get("low", spot_close))
        spot_vol = int(bar.get("volume", 0))

        # Option price approximation or explicit strike close
        opt_close = float(bar.get("opt_close") or bar.get("option_close") or spot_close)
        opt_open = float(bar.get("opt_open") or bar.get("option_open") or opt_close)
        opt_high = float(bar.get("opt_high") or bar.get("option_high") or opt_close)
        opt_low = float(bar.get("opt_low") or bar.get("option_low") or opt_close)
        opt_vol = int(bar.get("opt_volume", 0))

        synced_bar = SynchronizedBar(
            timestamp=str(ts),
            spot_open=spot_open,
            spot_high=spot_high,
            spot_low=spot_low,
            spot_close=spot_close,
            spot_volume=spot_vol,
            opt_open=opt_open,
            opt_high=opt_high,
            opt_low=opt_low,
            opt_close=opt_close,
            opt_volume=opt_vol
        )

        try:
            sig: Optional[IndicatorSignal] = self.indicator.on_bar(synced_bar)
        except Exception as e:
            print(f"IndicatorStrategyAdapter [{self.name}]: Error evaluating on_bar: {e}", flush=True)
            return None

        if sig is None or sig.action in ("HOLD", "NONE", ""):
            return None

        # Determine side (CE or PE)
        side = "CE" if "CE" in sig.action.upper() else ("PE" if "PE" in sig.action.upper() else "CE")
        action = "BUY" if "BUY" in sig.action.upper() else "EXIT"

        # Parse timestamp to datetime object
        parsed_dt = datetime.now()
        if isinstance(ts, str) and ts:
            try:
                parsed_dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            except Exception:
                try:
                    parsed_dt = datetime.strptime(ts[:19], "%Y-%m-%d %H:%M:%S")
                except Exception:
                    pass
        elif isinstance(ts, datetime):
            parsed_dt = ts

        symbol = bar.get("symbol", self.cfg.symbol if hasattr(self.cfg, "symbol") else "NIFTY")

        # Map to standard StrategySignal
        return StrategySignal(
            strategy_name=self.name,
            timestamp=parsed_dt,
            symbol=symbol,
            side=side,
            action=action,
            trigger_price=sig.trigger_price,
            stop_loss=sig.stop_loss,
            target=sig.target,
            risk_points=sig.risk_points,
            reward_points=sig.reward_points,
            rr_ratio=sig.rr_ratio,
            confidence_score=sig.confidence,
            risk_rating="LOW" if sig.confidence >= 0.70 else "MEDIUM",
            setup_details=sig.extra or {},
            is_confirmed=True
        )
