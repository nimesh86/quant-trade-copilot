# Task 2: Dynamic Python Runtime & Hot-Reload Engine
## Detailed Specification, Architecture & Acceptance Criteria

> **Document Version:** 1.0.0  
> **Status:** Ready for Implementation  
> **Target Subsystem:** `trading_engine/runtime/`  
> **Test Suite:** `tests/test_indicator_runtime.py`

---

## 1. Objective & Architectural Scope

Task 2 builds the **execution kernel** that allows Python indicator code written in the Monaco editor to be compiled, validated, warmed up with historical data, and mounted into the live tick stream **dynamically at runtime without restarting the FastAPI server or dropping broker WebSocket connections**.

This component bridges user-authored code with the backend trading pipeline, guaranteeing **$\le 1\text{ms}$ hot-swap latency** and total protection against crashes or infinite loops.

---

## 2. Decoupled, Scalable Backend Design Principles

1. **Sandboxed Code Validation via AST Inspection:**
   Before any code is executed, the runtime parses the Abstract Syntax Tree using Python's built-in `ast` module. Dangerous operations (e.g. `import os`, `import subprocess`, `eval()`, writing files outside designated directories) are blocked instantly with clear error messages.
2. **Dynamic Isolated Module Namespace:**
   User scripts are executed inside a fresh `types.ModuleType("user_indicator")` namespace with access only to safe built-ins and `quant_lib`. A bug or exception inside a user indicator cannot corrupt the global Python process.
3. **Atomic Pointer Swapping (Zero-Downtime Hot-Reload):**
   Active indicators for any symbol (e.g. `NIFTY24OCT24200CE`) are stored in an in-memory dictionary protected by a re-entrant lock (`threading.RLock`). When an updated script is mounted, the old instance reference is atomically swapped with the new, warmed-up instance. No broker ticks are missed.
4. **Automated Input Introspection:**
   The runtime automatically inspects the indicator instance's attributes to find all `InputDescriptor` objects (created via `input.int()`, `input.float()`, etc.) and serializes them into a JSON schema for the frontend UI.

---

## 3. Class & Component Inventory

### 3.1 AST Security Validator & Compiler (`runtime/compiler.py`)

```python
import ast
import types
from typing import Dict, Any, Tuple, Optional, Type
from ..quant_lib.base import Indicator, InputDescriptor

class SecurityValidationError(Exception):
    def __init__(self, message: str, line: int = 0, column: int = 0):
        super().__init__(message)
        self.line = line
        self.column = column

class IndicatorCompiler:
    DISALLOWED_MODULES = {"os", "subprocess", "sys", "shutil", "socket", "http", "urllib", "requests"}
    DISALLOWED_BUILTINS = {"exec", "eval", "compile", "__import__", "open", "input"}

    @classmethod
    def validate_ast(cls, code_str: str) -> None:
        """Parses AST and rejects dangerous nodes."""
        try:
            tree = ast.parse(code_str)
        except SyntaxError as e:
            raise SecurityValidationError(f"SyntaxError: {e.msg}", line=e.lineno, column=e.offset)

        for node in ast.walk(tree):
            # Check forbidden imports
            if isinstance(node, ast.Import):
                for alias in node.names:
                    root_mod = alias.name.split(".")[0]
                    if root_mod in cls.DISALLOWED_MODULES:
                        raise SecurityValidationError(f"Prohibited import: '{root_mod}'", line=node.lineno, column=node.col_offset)
            elif isinstance(node, ast.ImportFrom):
                root_mod = (node.module or "").split(".")[0]
                if root_mod in cls.DISALLOWED_MODULES:
                    raise SecurityValidationError(f"Prohibited import: '{root_mod}'", line=node.lineno, column=node.col_offset)
            # Check forbidden function calls
            elif isinstance(node, ast.Call):
                if isinstance(node.func, ast.Name) and node.func.id in cls.DISALLOWED_BUILTINS:
                    raise SecurityValidationError(f"Prohibited function call: '{node.func.id}()'", line=node.lineno, column=node.col_offset)

    @classmethod
    def compile_and_load(cls, code_str: str, module_name: str = "custom_indicator") -> Tuple[Type[Indicator], Dict[str, Any]]:
        """Validates, compiles, and extracts the Indicator class and parameter schema."""
        cls.validate_ast(code_str)
        
        # Prepare safe sandbox namespace
        module = types.ModuleType(module_name)
        # Inject standard math & quant_lib
        from .. import quant_lib
        module.__dict__["quant_lib"] = quant_lib
        module.__dict__["ta"] = quant_lib.ta
        module.__dict__["input"] = quant_lib.input
        module.__dict__["Indicator"] = quant_lib.Indicator
        module.__dict__["IndicatorSignal"] = quant_lib.IndicatorSignal

        try:
            compiled_bytecode = compile(code_str, filename=f"<{module_name}>", mode="exec")
            exec(compiled_bytecode, module.__dict__)
        except Exception as e:
            raise RuntimeError(f"Compilation/Runtime error: {str(e)}")

        # Find class subclassing Indicator
        indicator_cls = None
        for item_name, item_val in module.__dict__.items():
            if isinstance(item_val, type) and issubclass(item_val, Indicator) and item_val is not Indicator:
                indicator_cls = item_val
                break

        if not indicator_cls:
            raise ValueError("No class inheriting from 'quant_lib.Indicator' was found in the script.")

        # Instantiate dummy instance to extract input parameter schema
        temp_instance = indicator_cls()
        param_schema = cls.extract_param_schema(temp_instance)
        return indicator_cls, param_schema

    @classmethod
    def extract_param_schema(cls, instance: Indicator) -> Dict[str, Any]:
        """Extracts all InputDescriptor instances from the indicator."""
        schema = {}
        for attr_name, attr_val in instance.__dict__.items():
            if isinstance(attr_val, InputDescriptor):
                schema[attr_name] = {
                    "name": attr_name,
                    "title": attr_val.title,
                    "type": attr_val.input_type,
                    "default": attr_val.default,
                    "current": attr_val.value,
                    "minval": attr_val.minval,
                    "maxval": attr_val.maxval,
                    "step": attr_val.step,
                    "options": attr_val.options,
                    "group": attr_val.group,
                    "tooltip": attr_val.tooltip
                }
        return schema
```

