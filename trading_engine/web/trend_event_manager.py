"""
Trend Event Manager
───────────────────
Zero-latency event trigger pipeline for trend signals detected by the
Smart Money Flow (SMF) Cloud and technical engines.

Responsibilities:
  1. Record trend switch events (BUY / SELL) into the TimescaleDB `trend_events` table.
  2. Dispatch real-time Telegram notifications.
  3. Broadcast WebSocket event notifications to active UI sessions.
  4. Provide the extension hook `on_trend_signal_triggered(event)` where automated
     paper trading contract selection logic can be plugged in seamlessly.
"""

import os
import sys
import json
import logging
from datetime import datetime
from zoneinfo import ZoneInfo
from pathlib import Path
from typing import Dict, Any, Optional, List
from sqlalchemy import text

# Ensure root paths are in sys.path
ENGINE_DIR = Path(__file__).resolve().parent.parent
PROJECT_ROOT = ENGINE_DIR.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
if str(ENGINE_DIR) not in sys.path:
    sys.path.insert(0, str(ENGINE_DIR))

from adapters.telegram_notifier import default_telegram_notifier

logger = logging.getLogger("trend_event_manager")
logger.setLevel(logging.INFO)


def _get_ist_now_str() -> str:
    try:
        return datetime.now(ZoneInfo("Asia/Kolkata")).strftime("%Y-%m-%d %H:%M:%S IST")
    except Exception:
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def on_trend_signal_triggered(event: Dict[str, Any]) -> None:
    """
    EXTENSIBLE EVENT TRIGGER HOOK FOR AUTOMATED PAPER TRADING.

    Invoked with zero latency whenever a new trend signal (BUY/SELL) is detected.
    Future trading logic can inspect `event` to:
      - Identify the optimal derivative contract to trade (e.g. ATM CE/PE for nearest expiry).
      - Check risk limits, ATR barriers, and slippage.
      - Dispatch automated paper trading execution.

    Per specification: Automated trading execution is intentionally deferred;
    this hook serves as the designated integration entry-point.
    """
    symbol = event.get("symbol", "UNKNOWN")
    direction = event.get("direction", "UNKNOWN")
    price = event.get("price", 0.0)
    strategy = event.get("strategy", "SMF_CLOUD")
    logger.info(
        f"⚡ [EVENT TRIGGER] on_trend_signal_triggered invoked: "
        f"{symbol} {direction} @ ₹{price:.2f} ({strategy})"
    )


