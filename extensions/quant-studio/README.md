# Quant Studio for Visual Studio Code & Antigravity IDE

Production-grade Quantitative Indicator Development, Native Debugging (`debugpy`), and Real-Time TradingView Market Replay.

---

## Key Features

1. **True Native Python Debugging**:
   - Set breakpoints anywhere in your indicator `.py` files.
   - Press `F5` to start debugging under Python's native `debugpy`.
   - Step Over (`F10`) and Step Into (`F11`) with full Call Stack and Variable Watch support.

2. **Synchronized TradingView Chart Webview**:
   - Embedded TradingView Lightweight Charts rendered right next to your code (`ViewColumn.Beside`).
   - Candles, forming wicks, dynamic indicator lines (EMAs, Supertrend, Support/Resistance), and buy/sell signals update instantly on every step.

3. **Multi-Scale Real-Time Market Replay**:
   - Scrub through historical trading sessions with sub-millisecond precision.
   - Dual-chart synchronized visualization for underlying Spot Index (e.g. NIFTY) and Option Strikes (e.g. CE / PE).

4. **100% Dual-IDE Compatibility**:
   - Installs and operates identically in Visual Studio Code and Google Antigravity IDE.

---

## Quick Start

1. Open any indicator script (e.g. `trading_engine/indicators/bss_pro_v5.py`).
2. Click the **Quant Studio: Open Chart** button in the editor title bar (or press `Cmd+Shift+P` -> `Quant Studio: Open Chart`).
3. Press `F5` or click **Quant Studio: Debug Active Indicator** to launch synchronized debugging.
