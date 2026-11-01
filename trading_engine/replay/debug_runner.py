"""
Native Python Replay Debug Runner for VS Code and Google Antigravity IDE.
Executes an indicator with real-time or stepped market replay, integrating natively
with VS Code's debugpy runtime and streaming state updates over local WebSocket IPC
to the Quant Studio Chart Webview.
"""

import argparse
import asyncio
import importlib.util
import inspect
import json
import logging
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, Optional, List, Set

# Ensure workspace root is always in sys.path regardless of how python is invoked
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import numpy as np
import pandas as pd
import websockets
from websockets.server import WebSocketServerProtocol

from trading_engine.quant_lib.base import Indicator
from trading_engine.replay.session import ReplaySession, ReplayFrame
from trading_engine.data.duckdb_store import LocalDataStore
from trading_engine.web.studio_router import _generate_synthetic_candles, _build_history_payload
from trading_engine.config import get_symbol_lot_size

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("quant_studio.debug_runner")


def load_indicator_from_file(file_path: str) -> Indicator:
    """
    Dynamically loads and instantiates the first concrete Indicator subclass
    found in the given Python file.
    """
    path = Path(file_path).resolve()
    if not path.exists():
        raise FileNotFoundError(f"Indicator file not found: {file_path}")

    module_name = f"dynamic_indicator_{path.stem}"
    spec = importlib.util.spec_from_file_location(module_name, str(path))
    if spec is None or spec.loader is None:
        raise ImportError(f"Could not load module spec from: {path}")

    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)

    # Search for an Indicator subclass defined in this module
    indicator_cls = None
    for name, obj in inspect.getmembers(module, inspect.isclass):
        if issubclass(obj, Indicator) and obj is not Indicator:
            indicator_cls = obj
            break

    if indicator_cls is None:
        # Check if user pressed F5 while viewing a non-indicator file (e.g., backtester.py)
        fallback_path = Path(__file__).resolve().parent.parent / "indicators" / "bss_pro_v5.py"
        if fallback_path.exists() and path != fallback_path:
            logger.warning(
                f"No concrete Indicator subclass found in '{path.name}'. "
                f"Auto-falling back to primary indicator: {fallback_path}"
            )
            return load_indicator_from_file(str(fallback_path))
        raise ValueError(
            f"No concrete Indicator subclass found in {file_path}. "
            f"Please open an indicator file (e.g. trading_engine/indicators/bss_pro_v5.py) before starting debug."
        )

    logger.info(f"Loaded indicator class '{indicator_cls.__name__}' from {file_path}")
    return indicator_cls()


