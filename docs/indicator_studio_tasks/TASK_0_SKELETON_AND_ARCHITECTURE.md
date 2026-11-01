# Task 0: Project Scaffolding, Core Types & Shared Contracts
## Detailed Specification, Architecture & Hand-Off Interface

> **Document Version:** 1.0.0  
> **Status:** Initial Foundational Task  
> **Target Subsystem:** `trading_engine/` & `tests/`  
> **Test Suite:** `tests/test_scaffolding.py`

---

## 1. Objective & Scope

Task 0 establishes the **foundational project architecture and skeleton** for the entire Indicator Studio before any mathematical or runtime logic is written.

It ensures that:
1. All directory structures and package namespaces exist with clean `__init__.py` exports.
2. Standard shared data contracts (`IndicatorSignal`, `InputDescriptor`, `BaseIndicator`, `SynchronizedBar`, `TickPacket`) are strictly defined in one place to eliminate circular dependencies.
3. System dependencies (`duckdb`, `numba`, `numpy`, `scipy`, `pandas`, `aiohttp`, `fastapi`) are verified.
4. The centralized test runner and mock test fixtures are configured for automated testing across all subsequent tasks.

---

## 2. Directory Structure Scaffolding

```
trading_engine/
├── quant_lib/                  # [Task 1] Pre-defined Math Library
│   ├── __init__.py             # Exports ta, structure, order_flow, options, Indicator, input
│   ├── base.py                 # Core Indicator base class, Signal, input descriptors
│   ├── ta.py                   # Technical analysis mathematical kernels
│   ├── structure.py            # Dynamic S/R pivot clustering, breakout boxes
│   ├── order_flow.py           # Buyer/Seller pressure, Volume Profile
│   └── options.py              # Black-Scholes Greeks, IV solver, Straddle
│
├── runtime/                    # [Task 2] Dynamic Python Runtime
│   ├── __init__.py             # Exports IndicatorCompiler, DynamicIndicatorRegistry, WarmupManager
│   ├── compiler.py             # AST security validator and module loader
│   ├── registry.py             # Thread-safe atomic indicator swap registry
│   └── warmup.py               # Historical candle priming pipeline
│
├── data/                       # [Task 3] Local DuckDB & VPS Gateway
│   ├── __init__.py             # Exports VPSGatewayClient, LocalDataStore, TimestampAlignmentBus
│   ├── vps_client.py           # Authenticated REST & WebSocket client for VPS
│   ├── duckdb_store.py         # Embedded DuckDB / Parquet storage engine
│   └── sync_bus.py             # Spot + Option timestamp alignment bus
│
├── replay/                     # [Task 4] Simulation & Backtesting
│   ├── __init__.py             # Exports ReplaySession, VectorizedBacktester, BreakpointEngine
│   ├── session.py              # Stepping iterator (step forward/back, scrub)
│   ├── backtester.py           # Vectorized macro backtest scorecard
│   └── breakpoints.py          # "Break on Signal" and condition evaluator
│
├── indicators/                 # [Task 6] Reference Indicator Templates
│   ├── __init__.py             # Exports PresetsManager
│   ├── presets.py              # Indicator preset discovery manager
│   ├── bss_pro_v5.py           # Native Python BSS Pro v5 implementation
│   ├── algoalpha_breakout.py   # Smart Money Breakout Channels
│   └── supertrend_cloud.py     # Multi-timeframe Supertrend Cloud
│
└── web/static/                 # [Task 5] Split-Screen Web Studio
    ├── studio.html             # Resizable vertical split-screen workspace
    ├── studio.css              # Dark-mode styling, resizer gutter, watch table
    ├── studio.js               # Monaco editor controller & WebSocket bridge
    └── studio_chart.js         # Lightweight Charts dual Spot/Option renderer
```

---

## 3. Shared Data Contracts (`quant_lib/base.py`)

All subsequent tasks import and consume these authoritative contracts:

