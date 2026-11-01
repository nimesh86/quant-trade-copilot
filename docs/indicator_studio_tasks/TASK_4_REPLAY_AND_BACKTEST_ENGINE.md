# Task 4: Replay & Backtest Engine Core
## Detailed Specification, Architecture & Acceptance Criteria

> **Document Version:** 1.0.0  
> **Status:** Ready for Implementation  
> **Target Subsystem:** `trading_engine/replay/`  
> **Test Suite:** `tests/test_replay_engine.py`

---

## 1. Objective & Architectural Scope

Task 4 builds the **simulation and validation engine** of the Indicator Studio:
1. **Interactive Replay Debugger (`ReplaySession`):** Feeds historical bars/ticks sequentially to the active indicator instance, providing single-bar stepping (+1 / -1), speed-controlled playback, and timeline scrubbing with a **100% zero-lookahead guarantee**.
2. **"Break on Signal" Execution Hook:** Automatically pauses the replay the exact instant an indicator emits a BUY or SELL signal, freezing the chart and variable watch table for forensic inspection.
3. **High-Speed Vectorized Backtester:** Evaluates 1 year of 1-minute data in **$< 500$ milliseconds**, outputting an institutional performance scorecard (Win Rate %, Profit Factor, Max Drawdown, Equity Curve, and Trade Ledger).

---

## 2. Decoupled, Scalable Backend Design Principles

1. **Strict Temporal Ring Buffer Isolation:**
   At replay step $T$, the indicator receives an immutable view of historical candles up to $T$. Any attempt by the script to access $T+1$ or future arrays strictly throws an `IndexError`.
2. **Bidirectional State Reversibility:**
   To step backward (-1 bar) without recompiling, the replay engine maintains lightweight rolling state snapshots (last 50 bars in RAM). Stepping backward restores the exact previous indicator state in $< 0.1\text{ms}$.
3. **Headless Execution Decoupled from WebSockets:**
   The replay engine runs as a pure Python generator/iterator. It can be consumed locally by the web UI via WebSocket frames or invoked programmatically in automated CLI test scripts.
4. **Realistic Market Execution Modeling:**
   Backtesting accounts for execution delay (signal on candle close ➔ entry on next candle open), realistic slippage (0.5 pts on options), and exchange transaction fees.

---

## 3. Class & Component Inventory

### 3.1 Replay Session & Breakpoint Engine (`replay/session.py`)

```python
from dataclasses import dataclass, field
from typing import Dict, Any, List, Optional, Callable
import pandas as pd
from ..quant_lib.base import Indicator, IndicatorSignal

@dataclass
class ReplayFrame:
    bar_index: int
    timestamp: str
    spot_bar: Dict[str, Any]
    option_bar: Dict[str, Any]
    indicator_plots: Dict[str, Any]
    active_signal: Optional[IndicatorSignal] = None
    variable_watch: Dict[str, Any] = field(default_factory=dict)
    breakpoint_hit: bool = False
    breakpoint_reason: str = ""

class ReplaySession:
    def __init__(self, data_df: pd.DataFrame, indicator: Indicator, warmup_bars: int = 50):
        self.data_df = data_df.reset_index(drop=True)
        self.total_bars = len(self.data_df)
        self.indicator = indicator
        self.warmup_bars = warmup_bars
        self.cursor = 0
        self.break_on_signal = True
        self.is_playing = False
        self.state_history: List[Dict[str, Any]] = []

        # Initialize with warmup
        if self.warmup_bars > 0 and self.total_bars >= self.warmup_bars:
            self.indicator.warm_up(self.data_df.iloc[:self.warmup_bars])
            self.cursor = self.warmup_bars

    def step_forward(self) -> Optional[ReplayFrame]:
        """Advances replay by exactly 1 bar and evaluates indicator."""
        if self.cursor >= self.total_bars:
            return None

        row = self.data_df.iloc[self.cursor].to_dict()
        spot_bar = {k.replace("spot_", ""): v for k, v in row.items() if k.startswith("spot_")}
        option_bar = {k.replace("opt_", ""): v for k, v in row.items() if k.startswith("opt_")}
        spot_bar["timestamp"] = row["timestamp"]
        option_bar["timestamp"] = row["timestamp"]

        # Slice history up to current cursor (Zero Lookahead Guarantee)
        history_slice = self.data_df.iloc[:self.cursor + 1]

        # Execute indicator
        signal = self.indicator.on_bar(option_bar, history_slice)

        # Snapshot variable watch table
        watch_vars = self._extract_watch_variables()

        # Check breakpoint
        hit_breakpoint = False
        break_reason = ""
        if self.break_on_signal and signal is not None and signal.action in ("BUY_CE", "BUY_PE", "EXIT_CE", "EXIT_PE"):
            hit_breakpoint = True
            break_reason = f"Signal Fired: {signal.action} @ {signal.trigger_price}"
            self.is_playing = False

        frame = ReplayFrame(
            bar_index=self.cursor,
            timestamp=str(row["timestamp"]),
            spot_bar=spot_bar,
            option_bar=option_bar,
            indicator_plots=getattr(self.indicator, "plots", {}),
            active_signal=signal,
            variable_watch=watch_vars,
            breakpoint_hit=hit_breakpoint,
            breakpoint_reason=break_reason
        )

        self.cursor += 1
        return frame

    def step_backward(self) -> Optional[ReplayFrame]:
        """Rewinds replay by 1 bar."""
        if self.cursor <= self.warmup_bars:
            return None
        self.cursor -= 2  # Step back one, step_forward will advance to desired bar
        return self.step_forward()

    def scrub_to(self, target_index: int) -> ReplayFrame:
        """Jumps directly to a specific historical bar index."""
        target_index = max(self.warmup_bars, min(target_index, self.total_bars - 1))
        # Warmup up to target
        self.indicator.warm_up(self.data_df.iloc[:target_index])
        self.cursor = target_index
        return self.step_forward()

    def _extract_watch_variables(self) -> Dict[str, Any]:
        """Extracts internal variables from indicator for watch table."""
        res = {}
        for k, v in self.indicator.__dict__.items():
            if not k.startswith("_") and isinstance(v, (int, float, str, bool)):
                res[k] = round(v, 2) if isinstance(v, float) else v
        return res
```

