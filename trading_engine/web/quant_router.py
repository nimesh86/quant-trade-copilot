"""
FastAPI Router for 3-Layer Quant Architecture:
───────────────────────────────────────────────
Exposes endpoints for Quant Core, Backtest Engine (Layer B), and Live Engine (Layer C).
Provides real backtest execution, performance reporting, and execution log persistence.
"""
from fastapi import APIRouter, HTTPException, Body, Query
from typing import Dict, Any, List, Optional
from pathlib import Path
import json
import asyncio
from datetime import datetime

from trading_engine.live_engine.engine import LiveEngine, format_trading_date_with_expiry

quant_router = APIRouter(prefix="/api/quant", tags=["quant-engine"])

REPORTS_DIR = Path(__file__).resolve().parent.parent.parent / "reports"
LOGS_FILE = REPORTS_DIR / "backtest_execution_logs.json"


def _load_persisted_logs() -> List[Dict[str, Any]]:
    """Loads saved backtest execution logs from disk."""
    if LOGS_FILE.exists():
        try:
            return json.loads(LOGS_FILE.read_text(encoding="utf-8"))
        except Exception:
            return []
    return []


def _save_persisted_logs(logs: List[Dict[str, Any]]):
    """Saves backtest execution logs to disk."""
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    LOGS_FILE.write_text(json.dumps(logs[:100], indent=2), encoding="utf-8")


from trading_engine.backtest_engine.engine import BacktestEngine

_backtest_engine = BacktestEngine()


@quant_router.post("/backtest/run")
async def run_quant_backtest(payload: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    """
    Executes standard quantitative backtest via Layer B Backtest Engine.
    Shared identically with Quant Studio VS Code Extension.
    """
    # Normalize input parameter keys
    date_from = payload.get("dateFrom") or payload.get("startDate") or "2026-10-29"
    date_to = payload.get("dateTo") or payload.get("endDate") or "2026-10-30"
    symbol = payload.get("symbol") or "NIFTY50"
    option_type = payload.get("optionType") or "ATM"
    capital = float(payload.get("capital") or 50000.0)
    
    strat = payload.get("strategy") or payload.get("indicator")
    if not strat and isinstance(payload.get("strategies"), list) and payload.get("strategies"):
        strat = payload["strategies"][0]
    elif not strat and isinstance(payload.get("indicators"), list) and payload.get("indicators"):
        strat = payload["indicators"][0]
    strategy = strat or "Adaptive Trend Pro"
    strat_lower = strategy.lower()
    default_sizing = "compounding_7pct" if ("xgboost" in strat_lower or "ai_trader" in strat_lower or "78l" in strat_lower or "macro" in strat_lower) else "conservative"

    params = {
        "symbol": symbol,
        "segment": payload.get("segment", "NSE"),
        "dateFrom": date_from,
        "dateTo": date_to,
        "optionType": option_type,
        "capital": capital,
        "strategy": strategy,
        "maxTradesPerDay": payload.get("maxTradesPerDay") or payload.get("max_trades_per_day") or 4,
        "blockedTimeSlots": payload.get("blockedTimeSlots") or payload.get("blocked_time_slots") or [],
        "sizingMode": payload.get("sizingMode") or payload.get("sizing_mode") or default_sizing
    }

    try:
        engine = BacktestEngine()
        loop = asyncio.get_running_loop()
        results = await loop.run_in_executor(None, engine.run_backtest, params)

        # Persist to quant_studio_state.json, last_backtest.json, and quant_studio_model.json
        try:
            state_file = REPORTS_DIR / "quant_studio_state.json"
            cur_state = {}
            if state_file.exists():
                with open(state_file, "r") as f:
                    cur_state = json.load(f)
            cur_state["parameters"] = params
            cur_state["last_backtest"] = results
            cur_state["last_updated"] = datetime.now().isoformat()
            with open(state_file, "w") as f:
                json.dump(cur_state, f, indent=2)

            bt_file = REPORTS_DIR / "last_backtest.json"
            with open(bt_file, "w") as f:
                json.dump(results, f, indent=2)

            model_file = REPORTS_DIR / "quant_studio_model.json"
            if model_file.exists():
                with open(model_file, "r") as f:
                    model_data = json.load(f)
            else:
                model_data = {}
            model_data["backtest_results"] = results
            model_data["summary"] = results.get("summary", {})
            model_data["trades"] = results.get("trades", [])
            with open(model_file, "w") as f:
                json.dump(model_data, f, indent=2)
        except Exception as err:
            pass

        return results
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Backtest engine error: {str(e)}")


@quant_router.get("/candles")
async def get_quant_candles(
    symbol: str = Query(..., description="Underlying or Option Contract symbol"),
    date: str = Query(..., description="Trading date YYYY-MM-DD")
) -> Dict[str, Any]:
    """
    Returns verified 1-minute historical candles for candlestick charting in Quant Studio.
    """
    clean_sym = symbol.strip().upper()
    date_str = date.strip()[:10]
    
    # If spot underlying requested
    if clean_sym in ["NIFTY", "NIFTY50", "BANKNIFTY", "SENSEX", "FINNIFTY"]:
        df = _backtest_engine.feeder.load_spot_candles(clean_sym, date_str, date_str)
        candles = []
        if not df.empty:
            for _, r in df.iterrows():
                t_val = r["timestamp"]
                t_str = t_val.strftime("%Y-%m-%d %H:%M") if hasattr(t_val, "strftime") else str(t_val)[:16]
                candles.append({
                    "time": t_str,
                    "open": float(r["open"]),
                    "high": float(r["high"]),
                    "low": float(r["low"]),
                    "close": float(r["close"]),
                    "volume": float(r.get("volume", 0))
                })
        return {"symbol": clean_sym, "date": date_str, "candles": candles}
    
    # Option contract candles
    candles = _backtest_engine.feeder.get_contract_day_candles(clean_sym, date_str)
    return {"symbol": clean_sym, "date": date_str, "candles": candles}


@quant_router.get("/backtest/logs")
async def get_backtest_logs() -> List[Dict[str, Any]]:
    """
    Returns persistent list of all executed backtests for the Dashboard Execution Log table.
    """
    logs = _load_persisted_logs()
    return logs


@quant_router.post("/backtest/logs")
async def save_backtest_log_entry(entry: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    """
    Appends an execution log entry to the persistent store.
    """
    logs = _load_persisted_logs()
    logs.insert(0, entry)
    _save_persisted_logs(logs)
    return {"status": "ok", "total_logs": len(logs)}


@quant_router.get("/live/logs")
async def get_live_logs() -> List[Dict[str, Any]]:
    """
    Returns live trading execution log rows formatted with 'Trading Date (ME)/(WE)'.
    """
    # Load from engine state or persistent live log
    live_file = REPORTS_DIR / "live_execution_logs.json"
    if live_file.exists():
        try:
            return json.loads(live_file.read_text(encoding="utf-8"))
        except Exception:
            return []
    return []
