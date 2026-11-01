# Indicator Studio: Master Implementation Plan & Task Index

> **Target System:** Local Split-Screen Quant Studio with Sub-20µs Backend Indicator Engine  
> **Deployment Model:** Strict Decoupling (Local Studio ➔ VPS Master Data Hub)  
> **Authoritative Specification:** This directory contains the exhaustive, standalone specification files for each task to guarantee zero context loss across the entire development lifecycle.

---

## 1. Hand-Off Chain of Custody & Execution Order

Every task receives prerequisites from the preceding task, executes and passes automated tests, and hands off clear, verified artifacts to the subsequent task:

```
┌─────────────────────────────────────────────────────────────────────────────┐
│ TASK 0: Project Scaffolding, Core Types & Shared Contracts                  │
│ • Directory namespaces, __init__.py exports, universal contracts           │
│ • Hands off to Task 1: quant_lib base classes & verified environment       │
└──────────────────────────────────────┬──────────────────────────────────────┘
                                       │
┌──────────────────────────────────────▼──────────────────────────────────────┐
│ TASK 1: Quant Mathematical Library (quant_lib)                              │
│ • Complete TA functions, Dynamic S/R, Breakout Boxes, Greeks, Pressure     │
│ • Hands off to Task 2: Vectorized & streaming mathematical modules         │
└──────────────────────────────────────┬──────────────────────────────────────┘
                                       │
┌──────────────────────────────────────▼──────────────────────────────────────┐
│ TASK 2: Dynamic Python Runtime & Hot-Reload Engine                          │
│ • AST sandbox validator, module compiler, dynamic registry, warmup runner   │
│ • Hands off to Task 3: In-memory live execution engine (< 1ms swap)         │
└──────────────────────────────────────┬──────────────────────────────────────┘
                                       │
┌──────────────────────────────────────▼──────────────────────────────────────┐
│ TASK 3: Local Storage & VPS Data Gateway                                    │
│ • DuckDB embedded cache, VPS REST/WS client, Spot + Option sync bus        │
│ • Hands off to Task 4: Unified historical & live market data feeds          │
└──────────────────────────────────────┬──────────────────────────────────────┘
                                       │
┌──────────────────────────────────────▼──────────────────────────────────────┐
│ TASK 4: Replay & Backtest Engine Core                                       │
│ • Zero-lookahead ReplaySession, single-bar stepping, "Break on Signal"      │
│ • Hands off to Task 5: Stepping iterator and frame serialization contracts │
└──────────────────────────────────────┬──────────────────────────────────────┘
                                       │
┌──────────────────────────────────────▼──────────────────────────────────────┐
│ TASK 5: Split-Screen Web Studio UI (Monaco + Canvas Chart)                  │
│ • Resizable split layout, Lightweight Charts, Monaco with quant_lib Intelli │
│ • Hands off to Task 6: Operational UI & live debugger workspace             │
└──────────────────────────────────────┬──────────────────────────────────────┘
                                       │
┌──────────────────────────────────────▼──────────────────────────────────────┐
│ TASK 6: Pre-Packaged Templates (BSS Pro v5) & End-to-End Validation         │
│ • Native Python BSS Pro v5, AlgoAlpha, presets loader, full system test    │
│ • Hands off to Trader: Fully validated, zero-latency Indicator Studio      │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## 2. Complete Task Specification Matrix

| Task File | Title | Primary Focus | Key Deliverables & Hand-Off |
| :--- | :--- | :--- | :--- |
| **[TASK 0](file:///Users/admin/Documents/Nimesh/Projects/Self%20Training%20Engine/docs/indicator_studio_tasks/TASK_0_SKELETON_AND_ARCHITECTURE.md)** | **Project Scaffolding & Core Contracts** | Architecture | Directory skeleton, shared contracts (`IndicatorSignal`, `InputDescriptor`, `BaseIndicator`), dependency checks. Hands off base to Task 1. |
| **[TASK 1](file:///Users/admin/Documents/Nimesh/Projects/Self%20Training%20Engine/docs/indicator_studio_tasks/TASK_1_QUANT_MATHEMATICAL_LIBRARY.md)** | **Quant Mathematical Library (`quant_lib`)** | Backend / Math | 10 MAs, RSI, MACD, Dynamic S/R body clustering, breakout channels, Greeks, dual mode ($< 15\mu s$). Hands off math to Task 2. |
| **[TASK 2](file:///Users/admin/Documents/Nimesh/Projects/Self%20Training%20Engine/docs/indicator_studio_tasks/TASK_2_DYNAMIC_RUNTIME_HOT_RELOAD.md)** | **Dynamic Runtime & Hot-Reload Engine** | Backend / Execution | AST validator, dynamic compiler, parameter schema extractor, atomic swap registry ($< 1$ms). Hands off runtime to Task 3. |
| **[TASK 3](file:///Users/admin/Documents/Nimesh/Projects/Self%20Training%20Engine/docs/indicator_studio_tasks/TASK_3_LOCAL_STORAGE_AND_VPS_GATEWAY.md)** | **Local Storage & VPS Data Gateway** | Backend / Data | VPS REST/WS client, embedded DuckDB local cache, Spot + Option sync bus. Hands off synced data to Task 4. |
| **[TASK 4](file:///Users/admin/Documents/Nimesh/Projects/Self%20Training%20Engine/docs/indicator_studio_tasks/TASK_4_REPLAY_AND_BACKTEST_ENGINE.md)** | **Replay & Backtest Engine Core** | Backend / Simulation | Zero-lookahead replay session, single-bar stepping, "Break on Signal" debugger, macro backtester. Hands off session to Task 5. |
| **[TASK 5](file:///Users/admin/Documents/Nimesh/Projects/Self%20Training%20Engine/docs/indicator_studio_tasks/TASK_5_SPLIT_SCREEN_STUDIO_UI.md)** | **Split-Screen Web Studio UI** | Frontend / UX | Resizable split view, Monaco Editor with IntelliSense, Lightweight Charts dual view, Variable Watcher. Hands off studio to Task 6. |
| **[TASK 6](file:///Users/admin/Documents/Nimesh/Projects/Self%20Training%20Engine/docs/indicator_studio_tasks/TASK_6_PREPACKAGED_INDICATORS_AND_TEMPLATES.md)** | **Pre-Packaged Templates & Validation** | Strategy / E2E | Native Python BSS Pro v5 & AlgoAlpha, preset loader, complete end-to-end replay & paper stream verification. |

---

## 3. Universal System Invariants

1. **Backend Execution Only:** All calculations occur in Python memory. The frontend is strictly a visualization and code-editing interface.
2. **Zero Direct Broker Contacts from Local Studio:** Local studio interfaces strictly with the VPS Data Gateway and local DuckDB cache to preserve broker rate limits and allow 24/7 off-hours work.
3. **Strict Zero-Lookahead Replay:** At step $T$, indicators can never access bars $> T$.
4. **Plug-and-Play Contract:** Every indicator compiles to a standard `BaseIndicator` adhering to `BaseStrategy`, mountable at runtime with zero server restart.
