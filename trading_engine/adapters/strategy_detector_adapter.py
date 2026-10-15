"""
Parallel Strategy Detection Adapter.
Runs multiple strategies concurrently across a ThreadPool on every 1-minute candle close.
Generates GTT Suggestion Payloads for Live Mode and dispatches signals for automated execution.
"""
import os
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, Any, List, Optional
from datetime import datetime
from pathlib import Path

try:
    from ..config import EngineConfig
    from ..strategy.base import BaseStrategy, StrategySignal
    from ..strategy.amd_volume_profile import AMDVolumeProfileStrategy
    from ..strategy.core_85_strategy import Core85Strategy
except ImportError:
    from config import EngineConfig
    from strategy.base import BaseStrategy, StrategySignal
    from strategy.amd_volume_profile import AMDVolumeProfileStrategy
    from strategy.core_85_strategy import Core85Strategy

class StrategyDetectorAdapter:
    def __init__(self, cfg: EngineConfig, config_path: Optional[str] = None):
        self.cfg = cfg
        self.strategies: List[BaseStrategy] = []
        self.active_suggestions: List[StrategySignal] = []
        self.detection_history: List[Dict[str, Any]] = []

        # Load strategies configuration
        if config_path is None:
            config_path = str(Path(__file__).resolve().parent.parent / "strategies_config.json")
        self.config_path = config_path
        self.strategies_cfg = self._load_strategies_config()

        # ThreadPool for parallel candle evaluations
        self.executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="strat_worker")
        self._initialize_strategies()
        print(f"StrategyDetectorAdapter: Initialized {len(self.strategies)} strategies running in parallel.", flush=True)

    def _load_strategies_config(self) -> Dict[str, Any]:
        if os.path.exists(self.config_path):
            try:
                with open(self.config_path, "r") as f:
                    return json.load(f)
            except Exception as e:
                print(f"StrategyDetectorAdapter: Error reading {self.config_path}: {e}")
        return {}

    def _initialize_strategies(self):
        # 1. AMD Volume Profile Strategy
        amd_cfg = self.strategies_cfg.get("amd_volume_profile", {})
        if amd_cfg.get("enabled", True):
            self.strategies.append(AMDVolumeProfileStrategy(self.cfg, amd_cfg))
            print("  ├── Registered: AMDVolumeProfileStrategy (TPO + Liquidity Sweep)")

    def reset_session(self):
        """Resets active suggestions and daily session states across all strategies."""
        self.active_suggestions = []
        for s in self.strategies:
            s.reset_session()

    def evaluate_candle(self, bar: Dict[str, Any], session_context: Optional[Dict[str, Any]] = None) -> List[StrategySignal]:
        """Direct synchronous candle evaluation (sub-millisecond latency per bar, ideal for replay loops)."""
        if not self.strategies:
            return []
        ctx = session_context or {}
        signals: List[StrategySignal] = []
        for s in self.strategies:
            try:
                sig = s.on_candle_close(bar, ctx)
                if sig is not None:
                    signals.append(sig)
            except Exception as e:
                print(f"StrategyDetectorAdapter: Error evaluating {s.name}: {e}")
        if signals:
            for s in signals:
                self._process_detected_signal(s, bar)
        return signals

    def evaluate_candle_parallel(self, bar: Dict[str, Any], session_context: Optional[Dict[str, Any]] = None) -> List[StrategySignal]:
        """
        Dispatches candle close event across all strategies in parallel.
        Executes within <20ms using ThreadPoolExecutor.
        """
        if not self.strategies:
            return []

        ctx = session_context or {}
        futures = {self.executor.submit(s.on_candle_close, bar, ctx): s.name for s in self.strategies}
        signals: List[StrategySignal] = []

        for future in as_completed(futures):
            strat_name = futures[future]
            try:
                sig = future.result(timeout=0.25) # 250ms SLA
                if sig is not None:
                    signals.append(sig)
            except Exception as e:
                print(f"StrategyDetectorAdapter: Error evaluating {strat_name}: {e}")

        # If any strategy detected a setup, process GTT alert and log
        if signals:
            for s in signals:
                self._process_detected_signal(s, bar)

        return signals

    def _process_detected_signal(self, signal: StrategySignal, bar: Dict[str, Any]):
        self.active_suggestions.append(signal)
        # Keep recent 20 suggestions
        if len(self.active_suggestions) > 20:
            self.active_suggestions.pop(0)

        record = {
            "timestamp": signal.timestamp.strftime("%Y-%m-%d %H:%M:%S") if isinstance(signal.timestamp, datetime) else str(signal.timestamp),
            "strategy": signal.strategy_name,
            "side": signal.side,
            "action": signal.action,
            "trigger_price": signal.trigger_price,
            "stop_loss": signal.stop_loss,
            "target": signal.target,
            "risk_pts": signal.risk_points,
            "reward_pts": signal.reward_points,
            "rr_ratio": signal.rr_ratio,
            "risk_rating": signal.risk_rating,
            "gtt": signal.gtt_payload
        }
        self.detection_history.append(record)

        # Print structured GTT Alert Panel to console
        print("\n" + "=" * 80)
        print(f"  🔔 [STRATEGY DETECTED] {signal.strategy_name} | SETUP: {signal.side} BUY")
        print("=" * 80)
        print(f"  Symbol:          {signal.symbol}")
        print(f"  GTT Trigger:     ₹{signal.trigger_price:.2f} (Confirm entry on bar close)")
        print(f"  Invalidation SL: ₹{signal.stop_loss:.2f} (-{signal.risk_points:.2f} pts)")
        print(f"  High Target:     ₹{signal.target:.2f} (+{signal.reward_points:.2f} pts)")
        print(f"  Risk/Reward:     {signal.rr_ratio:.2f}R | Risk Rating: {signal.risk_rating}")
        if signal.setup_details:
            dtls = signal.setup_details
            print(f"  Context:         POC: ₹{dtls.get('poc', 0):.2f} | VAH: ₹{dtls.get('vah', 0):.2f} | VAL: ₹{dtls.get('val', 0):.2f}")
            print(f"  Sweep Info:      {dtls.get('setup_type')} (Swept {dtls.get('sweep_depth_pts', 0):.2f} pts, Reclaimed in {dtls.get('bars_to_reclaim', 0)} bars)")
        print("=" * 80 + "\n", flush=True)

    def get_latest_suggestions(self) -> List[Dict[str, Any]]:
        return [s.gtt_payload for s in self.active_suggestions]

    def shutdown(self):
        self.executor.shutdown(wait=False)