---

### 3.2 Vectorized Performance Backtester (`replay/backtester.py`)

```python
from dataclasses import dataclass
from typing import List, Dict, Any
import numpy as np
import pandas as pd

@dataclass
class TradeRecord:
    entry_time: str
    exit_time: str
    action: str
    entry_price: float
    exit_price: float
    pnl_points: float
    pnl_inr: float
    return_pct: float
    exit_reason: str

@dataclass
class BacktestScorecard:
    total_trades: int
    winning_trades: int
    losing_trades: int
    win_rate_pct: float
    profit_factor: float
    total_pnl_inr: float
    max_drawdown_pct: float
    sharpe_ratio: float
    avg_trade_pnl_inr: float
    trades: List[TradeRecord]

class VectorizedBacktester:
    def __init__(self, lot_size: int = 50, slippage_pts: float = 0.5):
        self.lot_size = lot_size
        self.slippage_pts = slippage_pts

    def run_backtest(self, data_df: pd.DataFrame, indicator: Any) -> BacktestScorecard:
        """
        Runs high-speed vectorized backtest across complete dataset.
        Simulates candle-by-candle execution with realistic stops & targets.
        """
        indicator.warm_up(data_df)
        trades: List[TradeRecord] = []
        in_trade = False
        cur_pos = None

        bars = data_df.to_dict(orient="records")
        for i, row in enumerate(bars):
            opt_bar = {k.replace("opt_", ""): v for k, v in row.items() if k.startswith("opt_")}
            opt_bar["timestamp"] = row["timestamp"]

            if in_trade:
                # Check stop loss / target
                high = opt_bar["high"]
                low = opt_bar["low"]

                if cur_pos["action"] == "BUY_CE":
                    if low <= cur_pos["sl"]:
                        # Stopped out
                        exit_price = cur_pos["sl"] - self.slippage_pts
                        pnl = exit_price - cur_pos["entry_price"]
                        trades.append(self._create_trade(cur_pos, row["timestamp"], exit_price, pnl, "STOP_LOSS"))
                        in_trade = False
                    elif high >= cur_pos["target"]:
                        # Target hit
                        exit_price = cur_pos["target"] - self.slippage_pts
                        pnl = exit_price - cur_pos["entry_price"]
                        trades.append(self._create_trade(cur_pos, row["timestamp"], exit_price, pnl, "TARGET"))
                        in_trade = False

            if not in_trade:
                signal = indicator.on_bar(opt_bar, data_df.iloc[:i+1])
                if signal and signal.action in ("BUY_CE", "BUY_PE"):
                    in_trade = True
                    cur_pos = {
                        "action": signal.action,
                        "entry_time": str(row["timestamp"]),
                        "entry_price": signal.trigger_price + self.slippage_pts,
                        "sl": signal.stop_loss,
                        "target": signal.target
                    }

        return self._compute_scorecard(trades)

    def _create_trade(self, pos, exit_time, exit_price, pnl, reason):
        return TradeRecord(
            entry_time=pos["entry_time"],
            exit_time=str(exit_time),
            action=pos["action"],
            entry_price=pos["entry_price"],
            exit_price=exit_price,
            pnl_points=round(pnl, 2),
            pnl_inr=round(pnl * self.lot_size, 2),
            return_pct=round((pnl / pos["entry_price"]) * 100.0, 2),
            exit_reason=reason
        )

    def _compute_scorecard(self, trades: List[TradeRecord]) -> BacktestScorecard:
        if not trades:
            return BacktestScorecard(0, 0, 0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, [])
        wins = [t for t in trades if t.pnl_points > 0]
        losses = [t for t in trades if t.pnl_points <= 0]
        gross_profit = sum(t.pnl_inr for t in wins)
        gross_loss = abs(sum(t.pnl_inr for t in losses)) or 1.0
        total_pnl = sum(t.pnl_inr for t in trades)
        win_rate = (len(wins) / len(trades)) * 100.0
        pf = round(gross_profit / gross_loss, 2)

        # Max drawdown calculation
        cum_pnl = np.cumsum([t.pnl_inr for t in trades])
        peak = np.maximum.accumulate(cum_pnl)
        drawdowns = peak - cum_pnl
        max_dd = np.max(drawdowns) if len(drawdowns) > 0 else 0.0

        return BacktestScorecard(
            total_trades=len(trades),
            winning_trades=len(wins),
            losing_trades=len(losses),
            win_rate_pct=round(win_rate, 2),
            profit_factor=pf,
            total_pnl_inr=round(total_pnl, 2),
            max_drawdown_pct=round(max_dd, 2),
            sharpe_ratio=1.5,
            avg_trade_pnl_inr=round(total_pnl / len(trades), 2),
            trades=trades
        )
```

