"""
Production FastAPI Web Server & Real-Time Engine Controller.
─────────────────────────────────────────────────────────────
Supports 5 Operating Modes:
  1. Predictor: Live AI Directional Bias, Target/Stop Barriers & Strike Recommendation
  2. Paper Trading: Virtual Execution with Statutory Iceberg Order Slicing & Real Costs
  3. Backtest: Walk-Forward Historical Strategy Evaluation & Report Viewer
  4. Live: Real Broker Execution via Zerodha Kite Connect
  5. Simulation: Real-time Replay of any historical date at custom speed (1x to 100x),
     simulating live ticks, generating live chart bars, AI signals, and executing paper trades!

Serves Mobile-First Responsive UI with TradingView-style Candlestick Chart,
Real-Time AI Signals, Option Chain Matrix, and Paper Portfolio Tracker.
"""

import os
import sys

# Prevent macOS Intel OpenMP threadpool suspension crash (__kmp_suspend_64 SIGSEGV)
os.environ["OMP_NUM_THREADS"] = "1"
os.environ["OPENBLAS_NUM_THREADS"] = "1"
os.environ["MKL_NUM_THREADS"] = "1"
os.environ["VECLIB_MAXIMUM_THREADS"] = "1"
os.environ["NUMEXPR_NUM_THREADS"] = "1"

import time
import json
import asyncio
from datetime import datetime, timezone, time as dtime
from pathlib import Path
from typing import Dict, Any, List, Optional

import numpy as np
import pandas as pd
from sqlalchemy import create_engine, text
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Query, Body, HTTPException, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse, HTMLResponse
from fastapi.middleware.cors import CORSMiddleware

# Ensure trading_engine directory and project root are on sys.path
ENGINE_DIR = Path(__file__).resolve().parent.parent
PROJECT_ROOT = ENGINE_DIR.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
if str(ENGINE_DIR) not in sys.path:
    sys.path.insert(0, str(ENGINE_DIR))
WEB_DIR = Path(__file__).resolve().parent
if str(WEB_DIR) not in sys.path:
    sys.path.insert(0, str(WEB_DIR))

try:
    import trading_engine.web.contract_data_manager as _cdm
except ImportError:
    import contract_data_manager as _cdm

# Register module aliases in sys.modules so ANY import gets the same singleton instance
sys.modules["contract_data_manager"] = _cdm
sys.modules["web.contract_data_manager"] = _cdm
sys.modules["trading_engine.web.contract_data_manager"] = _cdm

search_contracts        = _cdm.search_contracts
fetch_today_candles     = _cdm.fetch_today_candles
fetch_contract_candles  = getattr(_cdm, "fetch_contract_candles", _cdm.fetch_today_candles)
is_pre_market           = _cdm.is_pre_market
is_market_open          = _cdm.is_market_open
get_today_str           = _cdm.get_today_str
get_live_ltp            = _cdm.get_live_ltp
update_forming_candle   = _cdm.update_forming_candle
get_forming_candle      = _cdm.get_forming_candle
register_contract_client   = _cdm.register_contract_client
unregister_contract_client = _cdm.unregister_contract_client
get_available_expiries     = _cdm.get_available_expiries
get_option_chain_data      = _cdm.get_option_chain_data
push_to_price_bus          = _cdm.push_to_price_bus
get_all_subscribed_symbols = _cdm.get_all_subscribed_symbols
get_contract_clients       = _cdm.get_contract_clients
get_all_live_prices        = _cdm.get_all_live_prices
get_contract_quote         = _cdm.get_contract_quote

try:
    import trading_engine.web.trend_event_manager as _tem
except ImportError:
    import trend_event_manager as _tem

sys.modules["trend_event_manager"] = _tem
sys.modules["web.trend_event_manager"] = _tem
sys.modules["trading_engine.web.trend_event_manager"] = _tem
record_trend_event      = _tem.record_trend_event
get_recent_trend_events = _tem.get_recent_trend_events

from config import EngineConfig
from adapters.backtest_adapter import BacktestAdapter
from adapters.paper_adapter import PaperAdapter

try:
    from execution.contract_engine import contract_engine
except ImportError:
    from trading_engine.execution.contract_engine import contract_engine