---

### 3.2 Thread-Safe Indicator Registry (`runtime/registry.py`)

```python
import threading
from typing import Dict, List, Optional
from ..quant_lib.base import Indicator, IndicatorSignal

class DynamicIndicatorRegistry:
    def __init__(self):
        self._indicators: Dict[str, Dict[str, Indicator]] = {}  # symbol -> {indicator_id: instance}
        self._lock = threading.RLock()

    def mount(self, symbol: str, indicator_id: str, instance: Indicator) -> None:
        """Atomically mounts an indicator instance for a symbol."""
        with self._lock:
            if symbol not in self._indicators:
                self._indicators[symbol] = {}
            self._indicators[symbol][indicator_id] = instance

    def unmount(self, symbol: str, indicator_id: str) -> bool:
        """Detaches an indicator from a symbol."""
        with self._lock:
            if symbol in self._indicators and indicator_id in self._indicators[symbol]:
                del self._indicators[symbol][indicator_id]
                return True
            return False

    def get_indicators_for_symbol(self, symbol: str) -> List[Indicator]:
        """Fast lockless or minimal read lock access for live tick dispatch."""
        with self._lock:
            return list(self._indicators.get(symbol, {}).values())

    def clear(self) -> None:
        with self._lock:
            self._indicators.clear()
```

---

### 3.3 Historical Warm-Up Pipeline (`runtime/warmup.py`)

Before an indicator processes live ticks, its rolling mathematical states (EMA, RSI, pivot swings, ATR) must be primed with historical bars.

```python
import pandas as pd
from typing import Dict, Any
from ..quant_lib.base import Indicator

class WarmupManager:
    @staticmethod
    def warm_up_indicator(indicator: Indicator, history_df: pd.DataFrame) -> Dict[str, Any]:
        """
        Feeds historical bars (e.g. 500-1000 bars) into the indicator instance.
        1. Calls indicator.warm_up(history_df) for vectorized priming.
        2. Steps through the last 50 bars via on_bar() to prime streaming recurrence states.
        Returns warmup metrics (latency_ms, bars_processed, final_values).
        """
        import time
        start_t = time.perf_counter()

        indicator.warm_up(history_df)
        
        # Step the trailing bars to ensure streaming ring buffer matches vectorized
        warm_bars = history_df.to_dict(orient="records")
        for bar in warm_bars[-50:]:
            indicator.on_bar(bar, history_df)

        elapsed_ms = (time.perf_counter() - start_t) * 1000.0
        return {
            "success": True,
            "bars_processed": len(history_df),
            "warmup_time_ms": round(elapsed_ms, 2)
        }
```

---

## 4. Impacted Areas in Existing Codebase

