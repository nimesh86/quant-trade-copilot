"""
Backtest Engine: Master Engine Orchestrator.
─────────────────────────────────────────────
Coordinates Feeder, Replayer, and Scorecard Generator.
Serves interactive backtest requests from Main Web App and Quant Studio VS Code.
Shared Quant Core contracts guarantee 100% mathematical consistency.
"""
from datetime import datetime
from typing import Dict, Any, Optional, List, Tuple
import os
import json

from trading_engine.backtest_engine.feeder import BacktestFeeder
from trading_engine.backtest_engine.replayer import BacktestReplayer
from trading_engine.backtest_engine.scorecard import ScorecardGenerator


class BacktestEngine:
    """
    Standardized Backtest Engine (Layer B).
    """

    def __init__(
        self,
        feeder: Optional[BacktestFeeder] = None,
        replayer: Optional[BacktestReplayer] = None
    ):
        self.feeder = feeder or BacktestFeeder()
        self.replayer = replayer or BacktestReplayer(feeder=self.feeder)

    @staticmethod
    def _normalize_blocked_slots(raw_slots: Any) -> List[Tuple[str, str]]:
        norm: List[Tuple[str, str]] = []
        if not raw_slots or not isinstance(raw_slots, list):
            return norm
        for slot in raw_slots:
            if isinstance(slot, (list, tuple)) and len(slot) >= 2:
                s, e = str(slot[0]).strip()[:5], str(slot[1]).strip()[:5]
                if s and e and s <= e:
                    norm.append((s, e))
            elif isinstance(slot, dict):
                s = str(slot.get("start") or slot.get("from") or slot.get("fromTime") or "").strip()[:5]
                e = str(slot.get("end") or slot.get("to") or slot.get("toTime") or "").strip()[:5]
                if s and e and s <= e:
                    norm.append((s, e))
            elif isinstance(slot, str) and "-" in slot:
                parts = slot.split("-")
                s, e = parts[0].strip()[:5], parts[1].strip()[:5]
                if s and e and s <= e:
                    norm.append((s, e))
        return norm

    def run_backtest(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """
        Executes complete backtest pipeline.
        
        Args:
            params: {
                "symbol": "NIFTY50",
                "segment": "NSE",
                "dateFrom": "2026-10-29",
                "dateTo": "2026-10-30",
                "optionType": "CE",        # "CE", "PE", or "ATM"
                "capital": 50000.0,
                "strategy": "Adaptive Trend Pro"
            }
        """
        symbol = str(params.get("symbol") or "NIFTY50").strip()
        date_from = str(params.get("dateFrom") or params.get("startDate") or params.get("start_date") or params.get("date_from") or "2026-10-29").strip()
        date_to = str(params.get("dateTo") or params.get("endDate") or params.get("end_date") or params.get("date_to") or "2026-10-30").strip()
        option_type = str(params.get("optionType") or params.get("option_type") or "ATM").strip()
        capital = float(params.get("capital") or 50000.0)
        strategy = str(params.get("strategy") or params.get("indicator") or params.get("strategy_name") or "Adaptive Trend Pro").strip()

        # Route to AI-Trader Dual XGBoost + Macro engine if selected
        strat_lower = strategy.lower()
        if "xgboost" in strat_lower or "ai_trader" in strat_lower or "dual_xgboost_macro" in strat_lower or "78l" in strat_lower:
            from trading_engine.backtest_engine.ai_trader_engine import AITraderBacktestEngine
            results = AITraderBacktestEngine.run(params)
            self._record_execution_log(results.get("executionLogEntry"))
            return results

        # 1. Load spot candle data
        spot_df = self.feeder.load_spot_candles(symbol, date_from, date_to)

        if spot_df.empty:
            return ScorecardGenerator.generate(
                trades=[],
                equity_curve=[],
                initial_capital=capital,
                parameters=params
            )

        # 2. Run event-driven simulation
        enable_hammer = bool(params.get("enable_hammer_reversal", False))
        enable_sr = bool(params.get("enable_sr_zones", False))
        rf = float(params.get("risk_factor", 0.5))
        max_trades = int(params.get("max_trades_per_day") or params.get("maxTradesPerDay") or 4)
        raw_blocked = params.get("blockedTimeSlots") or params.get("blocked_time_slots") or [["09:15", "10:05"]]
        norm_blocked = self._normalize_blocked_slots(raw_blocked)
        sizing_mode = str(params.get("sizingMode") or params.get("sizing_mode") or "conservative").strip()

        trades, equity_curve = self.replayer.run(
            spot_df=spot_df,
            symbol=symbol,
            option_filter=option_type,
            initial_capital=capital,
            strategy_name=strategy,
            enable_hammer_reversal=enable_hammer,
            enable_sr_zones=enable_sr,
            risk_factor=rf,
            blocked_time_slots=norm_blocked,
            max_trades_per_day=max_trades,
            sizing_mode=sizing_mode
        )

        # 3. Generate Scorecard & Execution Log
        results = ScorecardGenerator.generate(
            trades=trades,
            equity_curve=equity_curve,
            initial_capital=capital,
            parameters=params
        )

        # Attach clean spot candles for IDE/web verification chart (optimized vector slice)
        spot_candles_list = []
        if not spot_df.empty:
            # If large multi-month range, limit spotCandles attached directly to payload to recent 5 days
            # Individual trade modals fetch exact date candles via /api/quant/candles on demand
            sample_df = spot_df if len(spot_df) <= 1875 else spot_df.iloc[-1875:]
            times = [t.strftime("%Y-%m-%d %H:%M") if hasattr(t, "strftime") else str(t)[:16] for t in sample_df["timestamp"]]
            opens = sample_df["open"].tolist()
            highs = sample_df["high"].tolist()
            lows = sample_df["low"].tolist()
            closes = sample_df["close"].tolist()
            vols = sample_df["volume"].tolist() if "volume" in sample_df else [0.0] * len(sample_df)
            spot_candles_list = [
                {"time": t, "open": float(o), "high": float(h), "low": float(l), "close": float(c), "volume": float(v)}
                for t, o, h, l, c, v in zip(times, opens, highs, lows, closes, vols)
            ]
        results["spotCandles"] = spot_candles_list

        # 4. Persist to reports/backtest_execution_logs.json
        self._record_execution_log(results.get("executionLogEntry"))

        return results

    def _record_execution_log(self, entry: Optional[Dict[str, Any]]):
        if not entry:
            return
        log_path = "reports/backtest_execution_logs.json"
        try:
            os.makedirs(os.path.dirname(log_path), exist_ok=True)
            logs = []
            if os.path.exists(log_path):
                try:
                    with open(log_path, "r") as f:
                        logs = json.load(f)
                except Exception:
                    logs = []
            logs.insert(0, entry)
            # Retain last 100 runs
            logs = logs[:100]
            with open(log_path, "w") as f:
                json.dump(logs, f, indent=2)
        except Exception as e:
            print(f"[BacktestEngine] Warning: Could not persist execution log: {e}")