app = FastAPI(title="AI Trader Autonomous Engine", version="2.5.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.middleware("http")
async def add_no_cache_headers(request: Request, call_next):
    response = await call_next(request)
    if request.url.path.startswith("/static") or request.url.path == "/":
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate, max-age=0"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    return response

STATIC_DIR = Path(__file__).resolve().parent / "static"
PAPER_STATE_FILE = Path(__file__).resolve().parent / "paper_trades_state.json"


def is_market_open_now() -> bool:
    """Returns True if NSE market is currently in active trading hours (09:15 - 15:30 IST Mon-Fri)."""
    try:
        from zoneinfo import ZoneInfo
        tz = ZoneInfo("Asia/Kolkata")
    except Exception:
        from datetime import timezone, timedelta
        tz = timezone(timedelta(hours=5, minutes=30))
    now = datetime.now(tz)
    if now.weekday() >= 5:  # Saturday or Sunday
        return False
    market_open = now.replace(hour=9, minute=15, second=0, microsecond=0)
    market_close = now.replace(hour=15, minute=30, second=0, microsecond=0)
    return market_open <= now <= market_close


def is_pre_market_today() -> bool:
    """Returns True if today is a weekday and current time is before 09:15 AM IST."""
    try:
        from zoneinfo import ZoneInfo
        tz = ZoneInfo("Asia/Kolkata")
    except Exception:
        from datetime import timezone, timedelta
        tz = timezone(timedelta(hours=5, minutes=30))
    now = datetime.now(tz)
    if now.weekday() >= 5:
        return False
    return now.time() < dtime(9, 15)


# Engine State Container
class EngineState:
    def __init__(self):
        self.cfg = EngineConfig()
        self.mode = "predictor" # 'predictor', 'paper', 'backtest', 'live', 'simulation'
        self.live_paper_adapter = PaperAdapter(self.cfg)
        self.sim_paper_adapter = PaperAdapter(self.cfg)
        self.db_engine = create_engine(self.cfg.db_url)
        
        # Real-time simulated/live market status
        self.current_spot = 0.0
        self.current_atr = 15.0
        self.current_rsi = 50.0
        self.current_vwap = 0.0
        
        # AI Predictor Output (Initialized dynamically on first tick or request)
        self.prediction = {
            "active": False,
            "status": "INITIALIZING",
            "message": "Initializing live market feed...",
            "suggested_strike": None,
            "direction": None,
            "bias": "INITIALIZING",
            "confidence_pct": 0,
            "model_1_score": 0.0,
            "model_2_status": "IDLE",
            "entry_range": "--",
            "entry_premium": 0,
            "target_premium": 0,
            "stop_loss_premium": 0,
            "expected_gain_pct": 0,
            "max_risk_pct": 0,
            "timestamp": datetime.now().isoformat(),
            "spot": 0.0,
            "atr": 0.0,
            "rsi": 50.0,
            "vwap": 0.0
        }

        # Simulation Replay Engine State
        self.simulation_active = False
        self.simulation_paused = False
        self.simulation_date = "2026-10-09"
        self.simulation_speed = 10
        self.simulation_task = None
        self.simulation_run_id = 0
        self.simulation_candles: List[Dict[str, Any]] = []
        self.simulation_markers: List[Dict[str, Any]] = []
        self.simulation_progress = {
            "current_bar": 0,
            "total_bars": 0,
            "current_time": "",
            "active": False,
            "paused": False
        }

        # Real-time intra-minute forming candle & live tick task
        self.forming_candle: Optional[Dict[str, Any]] = None
        self.tick_broadcaster_task = None

        # WebSocket clients pool
        self.active_websockets: List[WebSocket] = []

        # Parallel Strategy Detector
        from adapters.strategy_detector_adapter import StrategyDetectorAdapter
        self.strategy_detector = StrategyDetectorAdapter(self.cfg)

        # Restore persistent paper trading portfolio & trade history
        self.load_paper_state()

        # Cloudflare Tunnel Supervisor Pipeline
        self.tunnel_pipeline = None
        self.tunnel_task = None

    def save_paper_state(self):
        """Persists live paper adapter trades & balance to disk so user results are never lost."""
        try:
            data = {
                "capital": self.live_paper_adapter.capital,
                "trade_history": self.live_paper_adapter.trade_history,
                "updated_at": datetime.now().isoformat()
            }
            with open(PAPER_STATE_FILE, "w") as f:
                json.dump(data, f, indent=2, default=str)
        except Exception as e:
            print(f"Error saving paper trades state: {e}")

    def load_paper_state(self):
        """Restores persistent paper trading trades from disk."""
        if PAPER_STATE_FILE.exists():
            try:
                with open(PAPER_STATE_FILE, "r") as f:
                    data = json.load(f)
                    if "capital" in data and float(data["capital"]) > 0:
                        self.live_paper_adapter.capital = float(data["capital"])
                    if "trade_history" in data and isinstance(data["trade_history"], list):
                        self.live_paper_adapter.trade_history = data["trade_history"]
                        print(f"EngineState: Restored {len(self.live_paper_adapter.trade_history)} persistent paper trades. Capital: ₹{self.live_paper_adapter.capital:,.2f}", flush=True)
            except Exception as e:
                print(f"Error loading paper trades state: {e}")

    @property
    def paper_adapter(self) -> PaperAdapter:
        if self.mode == "simulation":
            return self.sim_paper_adapter
        return self.live_paper_adapter

    @paper_adapter.setter
    def paper_adapter(self, adapter: PaperAdapter):
        if self.mode == "simulation":
            self.sim_paper_adapter = adapter
        else:
            self.live_paper_adapter = adapter

state = EngineState()


def build_option_chain_data(spot: float) -> Dict[str, Any]:
    """Generates real-time 11-strike option chain centered around dynamic ATM."""
    atm_strike = int(round(spot / 50.0) * 50)
    strikes = [atm_strike + (i * 50) for i in range(-5, 6)]
    chain_rows = []
    for s in strikes:
        dist = s - spot
        call_iv = round(12.5 + abs(dist) * 0.004, 2)
        call_delta = round(max(0.05, min(0.95, 0.50 - (dist / 400.0))), 2)
        call_ltp = round(max(5.0, 160.0 - (dist * 0.55)), 2)
        call_oi = int(max(1000, 45000 - abs(dist) * 45))

        put_iv = round(13.2 + abs(dist) * 0.004, 2)
        put_delta = round(max(-0.95, min(-0.05, -0.50 - (dist / 400.0))), 2)
        put_ltp = round(max(5.0, 160.0 + (dist * 0.55)), 2)
        put_oi = int(max(1000, 42000 - abs(dist) * 40))

        chain_rows.append({
            "strike": s,
            "is_atm": (s == atm_strike),
            "call_ltp": call_ltp,
            "call_iv": call_iv,
            "call_delta": call_delta,
            "call_oi": call_oi,
            "put_ltp": put_ltp,
            "put_iv": put_iv,
            "put_delta": put_delta,
            "put_oi": put_oi,
        })
    return {
        "underlying": "NIFTY 50",
        "spot": round(spot, 2),
        "atm_strike": atm_strike,
        "chain": chain_rows
    }


def update_dynamic_prediction(spot: float, atr: float, rsi: float = 50.0, vwap: float = 0.0) -> Dict[str, Any]:
    """Dynamically updates suggested strike, option premiums, and profit targets with zero lag."""
    if state.mode != "simulation" and not is_market_open_now():
        inactive = {
            "active": False,
            "status": "MARKET_CLOSED",
            "message": "Market is closed. Real-time AI suggestions resume at 09:15 AM IST.",
            "suggested_strike": None,
            "direction": None,
            "bias": "MARKET CLOSED",
            "confidence_pct": 0,
            "model_1_score": 0.0,
            "model_2_status": "IDLE",
            "entry_range": "--",
            "entry_premium": 0,
            "target_premium": 0,
            "stop_loss_premium": 0,
            "expected_gain_pct": 0,
            "max_risk_pct": 0,
            "timestamp": datetime.now().isoformat(),
            "spot": round(spot, 2),
            "atr": round(atr, 2),
            "rsi": round(rsi, 2),
            "vwap": round(vwap, 2)
        }
        state.prediction = inactive
        return inactive

    # In live mode, preserve true multi-factor model prediction from compute_live_market_prediction
    if state.mode != "simulation":
        curr = state.prediction
        if curr and curr.get("status") in ("NO_SETUP", "MARKET_CLOSED", "INSUFFICIENT_DATA"):
            curr["spot"] = round(spot, 2)
            curr["atr"] = round(atr, 2)
            curr["rsi"] = round(rsi, 2)
            curr["vwap"] = round(vwap, 2)
            return curr

        if curr and curr.get("active") and curr.get("suggested_strike"):
            last_spot = curr.get("spot", spot)
            delta = 0.55
            pts_diff = (spot - last_spot) if curr.get("direction") == "CALL" else (last_spot - spot)
            curr["spot"] = round(spot, 2)
            curr["entry_range"] = f"{round(spot - 4, 1)} - {round(spot + 4, 1)}"
            if pts_diff != 0 and curr.get("entry_premium"):
                new_prem = round(max(5.0, curr["entry_premium"] + (pts_diff * delta)), 1)
                curr["entry_premium"] = new_prem
            return curr

        return compute_live_market_prediction()

    # In simulation mode:
    atm_strike = int(round(spot / 50.0) * 50)
    is_bullish = rsi >= 50.0
    direction = "CALL" if is_bullish else "PUT"
    strike_code = "CE" if is_bullish else "PE"
    bias = ("BULLISH " + ("BREAKOUT" if rsi > 62 else "TREND")) if is_bullish else ("BEARISH " + ("BREAKDOWN" if rsi < 38 else "TREND"))
    conf = min(89.5, max(62.0, 60.0 + abs(rsi - 50.0) * 1.1))

    intrinsic_approx = (spot - atm_strike) if strike_code == "CE" else (atm_strike - spot)
    est_prem = max(20.0, round(atr * 5.2 + intrinsic_approx * 0.55, 1))
    tgt_pts = round(max(10.0, atr * 0.75), 1)
    sl_pts = round(max(6.0, atr * 0.45), 1)
    target_prem = round(est_prem + (tgt_pts * 0.55), 1)
    stop_prem = round(max(2.0, est_prem - (sl_pts * 0.40)), 1)
    exp_gain = round(((target_prem - est_prem) / est_prem) * 100, 1) if est_prem > 0 else 14.2
    max_risk = round(((est_prem - stop_prem) / est_prem) * 100, 1) if est_prem > 0 else 8.5

    pred = {
        "active": True,
        "status": "ACTIVE_SIGNAL",
        "direction": direction,
        "bias": bias,
        "confidence_pct": round(conf, 1),
        "model_1_score": round(conf / 100.0, 3),
        "model_2_status": "CONFIRMED_MOMENTUM" if conf > 75 else "MOMENTUM_WATCH",
        "suggested_strike": f"NIFTY {atm_strike} {strike_code}",
        "entry_range": f"{round(spot - 4, 1)} - {round(spot + 4, 1)}",
        "entry_premium": est_prem,
        "target_premium": target_prem,
        "stop_loss_premium": stop_prem,
        "expected_gain_pct": exp_gain,
        "max_risk_pct": max_risk,
        "timestamp": datetime.now().isoformat(),
        "spot": round(spot, 2),
        "atr": round(atr, 2),
        "rsi": round(rsi, 2),
        "vwap": round(vwap, 2)
    }
    state.prediction = pred
    return pred


def get_dynamic_strategy_suggestions() -> List[Dict[str, Any]]:
    """Enriches detected strategy suggestions with real-time calculated option premiums."""
    suggestions = []
    spot = state.current_spot
    atr = state.current_atr or 20.0
    for s in state.strategy_detector.active_suggestions:
        p = dict(s.gtt_payload)
        atm = int(round(spot / 50.0) * 50)
        p["current_spot"] = round(spot, 2)
        side = s.side
        dist = (spot - atm) if side == "CE" else (atm - spot)
        p["current_premium"] = max(15.0, round(atr * 5.0 + dist * 0.55, 1))
        suggestions.append(p)
    return suggestions


# ── REST API Endpoints ────────────────────────────────────────────────────────

@app.get("/api/status")
async def get_status():
    """Returns engine system status, current mode, capital, and active trade."""
    if state.mode != "simulation":
        try:
            with state.db_engine.connect() as conn:
                row = conn.execute(text("SELECT close FROM minute_candles WHERE symbol = 'NIFTY-I' ORDER BY timestamp DESC LIMIT 1")).fetchone()
                if row and row[0]:
                    state.current_spot = float(row[0])
        except Exception:
            pass

    pos = state.paper_adapter.active_position
    summary = state.paper_adapter.get_portfolio_summary()

    # Calculate floating unrealized PnL if active position exists
    unrealized_pnl = 0.0
    if pos:
        side = pos["side"]
        current_px = state.current_spot
        entry_px = pos["entry_price"]
        pts = (current_px - entry_px) if side == "CE" else (entry_px - current_px)
        opt_pts = pts * 0.65
        unrealized_pnl = round(opt_pts * pos["units"], 2)

    try:
        from zoneinfo import ZoneInfo
        tz_kolkata = ZoneInfo("Asia/Kolkata")
    except Exception:
        from datetime import timezone, timedelta
        tz_kolkata = timezone(timedelta(hours=5, minutes=30))
    today_str = datetime.now(tz_kolkata).strftime("%Y-%m-%d")

    return {
        "mode": state.mode,
        "symbol": state.cfg.symbol,
        "server_date": today_str,
        "market_open": is_market_open_now(),
        "spot_price": round(state.current_spot, 2),
        "capital": round(state.paper_adapter.capital, 2),
        "paper_capital": round(state.live_paper_adapter.capital, 2),
        "simulation_capital": round(state.sim_paper_adapter.capital, 2),
        "unrealized_pnl": unrealized_pnl,
        "realized_pnl": round(state.paper_adapter.capital - state.cfg.initial_capital, 2),
        "total_trades": len(state.paper_adapter.trade_history),
        "win_rate": summary.get("win_rate", 0.0),
        "active_position": pos,
        "prediction": state.prediction,
        "option_chain": build_option_chain_data(state.current_spot),
        "iceberg_ceiling_lots": state.cfg.max_lots,
        "iceberg_leg_freeze": state.cfg.freeze_lots,
        "simulation": state.simulation_progress,
        "strategy_suggestions": get_dynamic_strategy_suggestions(),
        "strategy_count": len(state.strategy_detector.active_suggestions)
    }


@app.get("/api/strategy/suggestions")
async def get_strategy_suggestions():
    """Returns real-time strategy detection signals and GTT recommendations."""
    return {
        "active_suggestions": get_dynamic_strategy_suggestions(),
        "history": state.strategy_detector.detection_history,
        "detector_active": True,
        "strategies": [s.name for s in state.strategy_detector.strategies]
    }


@app.post("/api/mode")
async def set_mode(payload: Dict[str, Any] = Body(...)):
    """Switches system operational mode."""
    target_mode = payload.get("mode", "").lower()
    if target_mode not in ["predictor", "paper", "backtest", "live", "simulation"]:
        raise HTTPException(status_code=400, detail="Invalid mode.")
    state.mode = target_mode
    if target_mode != "simulation":
        state.simulation_active = False
        state.simulation_paused = False
        if state.simulation_task and not state.simulation_task.done():
            state.simulation_task.cancel()
        state.simulation_progress["active"] = False
    await broadcast_state()
    return {"status": "success", "mode": state.mode}





def compute_live_market_prediction() -> Dict[str, Any]:
    """Dynamically computes real-time AI Direction, Technical Regimes, 
    True ATM Strike, and Realistic Option Targets from live minute candles."""
    # When market is closed and not in simulation, return inactive prediction (no suggestions)
    if state.mode != "simulation" and not is_market_open_now():
        inactive_pred = {
            "active": False,
            "status": "MARKET_CLOSED",
            "message": "Market is closed. Real-time AI suggestions resume at 09:15 AM IST.",
            "suggested_strike": None,
            "direction": None,
            "bias": "MARKET CLOSED",
            "confidence_pct": 0,
            "model_1_score": 0.0,
            "model_2_status": "IDLE",
            "entry_range": "--",
            "entry_premium": 0,
            "target_premium": 0,
            "stop_loss_premium": 0,
            "expected_gain_pct": 0,
            "max_risk_pct": 0,
            "timestamp": datetime.now().isoformat(),
            "spot": state.current_spot,
            "atr": state.current_atr,
            "rsi": state.current_rsi,
            "vwap": state.current_vwap
        }
        state.prediction = inactive_pred
        return inactive_pred

    try:
        try:
            from zoneinfo import ZoneInfo
            tz_kolkata = ZoneInfo("Asia/Kolkata")
        except Exception:
            from datetime import timezone, timedelta
            tz_kolkata = timezone(timedelta(hours=5, minutes=30))
        today_str = datetime.now(tz_kolkata).strftime("%Y-%m-%d")

        q = text("""
            SELECT timestamp, open, high, low, close, volume 
            FROM minute_candles 
            WHERE symbol = 'NIFTY-I'
              AND (timestamp AT TIME ZONE 'Asia/Kolkata')::date = :today_date
            ORDER BY timestamp DESC 
            LIMIT 60
        """)
        with state.db_engine.connect() as conn:
            df = pd.read_sql(q, conn, params={"today_date": today_str})

        if df.empty or len(df) < 5:
            insufficient_pred = {
                "active": False,
                "status": "INSUFFICIENT_DATA",
                "message": "Awaiting initial session bars (09:15 AM onwards)...",
                "suggested_strike": None,
                "direction": None,
                "bias": "GATHERING DATA",
                "confidence_pct": 0,
                "model_1_score": 0.0,
                "model_2_status": "AWAITING_BARS",
                "entry_range": "--",
                "entry_premium": 0,
                "target_premium": 0,
                "stop_loss_premium": 0,
                "expected_gain_pct": 0,
                "max_risk_pct": 0,
                "timestamp": datetime.now().isoformat(),
                "spot": state.current_spot,
                "atr": state.current_atr,
                "rsi": state.current_rsi,
                "vwap": state.current_vwap
            }
            state.prediction = insufficient_pred
            return insufficient_pred

        df = df.iloc[::-1].reset_index(drop=True)

        # 1. True Range & ATR
        df["tr"] = np.maximum(df["high"] - df["low"], 
                   np.maximum(abs(df["high"] - df["close"].shift(1)), 
                              abs(df["low"] - df["close"].shift(1))))
        atr = float(df["tr"].rolling(14, min_periods=3).mean().iloc[-1])
        if np.isnan(atr) or atr <= 0:
            atr = 18.5

        # 2. RSI (14)
        delta = df["close"].diff()
        gain = delta.clip(lower=0).rolling(14, min_periods=3).mean()
        loss = (-delta.clip(upper=0)).rolling(14, min_periods=3).mean()
        rs = gain / (loss + 1e-9)
        rsi = float((100 - (100 / (1 + rs))).iloc[-1])
        if np.isnan(rsi):
            rsi = 50.0

        # 3. Session VWAP & EMAs
        df["tp"] = (df["high"] + df["low"] + df["close"]) / 3.0
        vwap = float(((df["tp"] * df["volume"]).cumsum() / (df["volume"].cumsum() + 1e-9)).iloc[-1])
        ema9 = float(df["close"].ewm(span=9).mean().iloc[-1])
        ema21 = float(df["close"].ewm(span=21).mean().iloc[-1])
        close = float(df["close"].iloc[-1])

        # Update engine state indicators
        state.current_spot = close
        state.current_atr = round(atr, 2)
        state.current_rsi = round(rsi, 2)
        state.current_vwap = round(vwap, 2)

        # 4. Multi-Factor Directional Classification
        is_bullish = (close > vwap) and (ema9 >= ema21) and (rsi >= 52.0)
        is_bearish = (close < vwap) and (ema9 <= ema21) and (rsi <= 48.0)

        if not is_bullish and not is_bearish:
            no_setup_pred = {
                "active": False,
                "status": "NO_SETUP",
                "message": "Market in consolidation. No active high-conviction trade setup.",
                "suggested_strike": None,
                "direction": None,
                "bias": "CONSOLIDATION",
                "confidence_pct": 0,
                "model_1_score": 0.0,
                "model_2_status": "MOMENTUM_WATCH",
                "entry_range": "--",
                "entry_premium": 0,
                "target_premium": 0,
                "stop_loss_premium": 0,
                "expected_gain_pct": 0,
                "max_risk_pct": 0,
                "timestamp": datetime.now().isoformat(),
                "spot": round(close, 2),
                "atr": round(atr, 2),
                "rsi": round(rsi, 2),
                "vwap": round(vwap, 2)
            }
            state.prediction = no_setup_pred
            return no_setup_pred

        if is_bullish:
            direction = "CALL"
            bias = "BULLISH BREAKOUT" if rsi > 65 else "BULLISH TREND"
            conf = min(88.5, max(65.0, 60.0 + (rsi - 50.0) * 0.75 + min(15.0, (close - vwap) / max(1.0, atr) * 3.0)))
        else:
            direction = "PUT"
            bias = "BEARISH BREAKDOWN" if rsi < 35 else "BEARISH TREND"
            conf = min(88.5, max(65.0, 60.0 + (50.0 - rsi) * 0.75 + min(15.0, (vwap - close) / max(1.0, atr) * 3.0)))

        atm_strike = int(round(close / 50.0) * 50)
        strike_code = "CE" if direction == "CALL" else "PE"
        suggested_strike = f"NIFTY {atm_strike} {strike_code}"

        # 5. Look up real option premium if available in database or price cache
        opt_price = None
        try:
            q_opt = text("""
                SELECT close 
                FROM minute_candles 
                WHERE symbol LIKE 'NIFTY%' AND symbol LIKE :pat
                ORDER BY timestamp DESC 
                LIMIT 1
            """)
            with state.db_engine.connect() as conn:
                row = conn.execute(q_opt, {"pat": f"%{atm_strike}{strike_code}"}).fetchone()
                if row and row[0] and float(row[0]) > 2.0:
                    opt_price = float(row[0])
        except Exception:
            pass

        if opt_price is None or opt_price <= 0:
            opt_price = max(45.0, round(atr * 5.2, 1))

        tgt_pts = round(max(8.0, atr * 0.75 * 0.55), 1)
        sl_pts = round(max(5.0, atr * 1.25 * 0.40), 1)

        entry_prem = round(opt_price, 1)
        target_prem = round(entry_prem + tgt_pts, 1)
        stop_prem = round(max(2.0, entry_prem - sl_pts), 1)

        exp_gain_pct = round((tgt_pts / entry_prem) * 100, 1) if entry_prem > 0 else 14.2
        max_risk_pct = round((sl_pts / entry_prem) * 100, 1) if entry_prem > 0 else 8.5

        pred = {
            "active": True,
            "status": "ACTIVE_SIGNAL",
            "direction": direction,
            "bias": bias,
            "confidence_pct": round(conf, 1),
            "model_1_score": round(conf / 100.0, 3),
            "model_2_status": "CONFIRMED_TREND" if conf > 70 else "MOMENTUM_WATCH",
            "suggested_strike": suggested_strike,
            "entry_range": f"{int(close - 8):,} - {int(close + 8):,}",
            "entry_premium": entry_prem,
            "target_premium": target_prem,
            "stop_loss_premium": stop_prem,
            "expected_gain_pct": exp_gain_pct,
            "max_risk_pct": max_risk_pct,
            "timestamp": datetime.now().isoformat(),
            "spot": round(close, 2),
            "atr": round(atr, 2),
            "rsi": round(rsi, 2),
            "vwap": round(vwap, 2)
        }
        state.prediction = pred
        return pred

    except Exception:
        return state.prediction


@app.get("/api/prediction")
async def get_prediction():
    """Returns real-time AI Directional Prediction and Strike Suggestions."""
    if state.mode == "simulation":
        return state.prediction
    return compute_live_market_prediction()


@app.get("/api/candles")
async def get_candles(limit: int = Query(375, ge=10, le=1000), date: Optional[str] = Query(None)):
    """Returns 1-minute spot candles for the requested date (or active session) with markers."""
    if state.mode == "simulation" and state.simulation_active and not date:
        return {
            "date": state.simulation_date,
            "candles": state.simulation_candles,
            "markers": state.simulation_markers
        }

    try:
        try:
            from zoneinfo import ZoneInfo
            tz_kolkata = ZoneInfo("Asia/Kolkata")
        except Exception:
            from datetime import timezone, timedelta
            tz_kolkata = timezone(timedelta(hours=5, minutes=30))
        now_ist = datetime.now(tz_kolkata)
        today_str = now_ist.strftime("%Y-%m-%d")

        target_date = date.strip() if date and len(date.strip()) == 10 else None
        is_weekday = now_ist.weekday() < 5
        resolved_date = target_date or today_str

        if target_date:
            # Explicit Session Date Requested - query that exact date without prepending prior sessions
            resolved_date = target_date
            if target_date == today_str and is_pre_market_today():
                df = pd.DataFrame(columns=["timestamp", "open", "high", "low", "close", "volume"])
            else:
                q_target = text("""
                    SELECT timestamp, open, high, low, close, volume 
                    FROM minute_candles 
                    WHERE symbol = 'NIFTY-I'
                      AND (timestamp AT TIME ZONE 'Asia/Kolkata')::date = :target_date
                    ORDER BY timestamp ASC
                """)
                with state.db_engine.connect() as conn:
                    df = pd.read_sql(q_target, conn, params={"target_date": target_date})
        else:
            # Active Day (Today) or Default Session
            if is_weekday and is_pre_market_today():
                resolved_date = today_str
                df = pd.DataFrame(columns=["timestamp", "open", "high", "low", "close", "volume"])
            elif is_market_open_now():
                resolved_date = today_str
                # Query today's candles only (do not prepend yesterday's tail)
                q_today = text("""
                    SELECT timestamp, open, high, low, close, volume 
                    FROM minute_candles 
                    WHERE symbol = 'NIFTY-I'
                      AND (timestamp AT TIME ZONE 'Asia/Kolkata')::date = :today_date
                    ORDER BY timestamp ASC
                """)
                with state.db_engine.connect() as conn:
                    df = pd.read_sql(q_today, conn, params={"today_date": today_str})
            else:
                # Outside market hours on weekday evening or weekend
                q_today = text("""
                    SELECT timestamp, open, high, low, close, volume 
                    FROM minute_candles 
                    WHERE symbol = 'NIFTY-I'
                      AND (timestamp AT TIME ZONE 'Asia/Kolkata')::date = :today_date
                    ORDER BY timestamp ASC
                """)
                with state.db_engine.connect() as conn:
                    df = pd.read_sql(q_today, conn, params={"today_date": today_str})

                if df.empty:
                    # Fallback to latest available trading day in DB
                    q_latest = text("""
                        SELECT DISTINCT (timestamp AT TIME ZONE 'Asia/Kolkata')::date as d
                        FROM minute_candles
                        WHERE symbol = 'NIFTY-I'
                        ORDER BY d DESC
                        LIMIT 1
                    """)
                    with state.db_engine.connect() as conn:
                        latest_row = conn.execute(q_latest).fetchone()
                        resolved_date = str(latest_row[0]) if latest_row else today_str

                    q_session = text("""
                        SELECT timestamp, open, high, low, close, volume 
                        FROM minute_candles 
                        WHERE symbol = 'NIFTY-I'
                          AND (timestamp AT TIME ZONE 'Asia/Kolkata')::date = :d
                        ORDER BY timestamp ASC
                    """)
                    with state.db_engine.connect() as conn:
                        df = pd.read_sql(q_session, conn, params={"d": resolved_date})

        candles = []
        if not df.empty and "timestamp" in df.columns:
            ts_series = pd.to_datetime(df["timestamp"])
            if ts_series.dt.tz is None:
                ts_series = ts_series.dt.tz_localize("UTC").dt.tz_convert("Asia/Kolkata")
            else:
                ts_series = ts_series.dt.tz_convert("Asia/Kolkata")
            df["timestamp"] = ts_series
            df = df.sort_values("timestamp").reset_index(drop=True)

            for _, row in df.iterrows():
                candles.append({
                    "time": row["timestamp"].strftime("%Y-%m-%d %H:%M"),
                    "timestamp": int(row["timestamp"].timestamp()),
                    "open": round(float(row["open"]), 2),
                    "high": round(float(row["high"]), 2),
                    "low": round(float(row["low"]), 2),
                    "close": round(float(row["close"]), 2),
                    "volume": int(row["volume"]),
                })

        # Append or update real-time forming candle if currently live during active market hours
        if is_market_open_now() and (resolved_date == today_str or not target_date or target_date == today_str):
            now_ts = int(now_ist.timestamp())
            bar_ts = (now_ts // 60) * 60
            if not state.forming_candle:
                spot = state.current_spot or (candles[-1]["close"] if candles else 22620.0)
                state.forming_candle = {
                    "time": bar_ts,
                    "open": spot,
                    "high": spot,
                    "low": spot,
                    "close": spot,
                    "volume": 0
                }
            fc = state.forming_candle
            if candles and candles[-1]["timestamp"] == fc["time"]:
                candles[-1]["high"] = max(candles[-1]["high"], round(fc["high"], 2))
                candles[-1]["low"] = min(candles[-1]["low"], round(fc["low"], 2))
                candles[-1]["close"] = round(fc["close"], 2)
            elif not candles or fc["time"] > candles[-1]["timestamp"]:
                candles.append({
                    "time": datetime.fromtimestamp(fc["time"], tz=tz_kolkata).strftime("%Y-%m-%d %H:%M"),
                    "timestamp": fc["time"],
                    "open": round(fc["open"], 2),
                    "high": round(fc["high"], 2),
                    "low": round(fc["low"], 2),
                    "close": round(fc["close"], 2),
                    "volume": int(fc.get("volume", 0)),
                })
            state.current_spot = fc["close"]
        elif candles:
            state.current_spot = candles[-1]["close"]

        markers = []
        target_adapter = state.sim_paper_adapter if (state.mode == "simulation") else state.live_paper_adapter
        for trade in target_adapter.trade_history:
            try:
                entry_time_str = str(trade.get("entry_time", ""))
                if not entry_time_str.startswith(resolved_date):
                    continue
                entry_dt = pd.to_datetime(trade.get("entry_time"))
                side = trade.get("side", "CE")
                markers.append({
                    "time": int(entry_dt.timestamp()),
                    "position": "belowBar" if side == "CE" else "aboveBar",
                    "color": "#00f2a9" if side == "CE" else "#ff3860",
                    "shape": "arrowUp" if side == "CE" else "arrowDown",
                    "text": f"▲ BUY {side} @ {trade.get('entry_price', 0):.0f}",
                    "type": f"ENTRY_{side}"
                })
                exit_dt = pd.to_datetime(trade.get("exit_time"))
                if exit_dt and pd.notna(exit_dt):
                    is_win = trade.get("is_win", False)
                    markers.append({
                        "time": int(exit_dt.timestamp()),
                        "position": "aboveBar" if is_win else "belowBar",
                        "color": "#00d2ff" if is_win else "#ff3860",
                        "shape": "flag",
                        "text": f"{'★ TGT' if is_win else '✖ SL'} {trade.get('net_pnl_inr', 0):+,.0f}",
                        "type": "EXIT_TARGET" if is_win else "EXIT_SL"
                    })
            except Exception:
                pass

        # Evaluate strategies on historical session using an isolated detector instance so state.strategy_detector is not mutated
        if target_date and candles:
            try:
                from strategy.amd_volume_profile import AMDVolumeProfileStrategy
                isolated_strat = AMDVolumeProfileStrategy(state.cfg)
                isolated_strat.reset_session()
                for idx_c, c_item in enumerate(candles):
                    try:
                        c_ts = pd.to_datetime(c_item["time"])
                        if c_ts.tz is None:
                            c_ts = c_ts.tz_localize("Asia/Kolkata")
                    except Exception:
                        c_ts = datetime.fromtimestamp(c_item["timestamp"])
                    b_eval = {
                        "timestamp": c_ts,
                        "open": c_item["open"],
                        "high": c_item["high"],
                        "low": c_item["low"],
                        "close": c_item["close"],
                        "volume": c_item["volume"],
                        "idx": idx_c
                    }
                    sig = isolated_strat.on_candle_close(b_eval, {})
                    if sig:
                        setup_name = sig.setup_details.get("setup_type", sig.side)
                        markers.append({
                            "time": c_item["timestamp"],
                            "position": "belowBar" if sig.side == "CE" else "aboveBar",
                            "color": "#00f2a9" if sig.side == "CE" else "#ff3860",
                            "shape": "arrowUp" if sig.side == "CE" else "arrowDown",
                            "text": f"🎯 AMD {setup_name[:8]}",
                            "type": f"STRAT_{sig.side}"
                        })
            except Exception as e:
                print(f"Error evaluating isolated strategy markers: {e}")

        return {"date": resolved_date, "today": today_str, "candles": candles, "markers": markers}

    except Exception as e:
        print(f"Error in get_candles: {e}", flush=True)
        return {"date": target_date or today_str, "today": today_str, "candles": [], "markers": []}


@app.get("/api/option_chain")
async def get_option_chain(date: Optional[str] = Query(None)):
    """Returns option chain matrix with strike grid around ATM for the specified date or current spot."""
    spot = state.current_spot or 22800.0
    if date:
        target_date = date.strip()
        try:
            q = text("""
                SELECT close FROM minute_candles 
                WHERE symbol = 'NIFTY-I'
                  AND (timestamp AT TIME ZONE 'Asia/Kolkata')::date = :target_date
                ORDER BY timestamp DESC 
                LIMIT 1
            """)
            with state.db_engine.connect() as conn:
                row = conn.execute(q, {"target_date": target_date}).fetchone()
                if row and row[0]:
                    spot = float(row[0])
                else:
                    # If requested date has no candles yet (e.g. today pre-market), get previous close
                    q_prev = text("""
                        SELECT close FROM minute_candles 
                        WHERE symbol = 'NIFTY-I'
                        ORDER BY timestamp DESC 
                        LIMIT 1
                    """)
                    row_prev = conn.execute(q_prev).fetchone()
                    if row_prev and row_prev[0]:
                        spot = float(row_prev[0])
        except Exception as e:
            print(f"Error querying spot for option chain: {e}")

    return build_option_chain_data(spot)


@app.get("/api/paper/trades")
async def get_paper_trades(mode: Optional[str] = Query(None), date: Optional[str] = Query(None)):
    """Returns executed paper trades history with Iceberg legs and itemized fees, filtered by date if provided."""
    if not state.live_paper_adapter.trade_history:
        state.load_paper_state()
    target_adapter = state.sim_paper_adapter if (mode == "simulation" or (mode is None and state.mode == "simulation")) else state.live_paper_adapter

    all_trades = list(reversed(target_adapter.trade_history))
    if date:
        date_str = date.strip()
        trades = [
            t for t in all_trades 
            if str(t.get("entry_time", "")).startswith(date_str) or str(t.get("exit_time", "")).startswith(date_str)
        ]
    else:
        trades = all_trades

    date_pnl = sum(float(t.get("net_pnl_inr", 0.0)) for t in trades)
    wins = sum(1 for t in trades if t.get("is_win", False))
    total_trades = len(trades)
    win_rate = round((wins / total_trades * 100), 1) if total_trades > 0 else 0.0

    return {
        "trades": trades,
        "capital": round(target_adapter.capital, 2),
        "initial_capital": round(state.cfg.initial_capital, 2),
        "active_position": target_adapter.active_position,
        "mode": "simulation" if target_adapter == state.sim_paper_adapter else "paper",
        "date_pnl": round(date_pnl, 2),
        "date_win_rate": win_rate,
        "date_trades_count": total_trades
    }


@app.post("/api/paper/order")
async def place_paper_order(payload: Dict[str, Any] = Body(...)):
    """Triggers an instantaneous Paper Order with Iceberg slicing."""
    action = payload.get("action", "ENTER")
    side = payload.get("side", state.prediction["direction"]).upper()

    if action == "ENTER":
        if state.mode != "simulation" and not is_market_open_now():
            raise HTTPException(
                status_code=400, 
                detail="Market is closed. Orders can only be placed during active market hours (09:15 - 15:30 IST) or in Simulation mode."
            )

        if not state.prediction.get("active") or not state.prediction.get("suggested_strike"):
            raise HTTPException(
                status_code=400,
                detail="No active trade signal at this time. Suggestion is not available."
            )

        if state.paper_adapter.active_position is not None:
            raise HTTPException(status_code=400, detail="A position is already active. Close it first.")

        pos = state.paper_adapter.open_position(
            symbol=state.cfg.symbol,
            side=side,
            price=state.current_spot,
            atr=state.current_atr,
            timestamp=datetime.now(),
            is_sar=False
        )
        if state.mode != "simulation":
            state.save_paper_state()
        await broadcast_state()
        return {"status": "success", "action": "ORDER_PLACED", "position": pos}

    elif action == "CLOSE":
        if state.paper_adapter.active_position is None:
            raise HTTPException(status_code=400, detail="No active position to close.")

        rec = state.paper_adapter.close_position(
            reason="MANUAL_SQUAREOFF",
            exit_price=state.current_spot,
            timestamp=datetime.now()
        )
        if state.mode != "simulation":
            state.save_paper_state()
        await broadcast_state()
        return {"status": "success", "action": "ORDER_CLOSED", "record": rec}


@app.post("/api/strategy/execute")
async def execute_strategy_order(payload: Dict[str, Any] = Body(...)):
    """Executes a detected institutional strategy setup (e.g. AMD Liquidity Sweep) on PaperAdapter."""
    idx = int(payload.get("index", 0))
    suggestions = state.strategy_detector.active_suggestions
    if not suggestions or idx < 0 or idx >= len(suggestions):
        raise HTTPException(status_code=400, detail="Strategy suggestion is no longer active or invalid index.")

    sig = suggestions[idx]
    # Close any active position to give institutional strategy trade priority
    if state.paper_adapter.active_position is not None:
        state.paper_adapter.close_position("STRATEGY_PRIORITY", state.current_spot, datetime.now())

    pos = state.paper_adapter.open_position(
        symbol=state.cfg.symbol,
        side=sig.side,
        price=state.current_spot,
        atr=state.current_atr,
        timestamp=datetime.now(),
        is_sar=False,
        custom_target=sig.target,
        custom_sl=sig.stop_loss,
        strategy_name=sig.strategy_name
    )
    if state.mode != "simulation":
        state.save_paper_state()
    await broadcast_state()
    return {"status": "success", "action": "STRATEGY_ORDER_PLACED", "position": pos}


# ── Contract Screen Trading Terminal Endpoints ──────────────────────────────

@app.get("/api/trade/state")
async def get_contract_trade_state(symbol: Optional[str] = None):
    """Returns current active position, day PnL, capital balance, and kill switch status."""
    if contract_engine.active_position:
        sym = symbol or contract_engine.active_position["symbol"]
        cache = get_all_live_prices()
        entry = cache.get(sym) or cache.get(sym.upper())
        if entry and entry.get("price"):
            contract_engine.update_price_tick(sym, float(entry["price"]))
    return contract_engine.get_state()


@app.post("/api/trade/order")
async def place_contract_order(payload: Dict[str, Any] = Body(...)):
    """
    Places 1-click Market or Limit order on contract.
    Payload: { symbol, side, order_type, lots, limit_price, sl, target1, target2, trailing_mode, current_market_price }
    """
    symbol = payload.get("symbol", "")
    if not symbol:
        raise HTTPException(status_code=400, detail="Contract symbol is required.")
    
    side = payload.get("side", "BUY")
    order_type = payload.get("order_type", "MARKET")
    lots = int(payload.get("lots", 1))
    limit_price = payload.get("limit_price")
    sl = payload.get("sl")
    target1 = payload.get("target1")
    target2 = payload.get("target2")
    trailing_mode = payload.get("trailing_mode", "be_lock")
    current_market_price = payload.get("current_market_price")

    res = contract_engine.place_order(
        symbol=symbol,
        side=side,
        order_type=order_type,
        lots=lots,
        limit_price=limit_price,
        sl=sl,
        target1=target1,
        target2=target2,
        trailing_mode=trailing_mode,
        current_market_price=current_market_price
    )
    if res.get("status") == "error":
        raise HTTPException(status_code=400, detail=res.get("message", "Order failed."))
    return res


@app.post("/api/trade/exit")
async def exit_contract_position(payload: Dict[str, Any] = Body(default={})):
    """1-click Market Exit for the active position."""
    reason = payload.get("reason", "MANUAL_EXIT")
    exit_price = payload.get("exit_price")
    res = contract_engine.close_position(reason=reason, exit_price=exit_price)
    if res.get("status") == "error":
        raise HTTPException(status_code=400, detail=res.get("message", "Exit failed."))
    return res


@app.post("/api/trade/cancel")
async def cancel_contract_pending_orders(payload: Dict[str, Any] = Body(default={})):
    """Cancels pending limit orders."""
    order_id = payload.get("order_id")
    res = contract_engine.cancel_pending_order(order_id=order_id)
    return res


@app.post("/api/trade/modify")
async def modify_contract_levels(payload: Dict[str, Any] = Body(...)):
    """Modifies SL, Target, Limit price, or Trailing mode on the fly (e.g. from chart drag)."""
    res = contract_engine.modify_active_levels(
        sl=payload.get("sl"),
        target1=payload.get("target1"),
        target2=payload.get("target2"),
        limit_price=payload.get("limit_price"),
        trailing_mode=payload.get("trailing_mode")
    )
    if res.get("status") == "error":
        raise HTTPException(status_code=400, detail=res.get("message", "Modification failed."))
    return res


@app.post("/api/trade/kill_switch")
async def trigger_contract_kill_switch():
    """Emergency Kill Switch: Flattens all positions, cancels orders, locks trading."""
    return contract_engine.trigger_kill_switch()


@app.post("/api/trade/kill_switch/unlock")
async def unlock_contract_kill_switch():
    """Unlocks the Kill Switch so normal trading can resume."""
    return contract_engine.unlock_kill_switch()


@app.post("/api/trade/mode")
async def set_contract_trade_mode(payload: Dict[str, Any] = Body(...)):
    """Switches trading mode ('paper', 'live', 'replay')."""
    mode = payload.get("mode", "paper")
    res = contract_engine.set_mode(mode)
    if res.get("status") == "error":
        raise HTTPException(status_code=400, detail=res.get("message"))
    return res


@app.post("/api/trade/reset")
async def reset_contract_account(payload: Dict[str, Any] = Body(default={})):
    """Resets paper account balance to initial capital and clears logs."""
    capital = payload.get("capital", 100000.0)
    return contract_engine.reset_account(capital)


@app.post("/api/trade/tick")
async def update_contract_tick(payload: Dict[str, Any] = Body(...)):
    """Tick update hook to evaluate live fills, unrealized PnL, and trailing exits."""
    symbol = payload.get("symbol", "")
    price = float(payload.get("price", 0.0))
    atr = float(payload.get("atr", 2.5))
    if symbol and price > 0:
        fill_or_exit = contract_engine.update_price_tick(symbol, price, atr)
        return {"status": "success", "event": fill_or_exit, "state": contract_engine.get_state()}
    return {"status": "ignored"}


# ── Simulation Replay Endpoints ──────────────────────────────────────────────

AVAILABLE_SIM_DATES = [
    "2026-10-13", "2026-10-12", "2026-10-09", "2026-10-08", "2026-10-07", "2026-10-06", "2026-10-05",
    "2026-10-01", "2026-09-30", "2026-09-29", "2026-09-28", "2026-09-25"
]

def load_sim_candles_from_db(sim_date: str) -> pd.DataFrame:
    """Indexed fast query for 1-minute candles on a given date (runs in ~0.3s)."""
    start_ts = f"{sim_date} 00:00:00+05:30"
    end_ts = f"{sim_date} 23:59:59+05:30"
    q = text("""
        SELECT timestamp, open, high, low, close, volume 
        FROM minute_candles 
        WHERE symbol = 'NIFTY-I' 
          AND timestamp >= :start_ts 
          AND timestamp <= :end_ts
        ORDER BY timestamp ASC
    """)
    with state.db_engine.connect() as conn:
        df = pd.read_sql(q, conn, params={"start_ts": start_ts, "end_ts": end_ts})
    if not df.empty:
        df["timestamp"] = pd.to_datetime(df["timestamp"]).dt.tz_convert("Asia/Kolkata")
    return df


_cached_sim_dates: List[str] = []
_last_dates_fetch_ts: float = 0.0

@app.get("/api/simulation/dates")
async def get_simulation_dates():
    """Returns available trading dates dynamically from DB with fallback, capped at 30 to prevent UI hangs."""
    global _cached_sim_dates, _last_dates_fetch_ts
    now = time.time()

    try:
        from zoneinfo import ZoneInfo
        tz_kolkata = ZoneInfo("Asia/Kolkata")
    except Exception:
        from datetime import timezone, timedelta
        tz_kolkata = timezone(timedelta(hours=5, minutes=30))
    now_ist = datetime.now(tz_kolkata)
    today_str = now_ist.strftime("%Y-%m-%d")
    is_weekday = now_ist.weekday() < 5

    # Cache for 60 seconds so rapid UI renders never block or hang the DB
    if _cached_sim_dates and (now - _last_dates_fetch_ts < 60):
        dates_copy = list(_cached_sim_dates)
        if is_weekday and today_str not in dates_copy:
            dates_copy.insert(0, today_str)
        return {"dates": dates_copy[:30], "today": today_str}

    try:
        q = text("""
            SELECT DISTINCT (timestamp AT TIME ZONE 'Asia/Kolkata')::date AS trade_date
            FROM minute_candles
            WHERE symbol = 'NIFTY-I'
            ORDER BY trade_date DESC
            LIMIT 30
        """)
        with state.db_engine.connect() as conn:
            rows = conn.execute(q).fetchall()
        dates = [str(r[0]) for r in rows if r[0]]
        if is_weekday and today_str not in dates:
            dates.insert(0, today_str)

        if dates:
            _cached_sim_dates = dates[:30]
            _last_dates_fetch_ts = now
            return {"dates": _cached_sim_dates, "today": today_str}
    except Exception as e:
        print(f"Error fetching sim dates from DB: {e}", flush=True)

    fallback = list(AVAILABLE_SIM_DATES)
    if is_weekday and today_str not in fallback:
        fallback.insert(0, today_str)
    return {"dates": fallback[:30], "today": today_str}


@app.get("/api/tunnel")
async def get_tunnel_status():
    """Returns active Cloudflare tunnel URL and Telegram notification status."""
    state_file = STATIC_DIR.parent / "tunnel_state.json"
    if state_file.exists():
        try:
            with open(state_file, "r") as f:
                return json.load(f)
        except Exception:
            pass
    return {"status": "inactive", "url": None}


@app.post("/api/simulation/start")
async def start_simulation(payload: Dict[str, Any] = Body(...)):
    """Starts historical session replay at user-defined speed with auto-execution."""
    raw_date = payload.get("date")
    if isinstance(raw_date, str) and len(raw_date.strip()) == 10:
        sim_date = raw_date.strip()
    else:
        sim_date = "2026-10-09"

    try:
        speed = int(payload.get("speed", 10))
    except (ValueError, TypeError):
        speed = 10

    if state.simulation_task and not state.simulation_task.done():
        state.simulation_active = False
        state.simulation_task.cancel()
        try:
            await asyncio.wait_for(asyncio.shield(state.simulation_task), timeout=0.2)
        except Exception:
            pass

    state.simulation_run_id += 1
    current_run_id = state.simulation_run_id
    state.mode = "simulation"
    state.simulation_date = sim_date
    state.simulation_speed = max(1, min(100, speed))
    state.simulation_active = True
    state.simulation_paused = False
    state.simulation_candles = []
    state.simulation_markers = []

    # Reset Paper Portfolio & Strategy Detector for fresh session replay
    state.paper_adapter = PaperAdapter(state.cfg)
    state.strategy_detector.reset_session()

    # Load session data asynchronously in thread pool to prevent blocking asyncio loop
    df = await asyncio.to_thread(load_sim_candles_from_db, sim_date)
    records = df.to_dict(orient="records") if not df.empty else []

    total_bars = len(records)
    state.simulation_progress = {
        "current_bar": 0,
        "total_bars": total_bars,
        "current_time": "09:15 IST",
        "active": True,
        "paused": False,
        "speed": state.simulation_speed,
        "date": sim_date
    }

    # Pre-populate opening 15 bars so chart can render instantly
    initial_candles = []
    init_count = min(15, total_bars)
    for i in range(init_count):
        r = records[i]
        ts = r["timestamp"]
        c_bar = {
            "time": int(ts.timestamp()),
            "timestamp": int(ts.timestamp()),
            "time_str": ts.strftime("%Y-%m-%d %H:%M"),
            "open": round(float(r["open"]), 2),
            "high": round(float(r["high"]), 2),
            "low": round(float(r["low"]), 2),
            "close": round(float(r["close"]), 2),
            "volume": int(r["volume"])
        }
        initial_candles.append(c_bar)
    state.simulation_candles = initial_candles.copy()

    # Launch background async replay loop with loaded records
    state.simulation_task = asyncio.create_task(run_simulation_replay_loop(records, sim_date, state.simulation_speed, current_run_id))
    return {
        "status": "started", 
        "date": sim_date, 
        "speed": state.simulation_speed,
        "total_bars": total_bars,
        "initial_candles": initial_candles
    }


@app.post("/api/simulation/pause")
async def pause_simulation():
    """Toggles pause/resume state of the running simulation."""
    if not state.simulation_active:
        raise HTTPException(status_code=400, detail="No active simulation running.")
    state.simulation_paused = not state.simulation_paused
    state.simulation_progress["paused"] = state.simulation_paused
    await broadcast_state()
    return {"status": "success", "paused": state.simulation_paused}


@app.post("/api/simulation/stop")
async def stop_simulation():
    """Stops the active simulation and restores predictor mode."""
    state.simulation_active = False
    state.simulation_paused = False
    if state.simulation_task and not state.simulation_task.done():
        state.simulation_task.cancel()
    state.simulation_progress["active"] = False
    state.mode = "predictor"
    await broadcast_state()
    return {"status": "stopped"}


# ── Asynchronous Simulation Replay Loop ──────────────────────────────────────

async def run_simulation_replay_loop(records: List[Dict[str, Any]], sim_date: str, speed: int, run_id: int):
    """Replays historical 1-minute market data bar-by-bar at defined speed."""
    print(f"Simulation Replay [Run #{run_id}]: Initializing replay for {sim_date} ({len(records)} bars) at {speed}x speed...", flush=True)
    state.simulation_progress["active"] = True
    state.simulation_progress["paused"] = False

    if not records:
        print(f"Simulation Replay: No data records for {sim_date}", flush=True)
        if state.simulation_run_id == run_id:
            state.simulation_active = False
            state.simulation_progress["active"] = False
        return

    try:
        # Dynamic delay per candle based on user-defined speed:
        # 1x -> 1.0s | 5x -> 0.35s | 10x -> 0.18s | 25x -> 0.08s | 50x -> 0.04s | 100x -> 0.02s
        speed_delays = {1: 1.0, 5: 0.35, 10: 0.18, 25: 0.08, 50: 0.04, 100: 0.02}
        nominal_delay = speed_delays.get(speed, max(0.02, 1.8 / max(1, speed)))
        history_buffer = []

        for i, bar in enumerate(records):
            while state.simulation_paused and state.simulation_active and state.simulation_run_id == run_id:
                await asyncio.sleep(0.3)

            if not state.simulation_active or state.simulation_run_id != run_id:
                break

            # Faster playback for opening 15 bars so chart initializes instantly
            bar_delay = 0.015 if i < 15 else nominal_delay

            ts = bar["timestamp"]
            c = float(bar["close"])
            h = float(bar["high"])
            l = float(bar["low"])
            o = float(bar["open"])
            vol = int(bar["volume"])

            state.current_spot = c
            state.simulation_progress["current_bar"] = i + 1
            state.simulation_progress["current_time"] = ts.strftime("%H:%M IST")

            # Rolling ATR & RSI
            history_buffer.append(bar)
            if len(history_buffer) > 200:
                history_buffer.pop(0)

            h_df = pd.DataFrame(history_buffer)
            tr = pd.concat([h_df["high"] - h_df["low"], (h_df["high"] - h_df["close"].shift(1)).abs(), (h_df["low"] - h_df["close"].shift(1)).abs()], axis=1).max(axis=1)
            atr_val = float(tr.rolling(14).mean().iloc[-1]) if len(h_df) >= 14 else 22.0

            delta = h_df["close"].diff()
            gain = delta.clip(lower=0).rolling(14).mean()
            loss = (-delta.clip(upper=0)).rolling(14).mean()
            rsi_val = float((100 - (100 / (1 + (gain / (loss + 1e-9))))).iloc[-1]) if len(h_df) >= 14 else 50.0

            state.current_atr = atr_val
            state.current_rsi = rsi_val

            candle_entry = {
                "time": int(ts.timestamp()),
                "timestamp": int(ts.timestamp()),
                "time_str": ts.strftime("%Y-%m-%d %H:%M"),
                "open": round(o, 2),
                "high": round(h, 2),
                "low": round(l, 2),
                "close": round(c, 2),
                "volume": vol
            }
            if i >= len(state.simulation_candles):
                state.simulation_candles.append(candle_entry)

            # ── 1. Check Active Paper Position Exit (Target / SL) ───────────
            exit_marker = None
            if state.paper_adapter.active_position:
                pos = state.paper_adapter.active_position
                if pos["side"] == "CE":
                    if h >= pos["target_price"] and l > pos["current_sl"]:
                        rec = state.paper_adapter.close_position("TARGET_HIT", pos["target_price"], ts)
                        exit_marker = {
                            "time": int(ts.timestamp()),
                            "position": "aboveBar",
                            "color": "#00d2ff",
                            "shape": "flag",
                            "text": f"★ TGT +{pos['target_price'] - pos['entry_price']:.1f}",
                            "type": "EXIT_TARGET"
                        }
                    elif l <= pos["current_sl"]:
                        rec = state.paper_adapter.close_position("STOP_LOSS", pos["current_sl"], ts)
                        exit_marker = {
                            "time": int(ts.timestamp()),
                            "position": "belowBar",
                            "color": "#ff3860",
                            "shape": "flag",
                            "text": f"✖ SL -{pos['entry_price'] - pos['current_sl']:.1f}",
                            "type": "EXIT_SL"
                        }
                else: # PE
                    if l <= pos["target_price"] and h < pos["current_sl"]:
                        rec = state.paper_adapter.close_position("TARGET_HIT", pos["target_price"], ts)
                        exit_marker = {
                            "time": int(ts.timestamp()),
                            "position": "belowBar",
                            "color": "#00d2ff",
                            "shape": "flag",
                            "text": f"★ TGT +{pos['entry_price'] - pos['target_price']:.1f}",
                            "type": "EXIT_TARGET"
                        }
                    elif h >= pos["current_sl"]:
                        rec = state.paper_adapter.close_position("STOP_LOSS", pos["current_sl"], ts)
                        exit_marker = {
                            "time": int(ts.timestamp()),
                            "position": "aboveBar",
                            "color": "#ff3860",
                            "shape": "flag",
                            "text": f"✖ SL -{pos['current_sl'] - pos['entry_price']:.1f}",
                            "type": "EXIT_SL"
                        }

                if exit_marker:
                    state.simulation_markers.append(exit_marker)

            # ── 2. AI Predictor Evaluation & Automatic Order Placement ───────
            entry_marker = None
            t_time = ts.time()
            # Alpha Window: 10:05 to 13:30 IST
            if (dtime(10, 5) <= t_time <= dtime(13, 30)):
                ret5 = float((c - h_df["close"].iloc[-5]) / h_df["close"].iloc[-5]) if len(h_df) >= 5 else 0.0
                atm_strike = int(round(c / 50.0) * 50)

                if rsi_val > 54.0 and ret5 > 0.0004:
                    update_dynamic_prediction(c, atr_val, rsi_val)
                    if state.paper_adapter.active_position is None:
                        pos = state.paper_adapter.open_position("NIFTY 50", "CE", c, atr_val, ts)
                        if pos:
                            entry_marker = {
                                "time": int(ts.timestamp()),
                                "position": "belowBar",
                                "color": "#00f2a9",
                                "shape": "arrowUp",
                                "text": f"▲ BUY CE @ {c:.0f}",
                                "type": "ENTRY_CE"
                            }
                            state.simulation_markers.append(entry_marker)

                elif rsi_val < 46.0 and ret5 < -0.0004:
                    update_dynamic_prediction(c, atr_val, rsi_val)
                    if state.paper_adapter.active_position is None:
                        pos = state.paper_adapter.open_position("NIFTY 50", "PE", c, atr_val, ts)
                        if pos:
                            entry_marker = {
                                "time": int(ts.timestamp()),
                                "position": "aboveBar",
                                "color": "#ff3860",
                                "shape": "arrowDown",
                                "text": f"▼ BUY PE @ {c:.0f}",
                                "type": "ENTRY_PE"
                            }
                            state.simulation_markers.append(entry_marker)
                else:
                    update_dynamic_prediction(c, atr_val, rsi_val)
            else:
                update_dynamic_prediction(c, atr_val, rsi_val)

            # ── 3. Modular Strategy Detection Engine (AMD Volume Profile Sweeps) ──
            strat_marker = None
            bar_eval = {
                "timestamp": ts,
                "open": o,
                "high": h,
                "low": l,
                "close": c,
                "volume": vol,
                "idx": i
            }
            detected_signals = state.strategy_detector.evaluate_candle(bar_eval, {
                "spot": c,
                "atr": atr_val,
                "rsi": rsi_val
            })
            if detected_signals:
                for sig in detected_signals:
                    setup_name = sig.setup_details.get("setup_type", sig.side)
                    # Close existing generic position to give institutional strategy trade priority
                    if state.paper_adapter.active_position is not None:
                        curr_pos = state.paper_adapter.active_position
                        if curr_pos.get("strategy") != sig.strategy_name:
                            state.paper_adapter.close_position("STRATEGY_PRIORITY", c, ts)

                    # Execute strategy order on PaperAdapter
                    pos = state.paper_adapter.open_position(
                        symbol="NIFTY 50",
                        side=sig.side,
                        price=c,
                        atr=atr_val,
                        timestamp=ts,
                        custom_target=sig.target,
                        custom_sl=sig.stop_loss,
                        strategy_name=sig.strategy_name
                    )

                    strat_marker = {
                        "time": int(ts.timestamp()),
                        "position": "belowBar" if sig.side == "CE" else "aboveBar",
                        "color": "#00f2a9" if sig.side == "CE" else "#ff3860",
                        "shape": "arrowUp" if sig.side == "CE" else "arrowDown",
                        "text": f"🎯 AMD {setup_name[:8]}",
                        "type": f"STRAT_{sig.side}"
                    }
                    state.simulation_markers.append(strat_marker)

            # ── 4. Broadcast Real-Time Replay Packet ────────────────────────
            dyn_chain = build_option_chain_data(c)
            dyn_sugg = get_dynamic_strategy_suggestions()
            packet = {
                "type": "SIMULATION_TICK",
                "spot": round(c, 2),
                "candle": candle_entry,
                "marker": strat_marker or entry_marker or exit_marker,
                "prediction": state.prediction,
                "option_chain": dyn_chain,
                "active_position": state.paper_adapter.active_position,
                "capital": round(state.paper_adapter.capital, 2),
                "progress": state.simulation_progress,
                "strategy_suggestions": dyn_sugg,
                "strategy_count": len(dyn_sugg)
            }
            await broadcast_packet(packet)
            await asyncio.sleep(bar_delay)

        if state.simulation_run_id == run_id:
            state.simulation_active = False
            state.simulation_progress["active"] = False
            print(f"Simulation Replay [Run #{run_id}]: Session completed for {sim_date}.", flush=True)

    except Exception as e:
        if state.simulation_run_id == run_id:
            print(f"Simulation Replay Error [Run #{run_id}]: {e}", flush=True)
            state.simulation_active = False
            state.simulation_progress["active"] = False


# ── WebSocket Real-Time Stream ───────────────────────────────────────────────

@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await ws.accept()
    state.active_websockets.append(ws)
    try:
        await ws.send_json({"type": "INIT", "mode": state.mode, "spot": state.current_spot})
        while True:
            msg = await ws.receive_text()
            data = json.loads(msg)
            if data.get("type") == "PING":
                await ws.send_json({"type": "PONG"})
    except WebSocketDisconnect:
        if ws in state.active_websockets:
            state.active_websockets.remove(ws)
    except Exception:
        if ws in state.active_websockets:
            state.active_websockets.remove(ws)


async def broadcast_state():
    """Broadcasts current state packet to all connected clients."""
    pos = state.paper_adapter.active_position
    unrealized_pnl = 0.0
    if pos:
        side = pos["side"]
        current_px = state.current_spot
        entry_px = pos["entry_price"]
        pts = (current_px - entry_px) if side == "CE" else (entry_px - current_px)
        unrealized_pnl = round(pts * 0.65 * pos["units"], 2)

    packet = {
        "type": "TICK_UPDATE",
        "spot": round(state.current_spot, 2),
        "prediction": state.prediction,
        "active_position": pos,
        "unrealized_pnl": unrealized_pnl,
        "capital": round(state.paper_adapter.capital, 2),
        "timestamp": datetime.now().isoformat()
    }
    await broadcast_packet(packet)


async def broadcast_packet(packet: Dict[str, Any]):
    """Low-level broadcast to WebSocket client pool with timeout protection."""
    if not state.active_websockets:
        return
    dead = []
    for ws in list(state.active_websockets):
        try:
            await asyncio.wait_for(ws.send_json(packet), timeout=0.25)
        except Exception:
            dead.append(ws)
    for ws in dead:
        if ws in state.active_websockets:
            state.active_websockets.remove(ws)


# ── Live Tick Monitor & Sub-Second Execution Broadcaster ──────────────────────

async def run_live_tick_broadcaster():
    """Continuously monitors live tick stream (from /tmp/td_live_prices.json or tick_data DB).
    1. Ingests sub-second ticks with zero lag.
    2. Dynamically aggregates and updates current forming 1-minute candle.
    3. Executes Stop Loss and Target instantly via paper_adapter.update_tick().
    4. Broadcasts real-time TICK_UPDATE packets to connected browser WebSockets.
    """
    print("LiveTickBroadcaster: Started real-time sub-second tick monitoring...", flush=True)
    last_prediction_refresh = 0.0

    while True:
        try:
            if state.mode != "simulation":
                tick_price = None
                tick_time = None

                market_open = is_market_open_now()
                is_fresh_tick = False

                # 1. Check instantaneous sub-second price cache from /tmp/td_live_prices.json
                if os.path.exists("/tmp/td_live_prices.json"):
                    try:
                        mtime = os.path.getmtime("/tmp/td_live_prices.json")
                        if time.time() - mtime < 120:
                            with open("/tmp/td_live_prices.json", "r") as f:
                                cache = json.load(f)
                            nifty_entry = cache.get("NIFTY") or cache.get("NIFTY-I") or cache.get("NIFTY 50")
                            if nifty_entry and nifty_entry.get("price"):
                                tick_price = float(nifty_entry["price"])
                                tick_time = nifty_entry.get("ts")
                                is_fresh_tick = True
                            # ⚡ Push entire cache to in-memory bus — zero disk I/O for contract page
                            for sym, entry in cache.items():
                                if entry and entry.get("price"):
                                    push_to_price_bus(sym, float(entry["price"]), entry.get("ts"))
                    except Exception:
                        pass

                # 2. Fallback to latest tick in TimescaleDB tick_data table
                if tick_price is None:
                    try:
                        with state.db_engine.connect() as conn:
                            row = conn.execute(text("""
                                SELECT timestamp, price 
                                FROM tick_data 
                                WHERE symbol IN ('NIFTY-I', 'NIFTY 50')
                                ORDER BY timestamp DESC 
                                LIMIT 1
                            """)).fetchone()
                            if row and row[1]:
                                tick_time = str(row[0])
                                tick_price = float(row[1])
                                db_ts = row[0]
                                if hasattr(db_ts, "timestamp") and abs(time.time() - db_ts.timestamp()) < 120:
                                    is_fresh_tick = True
                    except Exception:
                        pass

                if tick_price is not None and tick_price > 0:
                    state.current_spot = tick_price
                    now = datetime.now()
                    now_min = now.replace(second=0, microsecond=0)
                    now_min_ts = int(now_min.timestamp())

                    # Maintain forming candle ONLY during active market hours with fresh ticks
                    if market_open and is_fresh_tick:
                        if state.forming_candle is None or state.forming_candle["time"] != now_min_ts:
                            if state.forming_candle is not None:
                                closed_bar = dict(state.forming_candle)
                                closed_bar["timestamp"] = datetime.fromtimestamp(closed_bar["time"])
                                closed_bar["atr"] = state.current_atr or 20.0
                                try:
                                    state.strategy_detector.evaluate_candle_parallel(closed_bar)
                                except Exception:
                                    pass
                                try:
                                    compute_live_market_prediction()
                                    last_prediction_refresh = time.time()
                                except Exception:
                                    pass

                            state.forming_candle = {
                                "time": now_min_ts,
                                "open": tick_price,
                                "high": tick_price,
                                "low": tick_price,
                                "close": tick_price,
                                "volume": 0
                            }
                        else:
                            state.forming_candle["high"] = max(state.forming_candle["high"], tick_price)
                            state.forming_candle["low"] = min(state.forming_candle["low"], tick_price)
                            state.forming_candle["close"] = tick_price

                        # Periodic refresh of technical prediction every 15 seconds during live market
                        if time.time() - last_prediction_refresh > 15.0:
                            try:
                                compute_live_market_prediction()
                                last_prediction_refresh = time.time()
                            except Exception:
                                pass
                    else:
                        state.forming_candle = None

                    # Sub-second position monitoring: check SL / Target immediately!
                    exit_marker = None
                    if state.paper_adapter.active_position is not None:
                        exit_rec = state.paper_adapter.update_tick({
                            "last_price": tick_price,
                            "timestamp": now
                        })
                        if exit_rec is not None:
                            if state.mode != "simulation":
                                state.save_paper_state()
                            is_win = exit_rec.get("is_win", False)
                            exit_marker = {
                                "time": now_min_ts,
                                "position": "aboveBar" if is_win else "belowBar",
                                "color": "#00d2ff" if is_win else "#ff3860",
                                "shape": "flag",
                                "text": f"{'★ TGT' if is_win else '✖ SL'} {exit_rec.get('net_pnl_inr', 0):+,.0f}",
                                "type": "EXIT_TARGET" if is_win else "EXIT_SL"
                            }

                    # Broadcast instant tick packet to active browser WebSockets
                    pos = state.paper_adapter.active_position
                    unrealized_pnl = 0.0
                    if pos:
                        side = pos["side"]
                        pts = (tick_price - pos["entry_price"]) if side == "CE" else (pos["entry_price"] - tick_price)
                        unrealized_pnl = round(pts * 0.65 * pos["units"], 2)

                    if state.mode == "simulation" or is_market_open_now():
                        dyn_pred = update_dynamic_prediction(tick_price, state.current_atr or 20.0, state.current_rsi or 50.0, state.current_vwap or tick_price)
                        dyn_sugg = get_dynamic_strategy_suggestions()
                    else:
                        dyn_pred = {
                            "active": False,
                            "status": "MARKET_CLOSED",
                            "message": "Market is closed. Real-time AI suggestions resume at 09:15 AM IST.",
                            "suggested_strike": None,
                            "direction": None,
                            "bias": "MARKET CLOSED",
                            "confidence_pct": 0,
                            "model_1_score": 0.0,
                            "model_2_status": "IDLE",
                            "entry_range": "--",
                            "entry_premium": 0,
                            "target_premium": 0,
                            "stop_loss_premium": 0,
                            "expected_gain_pct": 0,
                            "max_risk_pct": 0,
                            "timestamp": now.isoformat(),
                            "spot": round(tick_price, 2)
                        }
                        dyn_sugg = []
                        state.prediction = dyn_pred

                    packet = {
                        "type": "TICK_UPDATE",
                        "spot": round(tick_price, 2),
                        "forming_candle": state.forming_candle,
                        "marker": exit_marker,
                        "active_position": pos,
                        "unrealized_pnl": unrealized_pnl,
                        "capital": round(state.paper_adapter.capital, 2),
                        "prediction": dyn_pred,
                        "option_chain": dyn_chain,
                        "strategy_suggestions": dyn_sugg,
                        "strategy_count": len(dyn_sugg),
                        "timestamp": now.isoformat()
                    }
                    await broadcast_packet(packet)

                    # ⚡ Immediately push to contract WS clients (zero waiting for contract loop)
                    if market_open and is_fresh_tick:
                        try:
                            c_symbols = get_all_subscribed_symbols()
                            if c_symbols:
                                _now_iso = now.isoformat()
                                for c_sym in c_symbols:
                                    c_entry = cache.get(c_sym) or cache.get(c_sym.upper())
                                    if not c_entry or not c_entry.get("price"):
                                        continue
                                    c_ltp = float(c_entry["price"])
                                    c_fc = update_forming_candle(c_sym, c_ltp)
                                    c_clients = get_contract_clients(c_sym)
                                    if not c_clients:
                                        continue
                                    c_packet = {
                                        "type": "CONTRACT_TICK",
                                        "symbol": c_sym,
                                        "ltp": round(c_ltp, 2),
                                        "forming_candle": c_fc,
                                        "timestamp": _now_iso,
                                    }
                                    _dead = []
                                    for _ws in c_clients:
                                        try:
                                            await asyncio.wait_for(_ws.send_json(c_packet), timeout=0.1)
                                        except Exception:
                                            _dead.append(_ws)
                                    for _ws in _dead:
                                        unregister_contract_client(c_sym, _ws)
                        except Exception:
                            pass

        except Exception:
            pass

        await asyncio.sleep(0.5)


async def run_collector_supervisor():
    """Continuously monitors and supervises the 24/7 Market & Option Chain collector daemon.
    Automatically starts trading_engine.collectors.market_collector if not running.
    """
    print("CollectorSupervisor: Started background market & option chain collector monitor...", flush=True)
    project_root = Path(__file__).resolve().parent.parent.parent

    while True:
        try:
            import subprocess
            # Check if market_collector daemon is running
            res = subprocess.run(["pgrep", "-f", "trading_engine.collectors.market_collector"], capture_output=True, text=True)
            collector_running = bool(res.stdout.strip())

            if not collector_running:
                # Check if managed by systemd or needs manual launch
                import shutil
                systemd_active = False
                if shutil.which("systemctl"):
                    try:
                        res_systemd = subprocess.run(["systemctl", "is-active", "quant-collector"], capture_output=True, text=True)
                        systemd_active = (res_systemd.stdout.strip() == "active")
                    except Exception:
                        systemd_active = False

                if not systemd_active:
                    print("CollectorSupervisor: market_collector daemon is not running. Launching background worker...", flush=True)
                    subprocess.Popen(
                        [sys.executable, "-m", "trading_engine.collectors.market_collector"],
                        cwd=str(project_root),
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        start_new_session=True
                    )
                    print("CollectorSupervisor: market_collector successfully launched!", flush=True)

        except Exception as err:
            print(f"CollectorSupervisor error: {err}", flush=True)

        await asyncio.sleep(60)


async def run_tunnel_supervisor():
    """
    Spawns Cloudflare Tunnel in background and automatically dispatches public URL to Telegram.
    Runs self-healing loop: if tunnel changes or restarts, sends new URL to Telegram.
    """
    try:
        import sys
        engine_dir = str(Path(__file__).resolve().parent.parent)
        if engine_dir not in sys.path:
            sys.path.insert(0, engine_dir)
        from trading_engine.tunnel_pipeline import TunnelPipeline
        pipeline = TunnelPipeline(target_port=8080)
        state.tunnel_pipeline = pipeline

        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, pipeline.run_forever)
    except Exception as e:
        print(f"TunnelSupervisor: Error running tunnel pipeline: {e}", flush=True)


@app.on_event("startup")
async def on_startup():
    try:
        compute_live_market_prediction()
    except Exception as e:
        print(f"Startup: Error computing initial prediction: {e}", flush=True)
    state.tick_broadcaster_task = asyncio.create_task(run_live_tick_broadcaster())
    state.collector_supervisor_task = asyncio.create_task(run_collector_supervisor())
    asyncio.create_task(run_contract_tick_broadcaster())
    state.tunnel_task = asyncio.create_task(run_tunnel_supervisor())


@app.on_event("shutdown")
async def on_shutdown():
    if hasattr(state, "tunnel_pipeline") and state.tunnel_pipeline:
        try:
            state.tunnel_pipeline.stop()
        except Exception:
            pass



# ── Static UI Mounting ────────────────────────────────────────────────────────

app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

@app.get("/")
async def serve_index():
    return FileResponse(STATIC_DIR / "index.html")

@app.get("/contract")
@app.get("/contract.html")
async def serve_contract():
    return FileResponse(
        STATIC_DIR / "contract.html",
        headers={
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Pragma": "no-cache",
            "Expires": "0",
        }
    )


# ── Contract Analysis Page APIs ───────────────────────────────────────────────




@app.get("/api/contracts/expiries")
async def contract_expiries(underlying: str = Query("NIFTY")):
    """Return available future option expiry dates for the given underlying."""
    expiries = get_available_expiries(underlying=underlying)
    return {
        "underlying": underlying.upper(),
        "total": len(expiries),
        "expiries": expiries,
    }


@app.get("/api/contracts/option_chain")
async def contract_option_chain(
    underlying: str = Query("NIFTY"),
    expiry: Optional[str] = Query(None)
):
    """Return full option chain matrix around ATM strike with live LTPs."""
    data = get_option_chain_data(underlying=underlying, expiry=expiry, spot_price=state.current_spot)
    return data


@app.get("/api/contracts/search")
async def contract_search(
    q: str = Query("", min_length=0),
    expiry: Optional[str] = Query(None),
    limit: int = Query(15, ge=1, le=50)
):
    """Fuzzy-search contracts from active Kite instruments + symbol_master."""
    if not q or not q.strip():
        return {"query": q, "total_matches": 0, "results": []}
    results = search_contracts(q.strip(), expiry=expiry, limit=limit, db_engine=state.db_engine)
    return {
        "query": q,
        "expiry": expiry,
        "total_matches": len(results),
        "results": results,
    }


@app.get("/api/contracts/candles")
async def contract_candles(
    symbol: str = Query(..., min_length=1),
    date: Optional[str] = Query(None),
):
    """
    Return 1-minute candles for the given contract symbol.
    If market is closed (weekend, holiday, pre-market) or today has 0 candles:
    Automatically returns the last available trading day's data!
    """
    try:
        res = fetch_contract_candles(symbol, target_date=date, db_engine=state.db_engine)
        # Enrich with live order flow / depth quote if available
        quote = get_contract_quote(symbol)
        res["quote"] = quote
        return res
    except Exception as e:
        print(f"contract_candles error for {symbol}: {e}", flush=True)
        return {
            "symbol": symbol,
            "date": get_today_str(),
            "is_last_day_data": False,
            "market_status": "ERROR",
            "num_candles": 0,
            "candles": [],
            "error": str(e),
        }


@app.get("/api/contracts/quote")
async def contract_quote(symbol: str = Query(...)):
    """Fetch live bid/offer depth, order flow ratio, volume, and OI."""
    q = get_contract_quote(symbol)
    return q or {"symbol": symbol, "error": "Quote not available"}


@app.websocket("/ws/contracts")
async def contract_ws_endpoint(websocket: WebSocket, symbol: str = Query(...)):
    """
    Dedicated WebSocket for contract live tick streaming.
    Broadcasts CONTRACT_TICK packets with LTP, forming candle, and session OHLC.
    """
    await websocket.accept()
    register_contract_client(symbol, websocket)
    print(f"ContractWS: Client connected for {symbol}", flush=True)

    try:
        # Send initial forming candle if available
        fc = get_forming_candle(symbol)
        if fc:
            ltp = fc.get("close", 0)
            await websocket.send_json({
                "type": "CONTRACT_TICK",
                "symbol": symbol,
                "ltp": ltp,
                "forming_candle": fc,
                "timestamp": datetime.now().isoformat(),
            })

        while True:
            try:
                raw = await asyncio.wait_for(websocket.receive_text(), timeout=60)
                msg = json.loads(raw)
                if msg.get("type") == "PING":
                    await websocket.send_json({"type": "PONG"})
            except asyncio.TimeoutError:
                # Send keepalive with latest LTP
                ltp = get_live_ltp(symbol)
                fc = get_forming_candle(symbol)
                if ltp and is_market_open():
                    fc_updated = update_forming_candle(symbol, ltp) if ltp else fc
                    await websocket.send_json({
                        "type": "CONTRACT_TICK",
                        "symbol": symbol,
                        "ltp": ltp,
                        "forming_candle": fc_updated,
                        "timestamp": datetime.now().isoformat(),
                    })
            except WebSocketDisconnect:
                break

    except Exception as e:
        print(f"ContractWS error for {symbol}: {e}", flush=True)
    finally:
        unregister_contract_client(symbol, websocket)
        print(f"ContractWS: Client disconnected for {symbol}", flush=True)


@app.post("/api/events/trend_signal")
async def api_post_trend_signal(payload: Dict[str, Any] = Body(...)):
    """
    Zero-latency Event Trigger endpoint for trend signals (from SMF Cloud or technical models).
    1. Persists event to TimescaleDB `trend_events` table.
    2. Dispatches real-time Telegram alert.
    3. Broadcasts WebSocket notification to connected UI clients.
    4. Invokes extensible `on_trend_signal_triggered` hook for automated trading contract selection.
    """
    symbol = payload.get("symbol", "NIFTY")
    direction = payload.get("direction", "BUY").upper()
    price = float(payload.get("price", 0.0) or state.current_spot or 0.0)
    strategy = payload.get("strategy", "SMF_CLOUD")
    timeframe = payload.get("timeframe", "1m")
    market_regime = payload.get("market_regime")
    details = payload.get("details", {})

    event = record_trend_event(
        symbol=symbol,
        direction=direction,
        price=price,
        strategy=strategy,
        timeframe=timeframe,
        market_regime=market_regime,
        details=details,
        db_engine=state.db_engine,
        send_telegram=True,
    )

    try:
        await broadcast_packet({
            "type": "TREND_SIGNAL_NOTIFICATION",
            "event": event
        })
    except Exception:
        pass

    return {"status": "success", "event": event}


@app.get("/api/events/trend_signals")
async def api_get_trend_signals(limit: int = Query(50, ge=1, le=200)):
    """Return recent trend signal events from TimescaleDB."""
    events = get_recent_trend_events(limit=limit, db_engine=state.db_engine)
    return {"total": len(events), "events": events}


@app.post("/api/events/order_execution")
async def api_post_order_execution(payload: Dict[str, Any] = Body(...)):
    """
    Zero-latency live order / target execution endpoint.
    Triggered instantaneously on tick arrival when target or stop condition is met.
    1. Logs execution with microsecond timestamp.
    2. If active paper/live position exists, triggers immediate close/partial fill.
    3. Broadcasts WebSocket execution notification to all connected clients.
    4. Ready for direct broker API routing (Zerodha / Shoonya / Finvasia / Angel).
    """
    symbol = payload.get("symbol", "NIFTY")
    action = payload.get("action", "TARGET_HIT")
    target_level = payload.get("target_level", "TARGET")
    target_price = float(payload.get("target_price", 0.0) or 0.0)
    exec_price = float(payload.get("execution_price", 0.0) or target_price)
    ts = payload.get("timestamp") or datetime.now().isoformat()
    details = payload.get("details", {})

    print(f"⚡ [LIVE TICK EXECUTION] {symbol} {action} ({target_level}) @ ₹{exec_price:.2f} (Target: ₹{target_price:.2f})", flush=True)

    # 1. Close or update active paper adapter position if matched
    exit_rec = None
    if state.paper_adapter.active_position is not None:
        try:
            exit_rec = state.paper_adapter.close_position(
                reason=action,
                exit_price=exec_price,
                timestamp=datetime.now()
            )
            if state.mode != "simulation":
                state.save_paper_state()
        except Exception as e:
            print(f"OrderExecution: Error closing paper position: {e}", flush=True)

    # 2. Record to TimescaleDB as an executed event
    try:
        record_trend_event(
            symbol=symbol,
            direction="EXIT",
            price=exec_price,
            strategy="LIVE_TICK_EXECUTION",
            timeframe="tick",
            market_regime="EXIT",
            details={
                "action": action,
                "target_level": target_level,
                "target_price": target_price,
                "execution_price": exec_price,
                "slippage": round(exec_price - target_price, 2) if target_price > 0 else 0.0,
                **details
            },
            db_engine=state.db_engine,
            send_telegram=True,
        )
    except Exception as e:
        print(f"OrderExecution: DB logging error: {e}", flush=True)

    # 3. Broadcast instant execution packet to WebSockets
    packet = {
        "type": "ORDER_EXECUTION_NOTIFICATION",
        "symbol": symbol,
        "action": action,
        "target_level": target_level,
        "target_price": target_price,
        "execution_price": exec_price,
        "timestamp": ts,
        "exit_record": exit_rec,
    }
    try:
        await broadcast_packet(packet)
    except Exception:
        pass

    return {
        "status": "executed",
        "action": action,
        "symbol": symbol,
        "execution_price": exec_price,
        "timestamp": ts,
        "exit_record": exit_rec
    }


@app.get("/api/vault/kite-status")
async def get_kite_vault_status():
    """
    Returns configuration status of Kite API credentials in KeyVault.
    Values are NEVER returned to the client to ensure zero leakage.
    """
    from trading_engine.core.keyvault import get_secret
    key = get_secret("KITE_API_KEY")
    token = get_secret("KITE_ACCESS_TOKEN")
    is_configured = bool(key and token and len(key.strip()) > 3 and len(token.strip()) > 10)
    return {
        "status": "ok",
        "configured": is_configured,
        "api_key_set": bool(key and len(key.strip()) > 0),
        "access_token_set": bool(token and len(token.strip()) > 0)
    }


@app.get("/api/vault/broker-status")
async def get_broker_vault_status():
    """
    Returns unified configuration status for both Zerodha Kite and ICICI Direct Breeze.
    Credentials values are NEVER returned.
    """
    from trading_engine.core.keyvault import get_secret
    active_broker = (get_secret("ACTIVE_BROKER") or "kite").strip().lower()

    # Kite status
    k_key = get_secret("KITE_API_KEY")
    k_tok = get_secret("KITE_ACCESS_TOKEN")
    kite_configured = bool(k_key and k_tok and len(k_key.strip()) > 3 and len(k_tok.strip()) > 10)

    # Breeze status
    b_key = get_secret("BREEZE_API_KEY")
    b_sec = get_secret("BREEZE_API_SECRET")
    b_tok = get_secret("BREEZE_SESSION_TOKEN")
    b_uid = get_secret("BREEZE_USER_ID")
    b_pwd = get_secret("BREEZE_PASSWORD")
    b_totp = get_secret("BREEZE_TOTP_KEY")

    breeze_configured = bool(b_key and b_sec and b_tok and len(b_key.strip()) > 3 and len(b_tok.strip()) > 3)
    breeze_autologin_ready = bool(b_key and b_sec and b_uid and b_pwd and b_totp and len(b_key.strip()) > 3 and len(b_totp.strip()) > 5)

    return {
        "status": "ok",
        "active_broker": active_broker,
        "kite": {
            "configured": kite_configured,
            "api_key_set": bool(k_key and len(k_key.strip()) > 0),
            "access_token_set": bool(k_tok and len(k_tok.strip()) > 0)
        },
        "breeze": {
            "configured": breeze_configured,
            "autologin_ready": breeze_autologin_ready,
            "api_key_set": bool(b_key and len(b_key.strip()) > 0),
            "api_secret_set": bool(b_sec and len(b_sec.strip()) > 0),
            "session_token_set": bool(b_tok and len(b_tok.strip()) > 0),
            "user_id_set": bool(b_uid and len(b_uid.strip()) > 0),
            "totp_key_set": bool(b_totp and len(b_totp.strip()) > 0)
        }
    }


@app.post("/api/vault/kite-credentials")
async def update_kite_vault_credentials(payload: Dict[str, Any] = Body(...)):
    """
    Directly encrypts and persists Kite API credentials into the hardware/OS KeyVault.
    Hot-reloads the Kite client in memory with zero downtime.
    Credentials are NEVER logged or echoed in responses.
    """
    api_key = str(payload.get("api_key", "")).strip()
    access_token = str(payload.get("access_token", "")).strip()

    if not api_key:
        return JSONResponse(status_code=400, content={"status": "error", "message": "Kite API Key cannot be empty."})
    if not access_token:
        return JSONResponse(status_code=400, content={"status": "error", "message": "Kite Access Token cannot be empty."})

    from trading_engine.core.keyvault import set_secret
    set_secret("KITE_API_KEY", api_key)
    set_secret("KITE_ACCESS_TOKEN", access_token)

    try:
        _cdm.reset_kite()
        _cdm._get_kite()
    except Exception as e:
        print(f"Server: Error reloading Kite client: {e}", flush=True)

    return {
        "status": "success",
        "message": "Kite credentials encrypted and saved to KeyVault. Live client reloaded."
    }


@app.post("/api/vault/breeze-credentials")
async def update_breeze_vault_credentials(payload: Dict[str, Any] = Body(...)):
    """
    Encrypts and persists ICICI Direct Breeze credentials into the KeyVault.
    Hot-reloads the Breeze client in memory with zero downtime.
    """
    api_key = str(payload.get("api_key", "")).strip()
    api_secret = str(payload.get("api_secret", "")).strip()
    session_token = str(payload.get("session_token", "")).strip()

    if not api_key:
        return JSONResponse(status_code=400, content={"status": "error", "message": "Breeze API Key cannot be empty."})
    if not api_secret:
        return JSONResponse(status_code=400, content={"status": "error", "message": "Breeze API Secret cannot be empty."})
    if not session_token:
        return JSONResponse(status_code=400, content={"status": "error", "message": "Breeze Session Token cannot be empty."})

    from trading_engine.core.keyvault import set_secret
    set_secret("BREEZE_API_KEY", api_key)
    set_secret("BREEZE_API_SECRET", api_secret)
    set_secret("BREEZE_SESSION_TOKEN", session_token)

    try:
        from trading_engine.adapters.breeze_adapter import default_breeze_adapter
        default_breeze_adapter.reset()
        default_breeze_adapter.connect()
    except Exception as e:
        print(f"Server: Error reloading Breeze client: {e}", flush=True)

    return {
        "status": "success",
        "message": "ICICI Direct Breeze credentials encrypted and saved to KeyVault. Live client reloaded."
    }


@app.post("/api/vault/breeze-autologin-credentials")
async def update_breeze_autologin_vault_credentials(payload: Dict[str, Any] = Body(...)):
    """
    Encrypts and persists full Zero-Touch ICICI Direct Breeze credentials (API Key, Secret, User ID, Password, TOTP Secret)
    into the KeyVault. Optionally triggers immediate auto-login.
    """
    api_key = str(payload.get("api_key", "")).strip()
    api_secret = str(payload.get("api_secret", "")).strip()
    user_id = str(payload.get("user_id", "")).strip()
    password = str(payload.get("password", "")).strip()
    totp_key = str(payload.get("totp_key", "")).strip().replace(" ", "").upper()
    trigger_now = bool(payload.get("trigger_now", True))

    from trading_engine.core.keyvault import set_secret
    if api_key: set_secret("BREEZE_API_KEY", api_key)
    if api_secret: set_secret("BREEZE_API_SECRET", api_secret)
    if user_id: set_secret("BREEZE_USER_ID", user_id)
    if password: set_secret("BREEZE_PASSWORD", password)
    if totp_key: set_secret("BREEZE_TOTP_KEY", totp_key)

    result = {
        "status": "success",
        "message": "Breeze Zero-Touch credentials encrypted and stored in KeyVault."
    }

    if trigger_now:
        import asyncio
        from trading_engine.adapters.breeze_autologin import BreezeAutoLogin
        auto = BreezeAutoLogin()
        prereq = auto.check_prerequisites()
        if prereq["success"]:
            loop = asyncio.get_event_loop()
            login_res = await loop.run_in_executor(None, auto.execute_login)
            result["auto_login"] = login_res
        else:
            result["auto_login"] = prereq

    return result


@app.post("/api/broker/breeze/auto-login")
async def trigger_breeze_auto_login():
    """
    Triggers the 100% Zero-Touch TOTP Playwright Auto-Login sequence.
    """
    import asyncio
    from trading_engine.adapters.breeze_autologin import BreezeAutoLogin

    auto = BreezeAutoLogin()
    prereq = auto.check_prerequisites()
    if not prereq["success"]:
        return JSONResponse(status_code=400, content=prereq)

    loop = asyncio.get_event_loop()
    res = await loop.run_in_executor(None, auto.execute_login)
    status_code = 200 if res.get("success") else 500
    return JSONResponse(status_code=status_code, content=res)


@app.api_route("/api/broker/breeze/callback", methods=["GET", "POST"])
async def breeze_oauth_callback(request: Request, apisession: Optional[str] = None):
    """
    Callback endpoint for ICICI Direct Breeze OAuth login redirect.
    Supports both GET query parameters and POST forms/redirects.
    Extracts the apisession token and updates KeyVault.
    """
    token = apisession or request.query_params.get("apisession")
    if not token and request.method == "POST":
        try:
            form = await request.form()
            token = form.get("apisession")
        except Exception:
            pass
        if not token:
            try:
                body = await request.json()
                token = body.get("apisession")
            except Exception:
                pass

    if not token:
        return HTMLResponse(
            "<h3>⚠️ Error: No apisession parameter received from ICICI Direct.</h3>",
            status_code=400
        )

    from trading_engine.core.keyvault import set_secret
    set_secret("BREEZE_SESSION_TOKEN", str(token).strip())
    set_secret("ACTIVE_BROKER", "breeze")

    try:
        from trading_engine.adapters.breeze_adapter import default_breeze_adapter
        default_breeze_adapter.reset()
        default_breeze_adapter.connect()
    except Exception as e:
        print(f"Server: Error reloading Breeze client on callback: {e}", flush=True)

    html = """
    <!DOCTYPE html>
    <html>
    <head>
        <title>Breeze Authentication Success</title>
        <meta http-equiv="refresh" content="3; url=/" />
        <style>
            body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: #0f172a; color: #f8fafc; display: flex; align-items: center; justify-content: center; height: 100vh; margin: 0; }
            .card { background: #1e293b; padding: 2rem 3rem; border-radius: 12px; border: 1px solid #334155; text-align: center; box-shadow: 0 10px 25px rgba(0,0,0,0.5); }
            h2 { color: #10b981; margin-bottom: 0.5rem; }
            p { color: #94a3b8; font-size: 0.95rem; }
            .btn { display: inline-block; margin-top: 1rem; padding: 0.6rem 1.2rem; background: #3b82f6; color: white; text-decoration: none; border-radius: 6px; font-weight: 500; }
        </style>
    </head>
    <body>
        <div class="card">
            <h2>✅ Breeze Authenticated Successfully!</h2>
            <p>Your ICICI Direct session token has been encrypted into KeyVault.</p>
            <p>Redirecting to dashboard in 3 seconds...</p>
            <a href="/" class="btn">Return to Dashboard</a>
        </div>
    </body>
    </html>
    """
    return HTMLResponse(content=html)


@app.post("/api/vault/active-broker")
async def set_active_broker_endpoint(payload: Dict[str, Any] = Body(...)):
    """
    Switches active market broker between 'kite' (Zerodha) and 'breeze' (ICICI Direct).
    """
    broker = str(payload.get("broker", "kite")).strip().lower()
    if broker not in ["kite", "breeze"]:
        return JSONResponse(status_code=400, content={"status": "error", "message": "Broker must be 'kite' or 'breeze'."})

    from trading_engine.core.keyvault import set_secret
    set_secret("ACTIVE_BROKER", broker)

    try:
        _cdm.reset_all_brokers()
    except Exception as e:
        print(f"Server: Error resetting broker clients: {e}", flush=True)

    return {
        "status": "success",
        "active_broker": broker,
        "message": f"Active broker switched to {'ICICI Direct Breeze' if broker == 'breeze' else 'Zerodha Kite'}."
    }




async def run_contract_tick_broadcaster():
    """
    Dedicated high-frequency tick broadcaster for /contract WebSocket clients.
    Watches in-memory bus and /tmp/td_live_prices.json every 50ms.
    Pushes CONTRACT_TICK immediately when price changes.
    """
    print("ContractTickBroadcaster: Started zero-latency loop (50ms).", flush=True)
    last_sent_prices: Dict[str, float] = {}

    while True:
        try:
            if is_market_open():
                symbols = get_all_subscribed_symbols()
                if symbols:
                    cache = get_all_live_prices()
                    now_iso = datetime.now().isoformat()
                    for symbol in symbols:
                        entry = cache.get(symbol) or cache.get(symbol.upper())
                        if not entry or not entry.get("price"):
                            continue
                        ltp = float(entry["price"])
                        prev = last_sent_prices.get(symbol)
                        if prev is not None and abs(prev - ltp) < 1e-4:
                            continue
                        last_sent_prices[symbol] = ltp
                        contract_engine.update_price_tick(symbol, ltp)

                        fc = update_forming_candle(symbol, ltp)
                        clients = get_contract_clients(symbol)
                        if not clients:
                            continue
                        packet = {
                            "type": "CONTRACT_TICK",
                            "symbol": symbol,
                            "ltp": round(ltp, 2),
                            "forming_candle": fc,
                            "timestamp": now_iso,
                        }
                        dead = []
                        for ws in clients:
                            try:
                                await asyncio.wait_for(ws.send_json(packet), timeout=0.1)
                            except Exception:
                                dead.append(ws)
                        for ws in dead:
                            unregister_contract_client(symbol, ws)
        except Exception:
            pass
        await asyncio.sleep(0.05)  # 50ms loop = 20 checks/second


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("trading_engine.web.server:app", host="0.0.0.0", port=8080)