class ReplayDebugServer:
    """
    Embedded WebSocket IPC Server coordinating between native debugpy execution
    and the VS Code / Antigravity Chart Webview panel.
    """

    def __init__(
        self,
        indicator: Indicator,
        symbol: str = "NIFTY",
        contract: str = "NIFTY_ATM_CE",
        date_str: str = "2026-10-30",
        end_date_str: Optional[str] = None,
        warmup_bars: int = 50,
        host: str = "127.0.0.1",
        port: int = 8765,
        speed_ticks_per_sec: float = 2.0,
    ):
        self.indicator = indicator
        self.symbol = symbol
        self.contract = contract
        self.date_str = date_str
        self.end_date_str = end_date_str or date_str
        self.warmup_bars = warmup_bars
        self.host = host
        self.port = port
        self.speed = speed_ticks_per_sec
        self.indicator_file = getattr(indicator, "_file_path", "")

        self.session: Optional[ReplaySession] = None
        self.connected_clients: Set[WebSocketServerProtocol] = set()
        self.is_running = False
        self.is_playing = False
        self._play_task: Optional[asyncio.Task] = None
        self._server = None

        self.quant_studio_state: Dict[str, Any] = {
            "session": {
                "symbol": self.symbol,
                "contract": self.contract,
                "date": self.date_str,
                "indicator_name": self.indicator.__class__.__name__,
                "indicator_file": self.indicator_file,
                "tick_cursor": 0,
                "cursor": 0,
                "total_bars": 0,
                "total_ticks": 0,
                "speed": self.speed,
                "is_playing": False,
                "current_timestamp": "",
            },
            "viewport": {
                "visible_from": None,
                "visible_to": None,
                "visible_from_str": None,
                "visible_to_str": None,
                "timeframe": "1m",
                "candle_count_in_view": 0,
                "focused_trade": None,
                "focused_trade_index": None,
            },
            "last_backtest": None,
            "last_updated": datetime.now().isoformat()
        }

        self._init_session()

    def _persist_state(self):
        """Atomically saves unified Quant Studio telemetry and state to disk."""
        try:
            report_dir = os.path.join(PROJECT_ROOT, "reports")
            os.makedirs(report_dir, exist_ok=True)
            self.quant_studio_state["last_updated"] = datetime.now().isoformat()
            if self.session:
                sess_info = self.quant_studio_state["session"]
                sess_info["tick_cursor"] = self.session.tick_cursor
                sess_info["cursor"] = self.session.cursor
                sess_info["total_bars"] = self.session.total_bars
                sess_info["total_ticks"] = self.session.total_ticks
                sess_info["date"] = self.date_str
                if self.session.last_frame:
                    sess_info["current_timestamp"] = self.session.last_frame.timestamp
            self.quant_studio_state["session"]["is_playing"] = self.is_playing
            self.quant_studio_state["session"]["speed"] = self.speed

            state_file = os.path.join(report_dir, "quant_studio_state.json")
            with open(state_file, "w") as f:
                json.dump(self.quant_studio_state, f, indent=2)

            if self.quant_studio_state.get("last_backtest"):
                bt_file = os.path.join(report_dir, "last_backtest.json")
                with open(bt_file, "w") as f:
                    json.dump(self.quant_studio_state["last_backtest"], f, indent=2)

            if self.quant_studio_state.get("viewport"):
                vp_file = os.path.join(report_dir, "viewport_state.json")
                with open(vp_file, "w") as f:
                    json.dump(self.quant_studio_state["viewport"], f, indent=2)
        except Exception as e:
            logger.debug(f"Failed to persist state: {e}")

    def _init_session(self):
        """Prepares market data and initializes ReplaySession."""
        store = LocalDataStore()
        start_ts = f"{self.date_str} 09:15:00"
        end_ts = f"{self.end_date_str} 15:30:00"
        df = pd.DataFrame()
        try:
            df = store.query_candles(self.symbol, self.contract, start_ts, end_ts)
        except Exception as e:
            logger.warning(f"Could not query database directly ({e}), generating synthetic market session.")
            df = pd.DataFrame()

        if df.empty or len(df) < 50:
            try:
                days = max(1, (pd.to_datetime(self.end_date_str) - pd.to_datetime(self.date_str)).days + 1)
            except Exception:
                days = 1
            total_bars = min(1500, days * 375)
            df = _generate_synthetic_candles(self.symbol, self.contract, total_bars, date_str=self.date_str)

        self.session = ReplaySession(
            df,
            self.indicator,
            warmup_bars=self.warmup_bars,
            break_on_signal=True
        )
        logger.info(
            f"Initialized ReplaySession: {self.session.total_bars} bars, "
            f"{self.session.total_ticks} ticks (Date Range: {self.date_str} to {self.end_date_str}, Symbol: {self.symbol})"
        )
        self._persist_state()

    def get_init_payload(self) -> Dict[str, Any]:
        """Builds session summary and warmup bars for client handshake."""
        history = _build_history_payload(self.session)
        last_frame_dict = self.session.last_frame.to_dict() if self.session.last_frame else None
        return {
            "type": "init",
            "symbol": self.symbol,
            "contract": self.contract,
            "lot_size": get_symbol_lot_size(self.symbol),
            "date": self.date_str,
            "start_date": self.date_str,
            "end_date": self.end_date_str,
            "total_bars": self.session.total_bars,
            "total_ticks": self.session.total_ticks,
            "cursor": self.session.cursor,
            "tick_cursor": self.session.tick_cursor,
            "warmup_bars": self.warmup_bars,
            "history_bars": history,
            "latest_frame": last_frame_dict,
            "is_playing": self.is_playing,
            "speed": self.speed,
        }

    async def broadcast(self, message: Dict[str, Any]):
        """Broadcasts a JSON frame to all connected Webview panels."""
        if not self.connected_clients:
            return
        payload = json.dumps(message)
        dead = []
        for client in self.connected_clients:
            try:
                await client.send(payload)
            except Exception:
                dead.append(client)
        for client in dead:
            self.connected_clients.discard(client)

    async def step(self, ticks: int = 1) -> Optional[ReplayFrame]:
        """Advances replay by `ticks` ticks and emits frame to Webview."""
        if not self.session:
            return None
        frame = self.session.step_tick_forward(ticks)
        if frame:
            await self.broadcast({
                "type": "frame",
                "frame": frame.to_dict(),
                "tick_cursor": self.session.tick_cursor,
                "cursor": self.session.cursor,
            })
        return frame

    async def step_bar(self, bars: int = 1) -> Optional[ReplayFrame]:
        """Advances replay by bars candles."""
        if not self.session:
            return None
        frame = self.session.step_bar_forward(bars)
        if frame:
            await self.broadcast({
                "type": "frame",
                "frame": frame.to_dict(),
                "tick_cursor": self.session.tick_cursor,
                "cursor": self.session.cursor,
            })
        return frame

    async def seek(self, tick_index: int):
        """Seeks to a specific tick index."""
        if not self.session:
            return
        frame = self.session.scrub_to(tick_index, mode="tick")
        history = _build_history_payload(self.session)
        await self.broadcast({
            "type": "seek",
            "tick_cursor": self.session.tick_cursor,
            "cursor": self.session.cursor,
            "history_bars": history,
            "frame": frame.to_dict() if frame else None,
        })

    async def load_date(self, start_date: str, end_date: Optional[str] = None):
        """Reloads market data and resets ReplaySession for a specific market date or date range."""
        self.pause()
        self.date_str = start_date
        self.end_date_str = end_date or start_date
        self._init_session()
        logger.info(f"Session reloaded for date range: {self.date_str} to {self.end_date_str}")
        await self.broadcast(self.get_init_payload())

    async def run_backtest_range(
        self,
        start_date: str,
        end_date: str,
        charges_data: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """Executes vectorized backtest across historical date range with ICICI Direct charges."""
        from trading_engine.replay.backtester import VectorizedBacktester, BrokerChargesConfig
        store = LocalDataStore()
        start_ts = f"{start_date} 09:15:00"
        end_ts = f"{end_date} 15:30:00"
        df = pd.DataFrame()
        try:
            df = store.query_candles(self.symbol, self.contract, start_ts, end_ts)
        except Exception as e:
            logger.warning(f"Backtest query failed ({e}), generating synthetic dataset.")
            df = pd.DataFrame()

        if df.empty or len(df) < 50:
            try:
                days = max(1, (pd.to_datetime(end_date) - pd.to_datetime(start_date)).days + 1)
            except Exception:
                days = 1
            total_bars = min(1500, days * 375)
            df = _generate_synthetic_candles(self.symbol, self.contract, total_bars, date_str=start_date)

        cfg = BrokerChargesConfig()
        cfg.lot_size = get_symbol_lot_size(self.symbol)
        if charges_data and isinstance(charges_data, dict):
            if "brokerage_per_order" in charges_data:
                cfg.brokerage_per_order = float(charges_data["brokerage_per_order"])
            if "lot_size" in charges_data and charges_data["lot_size"]:
                cfg.lot_size = int(charges_data["lot_size"])
            if "stt_rate" in charges_data:
                cfg.stt_rate = float(charges_data["stt_rate"])
            if "broker_name" in charges_data:
                cfg.broker_name = str(charges_data["broker_name"])

        backtester = VectorizedBacktester(lot_size=cfg.lot_size, charges_config=cfg)
        indicator_copy = type(self.indicator)()
        scorecard = backtester.run_backtest(df, indicator_copy, warmup_bars=self.warmup_bars)
        result_dict = scorecard.to_dict()

        # Build synchronized history_bars for complete chart display across backtested range
        history_bars = []
        for _, row in df.iterrows():
            history_bars.append({
                "timestamp": str(row.get("timestamp")),
                "spot_open": float(row.get("spot_open", row.get("open", 100))),
                "spot_high": float(row.get("spot_high", row.get("high", 100))),
                "spot_low": float(row.get("spot_low", row.get("low", 100))),
                "spot_close": float(row.get("spot_close", row.get("close", 100))),
                "opt_open": float(row.get("opt_open", 100)),
                "opt_high": float(row.get("opt_high", 100)),
                "opt_low": float(row.get("opt_low", 100)),
                "opt_close": float(row.get("opt_close", 100)),
            })
        result_dict["history_bars"] = history_bars

        try:
            report_dir = os.path.join(PROJECT_ROOT, "reports")
            os.makedirs(report_dir, exist_ok=True)
            with open(os.path.join(report_dir, "last_backtest_result.json"), "w") as f:
                json.dump(result_dict, f, indent=2)
        except Exception as err:
            logger.debug(f"Could not write last_backtest_result.json: {err}")
        return result_dict

    def play(self, speed: Optional[float] = None):
        """Starts continuous playback task."""
        if speed is not None:
            self.speed = max(0.2, min(50.0, speed))
        self.is_playing = True
        if self._play_task is None or self._play_task.done():
            self._play_task = asyncio.create_task(self._playback_loop())

    def pause(self):
        """Pauses continuous playback."""
        self.is_playing = False
        if self._play_task and not self._play_task.done():
            self._play_task.cancel()
            self._play_task = None

    async def _playback_loop(self):
        """Worker loop for auto playback."""
        try:
            while self.is_playing and self.session and self.session.tick_cursor < self.session.total_ticks - 1:
                frame = await self.step(1)
                if frame and frame.breakpoint_hit:
                    logger.info(f"Replay paused on breakpoint/signal: {frame.breakpoint_reason}")
                    self.pause()
                    await self.broadcast({"type": "status", "is_playing": False, "reason": "breakpoint_hit"})
                # Realtime pacing: 1 candle represents 60 seconds of market time.
                # At 1x speed, 300 ticks are emitted over exactly 60 wall-clock seconds (200ms per tick).
                # At higher speed multipliers (e.g. 5x, 10x), delay is scaled down proportionally.
                ticks_per_candle = getattr(self.session, "TICKS_PER_CANDLE", 300)
                base_tick_interval = 60.0 / max(1, ticks_per_candle)
                delay = base_tick_interval / max(0.1, self.speed)
                await asyncio.sleep(delay)
        except asyncio.CancelledError:
            pass
        finally:
            self.is_playing = False
            await self.broadcast({"type": "status", "is_playing": False})

    async def handle_client(self, websocket: WebSocketServerProtocol):
        """Handles an incoming WebSocket connection from VS Code / Antigravity Webview."""
        self.connected_clients.add(websocket)
        logger.info(f"Webview client connected from {websocket.remote_address}")
        try:
            # Send initial session handshake
            await websocket.send(json.dumps(self.get_init_payload()))

            async for raw in websocket:
                try:
                    data = json.loads(raw)
                    action = data.get("action")
                    if action == "step":
                        ticks = int(data.get("ticks", 1))
                        await self.step(ticks)
                    elif action == "step_bar":
                        await self.step_bar()
                    elif action == "play":
                        spd = float(data.get("speed", self.speed))
                        self.play(spd)
                        await self.broadcast({"type": "status", "is_playing": True, "speed": self.speed})
                    elif action == "pause":
                        self.pause()
                        await self.broadcast({"type": "status", "is_playing": False})
                    elif action == "seek":
                        tick_idx = int(data.get("tick", 0))
                        await self.seek(tick_idx)
                    elif action == "set_speed":
                        self.speed = float(data.get("speed", self.speed))
                        await self.broadcast({"type": "status", "speed": self.speed})
                    elif action == "load_date":
                        s_dt = data.get("start_date") or data.get("date", self.date_str)
                        e_dt = data.get("end_date") or s_dt
                        logger.info(f"Loading market session dates: {s_dt} to {e_dt}")
                        await self.load_date(s_dt, e_dt)
                    elif action == "sync_viewport":
                        vp = data.get("viewport", {})
                        if isinstance(vp, dict):
                            self.quant_studio_state["viewport"].update(vp)
                            self._persist_state()
                    elif action == "focus_trade":
                        tr = data.get("trade")
                        idx = data.get("trade_index")
                        self.quant_studio_state["viewport"]["focused_trade"] = tr
                        self.quant_studio_state["viewport"]["focused_trade_index"] = idx
                        self._persist_state()
                    elif action == "get_state":
                        self._persist_state()
                        await websocket.send(json.dumps({
                            "type": "quant_studio_state",
                            "state": self.quant_studio_state
                        }))
                    elif action == "run_backtest":
                        s_dt = data.get("start_date", self.date_str)
                        e_dt = data.get("end_date", self.date_str)
                        charges_cfg = data.get("charges_config", {})
                        logger.info(f"Running backtest from {s_dt} to {e_dt} with charges: {charges_cfg}...")
                        result = await self.run_backtest_range(s_dt, e_dt, charges_cfg)
                        self.quant_studio_state["last_backtest"] = result
                        self.quant_studio_state["session"]["backtest_date_range"] = {"start_date": s_dt, "end_date": e_dt}
                        self._persist_state()
                        await websocket.send(json.dumps({
                            "type": "backtest_result",
                            "start_date": s_dt,
                            "end_date": e_dt,
                            "scorecard": result,
                            "history_bars": result.get("history_bars", [])
                        }))
                    elif action == "ping":
                        await websocket.send(json.dumps({"type": "pong"}))
                except Exception as ex:
                    logger.error(f"Error handling command: {ex}", exc_info=True)
                    await websocket.send(json.dumps({"type": "error", "message": str(ex)}))
        except websockets.exceptions.ConnectionClosed:
            pass
        finally:
            self.connected_clients.discard(websocket)
            logger.info("Webview client disconnected")

    async def start(self):
        """Starts the WebSocket IPC server."""
        self.is_running = True
        self._server = await websockets.serve(self.handle_client, self.host, self.port)
        logger.info(f"Quant Studio Debug IPC listening on ws://{self.host}:{self.port}")

    async def stop(self):
        """Stops server and closes all client connections."""
        self.pause()
        self.is_running = False
        if self._server:
            self._server.close()
            await self._server.wait_closed()
        for client in list(self.connected_clients):
            await client.close()
        self.connected_clients.clear()
        logger.info("Quant Studio Debug IPC server stopped")


def parse_args():
    parser = argparse.ArgumentParser(description="Quant Studio Native Debug Runner")
    parser.add_argument("--file", "-f", required=True, help="Path to indicator .py file")
    parser.add_argument("--symbol", "-s", default="NIFTY", help="Symbol name (default: NIFTY)")
    parser.add_argument("--contract", "-c", default="NIFTY_ATM_CE", help="Option contract (default: NIFTY_ATM_CE)")
    parser.add_argument("--date", "-d", default="2026-10-30", help="Trading session date (YYYY-MM-DD)")
    parser.add_argument("--port", "-p", type=int, default=8765, help="WebSocket IPC port (default: 8765)")
    parser.add_argument("--warmup", type=int, default=50, help="Warmup bars (default: 50)")
    parser.add_argument("--speed", type=float, default=5.0, help="Ticks per second playback speed")
    parser.add_argument("--headless", action="store_true", help="Auto-play without waiting for Webview")
    parser.add_argument("--ticks", type=int, default=None, help="Max ticks to run before terminating")
    return parser.parse_args()


async def main_async(args):
    indicator = load_indicator_from_file(args.file)
    indicator._file_path = str(args.file)
    server = ReplayDebugServer(
        indicator=indicator,
        symbol=args.symbol,
        contract=args.contract,
        date_str=args.date,
        warmup_bars=args.warmup,
        port=args.port,
        speed_ticks_per_sec=args.speed,
    )
    await server.start()

    if args.headless:
        logger.info("Running in headless mode...")
        server.play()
        if args.ticks:
            for _ in range(args.ticks):
                await server.step(1)
                await asyncio.sleep(0.01)
            server.pause()
            await server.stop()
            return

    try:
        # Keep process running for debugpy / interactive commands
        while True:
            await asyncio.sleep(1)
    except (asyncio.CancelledError, KeyboardInterrupt):
        logger.info("Shutting down debug runner...")
        await server.stop()


def main():
    args = parse_args()
    try:
        asyncio.run(main_async(args))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