```python
from dataclasses import dataclass, field
from typing import Dict, Any, Optional, List, Union
import pandas as pd
import numpy as np

@dataclass
class IndicatorSignal:
    """Universal Signal Contract produced by indicators and consumed by trading engine."""
    action: str                 # "BUY_CE", "BUY_PE", "EXIT_CE", "EXIT_PE", "HOLD"
    trigger_price: float        # Execution price recommendation
    stop_loss: float = 0.0      # Invalidation price
    target: float = 0.0         # Primary profit target
    target_2: float = 0.0       # Secondary profit target
    risk_points: float = 0.0    # abs(trigger_price - stop_loss)
    reward_points: float = 0.0  # abs(target - trigger_price)
    rr_ratio: float = 0.0       # reward_points / risk_points
    confidence: float = 1.0     # 0.0 to 1.0 score
    tag: str = "SIGNAL"         # e.g. "BSS_PULLBACK", "BREAKOUT"
    timestamp: str = ""
    extra: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if self.risk_points == 0.0 and self.stop_loss > 0.0:
            self.risk_points = round(abs(self.trigger_price - self.stop_loss), 2)
        if self.reward_points == 0.0 and self.target > 0.0:
            self.reward_points = round(abs(self.target - self.trigger_price), 2)
        if self.risk_points > 0.0 and self.rr_ratio == 0.0:
            self.rr_ratio = round(self.reward_points / self.risk_points, 2)

@dataclass
class SynchronizedBar:
    """Synchronized Spot Index and Option Strike data bundle."""
    timestamp: str
    spot_open: float
    spot_high: float
    spot_low: float
    spot_close: float
    spot_volume: int
    opt_open: float
    opt_high: float
    opt_low: float
    opt_close: float
    opt_volume: int
    opt_oi: int

class InputDescriptor:
    """Descriptor for an indicator parameter rendered in the UI."""
    def __init__(self, default: Any, title: str, input_type: str, minval=None, maxval=None, step=None, options=None, group="General", tooltip=""):
        self.default = default
        self.title = title
        self.input_type = input_type
        self.minval = minval
        self.maxval = maxval
        self.step = step
        self.options = options or []
        self.group = group
        self.tooltip = tooltip
        self.value = default

class BaseIndicator:
    """Abstract Base Class for all Indicators."""
    def __init__(self, **kwargs):
        self.plots: Dict[str, Any] = {}
        self.init()

    def init(self):
        """Declare input parameters here."""
        pass

    def warm_up(self, historical_bars: pd.DataFrame):
        """Vectorized priming across historical bars."""
        pass

    def on_bar(self, bar: Dict[str, Any], history: Any) -> Optional[IndicatorSignal]:
        """Called upon candle close (or historical replay step)."""
        pass

    def on_tick(self, ltp: float, volume: int, timestamp: str) -> Optional[IndicatorSignal]:
        """Called upon live sub-second tick."""
        pass

    def signal(self, action: str, trigger_price: float, stop_loss: float = 0.0, target: float = 0.0, **kwargs) -> IndicatorSignal:
        return IndicatorSignal(action=action, trigger_price=trigger_price, stop_loss=stop_loss, target=target, **kwargs)
```

---

## 4. Concrete Acceptance Criteria & Verification Matrix

| AC ID | Requirement | Metric / Tolerance | Test Function |
| :--- | :--- | :--- | :--- |
| **AC-0.1** | Directory Skeleton Creation | All 5 sub-packages exist with valid `__init__.py` files and export declarations. | `test_directory_structure_exists()` |
| **AC-0.2** | Shared Data Contracts | `IndicatorSignal`, `SynchronizedBar`, `InputDescriptor`, and `BaseIndicator` instantiate and serialize without error. | `test_core_contracts_instantiation()` |
| **AC-0.3** | Dependency Sanity Check | `duckdb`, `numba`, `numpy`, `pandas`, `scipy`, `aiohttp`, `fastapi` import successfully with supported versions. | `test_environment_dependencies()` |
| **AC-0.4** | Base Signal Auto-Calculation | `IndicatorSignal` automatically computes `risk_points`, `reward_points`, and `rr_ratio` when target and stop-loss are provided. | `test_signal_auto_calculation()` |
| **AC-0.5** | Clean Package Importability | `from trading_engine import quant_lib, runtime, data, replay, indicators` succeeds with 0 circular import warnings. | `test_clean_package_imports()` |

---

## 5. Automated Test Suite (`tests/test_scaffolding.py`)

The test suite must implement:
1. `test_directory_structure_exists()`: Verifies that every directory and skeleton file in Section 2 exists on disk.
2. `test_core_contracts_instantiation()`: Tests `IndicatorSignal`, `InputDescriptor`, and `BaseIndicator` instantiation.
3. `test_environment_dependencies()`: Verifies all required dependencies are installed and accessible in Python path.
4. `test_signal_auto_calculation()`: Validates that risk/reward mathematics inside `IndicatorSignal` are strictly accurate.
5. `test_clean_package_imports()`: Imports all top-level modules to verify zero syntax errors and zero circular dependencies.

---

## 6. HAND-OFF CONTRACT FOR NEXT TASK (TASK 1)

Before Task 1 begins, Task 0 guarantees and hands off:
* ✅ Directory `trading_engine/quant_lib/` is created with `base.py` containing `IndicatorSignal`, `InputDescriptor`, and `BaseIndicator`.
* ✅ Clean module export in `trading_engine/quant_lib/__init__.py`.
* ✅ Verified numerical environment (NumPy, SciPy, Numba) ready for mathematical implementations.
* ✅ Task 1 will implement the mathematical formulas in `quant_lib/ta.py`, `structure.py`, `order_flow.py`, and `options.py`.