def record_trend_event(
    symbol: str,
    direction: str,
    price: float,
    strategy: str = "SMF_CLOUD",
    timeframe: str = "1m",
    market_regime: Optional[str] = None,
    details: Optional[Dict[str, Any]] = None,
    db_engine=None,
    send_telegram: bool = True,
    broadcast_coro=None,
) -> Dict[str, Any]:
    """
    Core event trigger:
      1. Persists event to TimescaleDB `trend_events` table.
      2. Calls extensible `on_trend_signal_triggered` hook.
      3. Dispatches Telegram notification alert.
      4. Returns structured event payload.
    """
    direction_norm = direction.upper()
    now_ist = _get_ist_now_str()
    now_iso = datetime.now().isoformat()
    regime = market_regime or ("BULLISH" if direction_norm == "BUY" else "BEARISH")
    details_json = json.dumps(details or {})

    event_id: Optional[int] = None

    # 1. Persist to Database
    if db_engine is not None:
        try:
            with db_engine.connect() as conn:
                res = conn.execute(
                    text("""
                        INSERT INTO trend_events (
                            timestamp, symbol, direction, price,
                            strategy, timeframe, market_regime,
                            status, details, notified
                        ) VALUES (
                            NOW(), :symbol, :direction, :price,
                            :strategy, :timeframe, :market_regime,
                            'TRIGGERED', CAST(:details AS jsonb), :notified
                        ) RETURNING id
                    """),
                    {
                        "symbol": symbol,
                        "direction": direction_norm,
                        "price": float(price),
                        "strategy": strategy,
                        "timeframe": timeframe,
                        "market_regime": regime,
                        "details": details_json,
                        "notified": send_telegram,
                    }
                )
                conn.commit()
                row = res.fetchone()
                if row:
                    event_id = row[0]
        except Exception as e:
            logger.error(f"Failed to record trend event in DB for {symbol}: {e}")

    event_payload = {
        "event_id": event_id,
        "symbol": symbol,
        "direction": direction_norm,
        "price": float(price),
        "strategy": strategy,
        "timeframe": timeframe,
        "market_regime": regime,
        "timestamp": now_iso,
        "timestamp_ist": now_ist,
        "status": "TRIGGERED",
        "details": details or {},
    }

    # 2. Invoke extensible hook for Automated Trading contract selection
    try:
        on_trend_signal_triggered(event_payload)
    except Exception as e:
        logger.error(f"Error in on_trend_signal_triggered hook: {e}")

    # 3. Dispatch Telegram notification
    if send_telegram and default_telegram_notifier.is_configured():
        try:
            dir_emoji = "🟢 <b>BUY / BULLISH</b>" if direction_norm == "BUY" else "🔴 <b>SELL / BEARISH</b>"
            limit_p = det.get("limit_price")
            target_p = det.get("target1") or det.get("target_price")
            target2_p = det.get("target2")
            be_lock_p = det.get("be_lock")
            sl_p = det.get("sl")
            session_win = det.get("session_window")

            pricing_lines = f"💰 <b>Trigger Rate:</b> ₹{price:,.2f}\n"
            order_rec = det.get("recommended_order") or ("MARKET" if det.get("is_rocket") else None)
            if order_rec == "MARKET":
                pricing_lines += f"⚡ <b>Execution:</b> <code>⚡ MARKET ORDER (Rocket Move)</code>\n"
            elif limit_p:
                pricing_lines += f"📥 <b>Execution:</b> <code>📥 LIMIT ORDER @ ₹{float(limit_p):,.2f}</code>\n"
            if target_p:
                pricing_lines += f"🎯 <b>T1 (Scalp 50%):</b> ₹{float(target_p):,.2f}\n"
            if be_lock_p:
                pricing_lines += f"🛡️ <b>BE Lock Level:</b> ₹{float(be_lock_p):,.2f} (Move SL to Cost)\n"
            if target2_p:
                pricing_lines += f"🚀 <b>T2 (Runner 50%):</b> ₹{float(target2_p):,.2f}\n"
            if sl_p:
                pricing_lines += f"🛑 <b>Stop Loss:</b> ₹{float(sl_p):,.2f}\n"
            if session_win:
                pricing_lines += f"⏱ <b>Session Window:</b> <code>{session_win}</code>\n"

            msg = (
                f"🚨 <b>TREND SIGNAL DETECTED</b>\n\n"
                f"📊 <b>Symbol:</b> <code>{symbol}</code>\n"
                f"🧭 <b>Signal:</b> {dir_emoji}\n"
                f"{pricing_lines}"
                f"⏱ <b>Timeframe:</b> {timeframe} | <b>Strategy:</b> {strategy}\n"
                f"⏰ <b>Detected:</b> <code>{now_ist}</code>\n\n"
                f"⚡ <b>Event Hook:</b> <i>Trigger dispatched. Ready for automated contract selection.</i>"
            )
            default_telegram_notifier.send_message(msg)
        except Exception as e:
            logger.error(f"Failed to send Telegram trend event alert: {e}")

    return event_payload


def get_recent_trend_events(limit: int = 50, db_engine=None) -> List[Dict[str, Any]]:
    """Retrieve recent trend signal events from the database."""
    if db_engine is None:
        return []
    try:
        with db_engine.connect() as conn:
            rows = conn.execute(
                text("""
                    SELECT id, timestamp, symbol, direction, price,
                           strategy, timeframe, market_regime, status, details
                    FROM trend_events
                    ORDER BY timestamp DESC
                    LIMIT :limit
                """),
                {"limit": limit}
            ).fetchall()

            events = []
            for r in rows:
                events.append({
                    "id": r[0],
                    "timestamp": r[1].isoformat() if hasattr(r[1], "isoformat") else str(r[1]),
                    "symbol": r[2],
                    "direction": r[3],
                    "price": float(r[4]),
                    "strategy": r[5],
                    "timeframe": r[6],
                    "market_regime": r[7],
                    "status": r[8],
                    "details": r[9] if isinstance(r[9], dict) else json.loads(r[9] or "{}"),
                })
            return events
    except Exception as e:
        logger.error(f"Failed to query recent trend events: {e}")
        return []