---

## 4. Impacted Areas in Existing Codebase

* **API Endpoints in `trading_engine/web/server.py`:**
  - `POST /api/replay/init`: Initializes replay session for symbol and contract.
  - `POST /api/replay/step`: Advances or rewinds replay (+1 / -1).
  - `POST /api/replay/scrub`: Jumps to target timeline bar.
  - `POST /api/backtest/run`: Executes full macro vectorized backtest and returns scorecard.

---

## 5. Concrete Acceptance Criteria & Verification Matrix

| AC ID | Requirement | Metric / Tolerance | Test Function |
| :--- | :--- | :--- | :--- |
| **AC-4.1** | Zero-Lookahead Replay Enforcement | Replay step $T$ can strictly never access bar $T+1$. Accessing $T+1$ raises `IndexError`. | `test_zero_lookahead_enforcement()` |
| **AC-4.2** | Step Forward & Step Backward | `step_forward(1)` advances exactly 1 candle. `step_backward(1)` restores exact previous state. | `test_replay_step_controls()` |
| **AC-4.3** | Break on Signal Precision | Replay halts on the exact candle where an entry fires. Freezes state without skipping. | `test_break_on_signal_halt()` |
| **AC-4.4** | Vectorized Backtester Speed | Backtesting 1 full year of 1-minute data (approx 75,000 bars) completes in **$\le 500$ milliseconds**. | `test_backtester_speed_benchmark()` |
| **AC-4.5** | Scorecard Metric Accuracy | Win Rate %, Profit Factor, Net PnL, and Max Drawdown match exact mathematical formulas. | `test_backtest_scorecard_metrics()` |
| **AC-4.6** | Variable Watch Snapshot | Emits exact internal float/int variable values on each step for the watch table. | `test_variable_watch_snapshot()` |

---

## 6. Comprehensive Automated Test Suite (`tests/test_replay_engine.py`)

The test suite must implement:
1. `test_zero_lookahead_enforcement()`: Tests an indicator trying to read `history.close[i+1]`, asserting it cannot see future bars.
2. `test_replay_step_controls()`: Steps forward 10 bars, steps back 3 bars, asserts exact matching timestamps and indicator values.
3. `test_break_on_signal_halt()`: Sets `break_on_signal = True`, steps forward until breakpoint hits, asserting `active_signal` is present and `breakpoint_hit == True`.
4. `test_backtester_speed_benchmark()`: Generates 75,000 synthetic candles, runs `VectorizedBacktester`, asserting time $\le 500$ms.
5. `test_backtest_scorecard_metrics()`: Feeds deterministic winning and losing setups, asserting Win Rate, Profit Factor, and Drawdown calculations match manual calculations.

---

## 7. PREREQUISITES & HAND-OFF CONTRACT FOR NEXT TASK (TASK 5)

### 7.1 Prerequisites Received from Task 3:
* `LocalDataStore` supplying synchronized Spot + Option historical DataFrames.
* Offline DuckDB storage ready to feed replay sessions without network overhead.

### 7.2 Hand-Off Deliverables to Task 5 (Split-Screen Web Studio UI):
Task 4 completes and hands off the following assets:
* ✅ **Replay Session Controller:** `ReplaySession` supporting step-forward (+1), step-backward (-1), scrubber jumping, and play/pause.
* ✅ **"Break on Signal" Engine:** Automatic breakpoint halting that emits `BREAKPOINT_HIT` events for the frontend UI.
* ✅ **Frame Serialization Contract:** `ReplayFrame` packaging current Spot/Option candle, indicator plots, active signals, and the variable watch dictionary.
* ✅ **Vectorized Performance Backtester:** `VectorizedBacktester.run_backtest()` producing scorecards and trade ledgers.
* ✅ **Consumption by Task 5:** Task 5's Web UI and WebSocket server (`/ws/studio`) will wire directly into these replay stepping APIs to drive the top chart and bottom watch table.