* **`trading_engine/web/server.py`:**
  - In `on_broker_websocket_tick(tick)`: Call `DynamicIndicatorRegistry.get_indicators_for_symbol(symbol)`.
  - Add API endpoints:
    - `POST /api/studio/validate`: AST syntax check without mounting.
    - `POST /api/studio/mount`: Validates, compiles, warms up, and atomically mounts indicator.
    - `POST /api/studio/unmount`: Detaches indicator.
* **`trading_engine/web/contract_data_manager.py`:**
  - Route actionable signals from `indicator.on_tick()` directly to `contract_engine.execute_signal()`.

---

## 5. Concrete Acceptance Criteria & Verification Matrix

| AC ID | Requirement | Metric / Tolerance | Test Function |
| :--- | :--- | :--- | :--- |
| **AC-2.1** | AST Security & Prohibited Calls | Blocks `os`, `sys`, `subprocess`, `eval`, `exec`, and file writes outside sandbox. Reports line and column numbers. | `test_ast_security_validation()` |
| **AC-2.2** | Syntax Error Reporting | Returns exact line number and syntax error message for unclosed brackets, indentation errors, or invalid keywords. | `test_syntax_error_reporting()` |
| **AC-2.3** | Parameter Schema Extraction | Accurately extracts `input.int`, `input.float`, `input.bool`, `input.string`, `input.color` with default, min, max, and tooltips. | `test_parameter_schema_extraction()` |
| **AC-2.4** | Historical Warm-Up Performance | Warming up 500 candles completes in **$\le 10$ milliseconds**. | `test_historical_warmup_performance()` |
| **AC-2.5** | Atomic Hot Swap Latency | Swapping active indicator for a symbol takes **$\le 1$ millisecond**. | `test_atomic_hot_swap_latency()` |
| **AC-2.6** | Zero Tick Loss During Swap | Rapidly streaming ticks while mounting a new indicator causes zero dropped ticks and no thread race conditions. | `test_zero_tick_loss_concurrency()` |
| **AC-2.7** | Signal Contract Integrity | Emitted `IndicatorSignal` conforms strictly to execution contract (`action`, `trigger_price`, `stop_loss`, `target`, `timestamp`). | `test_signal_contract_integrity()` |

---

## 6. Comprehensive Automated Test Suite (`tests/test_indicator_runtime.py`)

The test suite must implement:
1. `test_ast_security_validation()`: Attempts to compile malicious code snippets (`import os`, `subprocess.Popen`, `open('/etc/passwd')`), asserting `SecurityValidationError`.
2. `test_syntax_error_reporting()`: Passes malformed Python code, asserting exact line and column coordinates match the syntax error.
3. `test_compiler_and_loader()`: Validates that valid Python code subclassing `Indicator` is dynamically compiled into an active Python type.
4. `test_parameter_schema_extraction()`: Asserts that inputs declared in `init()` are parsed into matching JSON dictionary definitions.
5. `test_historical_warmup_performance()`: Creates a 1,000-candle synthetic DataFrame, warms up an indicator, and asserts execution time $\le 10$ms.
6. `test_atomic_hot_swap_latency()`: Times the atomic pointer swap in `DynamicIndicatorRegistry`, asserting $\le 1$ms.
7. `test_zero_tick_loss_concurrency()`: Runs a producer thread firing 10,000 ticks/sec while a consumer thread swaps indicators 100 times, asserting 0 dropped ticks and 0 exceptions.
8. `test_signal_routing()`: Injects tick that triggers an entry condition, verifying that `Signal` is dispatched to the callback with exact parameters.

---

## 7. PREREQUISITES & HAND-OFF CONTRACT FOR NEXT TASK (TASK 3)

### 7.1 Prerequisites Received from Task 1:
* Fully tested `quant_lib` (`ta`, `structure`, `order_flow`, `options`, `Indicator`, `input`).
* Clean module namespace ready for injection into the dynamic compiler sandbox.

### 7.2 Hand-Off Deliverables to Task 3 (Local Storage & Gateway):
Task 2 completes and hands off the following assets:
* ✅ **Dynamic Compiler Engine:** `IndicatorCompiler.compile_and_load(code)` to validate and compile Python source code into active `Indicator` classes.
* ✅ **Parameter Extractor:** `IndicatorCompiler.extract_param_schema(instance)` returning JSON schemas for UI sliders.
* ✅ **Warmup Pipeline:** `WarmupManager.warm_up_indicator(instance, df)` to prime rolling states with historical data.
* ✅ **Thread-Safe Live Registry:** `DynamicIndicatorRegistry` capable of mounting and evaluating indicators on live tick streams in $< 1$ms.
* ✅ **Consumption by Task 3:** Task 3 will build the storage and synchronization layer that supplies the historical and live feeds consumed by this runtime.

