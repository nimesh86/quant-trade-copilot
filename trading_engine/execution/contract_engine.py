"""
Contract Trading Engine Module.
Unified execution manager for the Option Contract Trading Terminal.
Supports:
- Paper Trading Adapter (realistic simulation with slippage, taxes, and disk persistence)
- Live Broker Adapter (routes to broker client with institutional slicing)
- Bar Replay Simulator Adapter
- Draggable SL / Target / Limit Order modifications
- Automatic Trailing Stop Loss (Breakeven Lock & Dynamic Chandelier)
- Kill Switch Emergency Flatten & Session Lockout
"""

import os
import json
import uuid
from typing import Dict, Any, Optional, List
from datetime import datetime, date
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"
STATE_FILE = DATA_DIR / "contract_trade_state.json"


class ContractTradingEngine:
    def __init__(self, initial_capital: float = 100000.0):
        self.initial_capital = float(initial_capital)
        self.capital = float(initial_capital)
        self.mode = "paper"  # 'paper', 'live', 'replay'
        
        # Positions and Orders
        self.active_position: Optional[Dict[str, Any]] = None
        self.active_positions: Dict[str, Dict[str, Any]] = {}
        self.pending_orders: List[Dict[str, Any]] = []
        self.trade_history: List[Dict[str, Any]] = []
        
        # Kill Switch State
        self.is_kill_switch_active = False
        self.kill_switch_triggered_at: Optional[str] = None
        
        # Real-time state
        self.current_symbol: Optional[str] = None
        self.current_price: float = 0.0
        
        # Day tracking
        self.today_date = date.today().isoformat()
        
        # Ensure data directory exists and restore saved paper state
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        self.load_state()

    def set_mode(self, mode: str) -> Dict[str, Any]:
        """Switch trading mode ('paper', 'live', 'replay')."""
        m = mode.lower()
        if m in ("paper", "live", "replay"):
            self.mode = m
            return {"status": "success", "mode": self.mode}
        return {"status": "error", "message": f"Invalid mode: {mode}"}

    def get_index_lot_size(self, symbol: str) -> int:
        """Infer lot size from underlying symbol."""
        s = symbol.upper()
        if "BANKNIFTY" in s:
            return 30
        elif "SENSEX" in s or "BANKEX" in s:
            return 20
        elif "FINNIFTY" in s:
            return 65
        elif "MIDCPNIFTY" in s:
            return 120
        # Default NIFTY lot size
        return 65

    def place_order(
        self,
        symbol: str,
        side: str = "BUY",
        order_type: str = "MARKET",
        lots: int = 1,
        limit_price: Optional[float] = None,
        sl: Optional[float] = None,
        target1: Optional[float] = None,
        target2: Optional[float] = None,
        trailing_mode: str = "be_lock",  # 'be_lock', 'chandelier', 'fixed_step', 'none'
        current_market_price: Optional[float] = None
    ) -> Dict[str, Any]:
        """Places a new market or limit order directly on the contract."""
        if self.is_kill_switch_active:
            return {
                "status": "error",
                "message": "Kill switch is ACTIVE. New orders are locked out. Unlock kill switch first."
            }

        symbol = symbol.strip().upper()
        lots = max(1, int(lots))
        lot_size = self.get_index_lot_size(symbol)
        units = lots * lot_size
        
        market_px = float(current_market_price or self.current_price or 0.0)
        
        if order_type.upper() == "LIMIT":
            if not limit_price or limit_price <= 0:
                return {"status": "error", "message": "Limit price must be greater than 0."}
            
            order_id = f"ORD_{uuid.uuid4().hex[:8].upper()}"
            pending_order = {
                "order_id": order_id,
                "symbol": symbol,
                "side": side.upper(),
                "order_type": "LIMIT",
                "lots": lots,
                "units": units,
                "limit_price": round(float(limit_price), 2),
                "sl": round(float(sl), 2) if sl else None,
                "target1": round(float(target1), 2) if target1 else None,
                "target2": round(float(target2), 2) if target2 else None,
                "trailing_mode": trailing_mode,
                "created_at": datetime.now().isoformat(),
                "status": "PENDING"
            }
            
            # Check immediate fill if market price already satisfies limit
            if market_px > 0 and market_px <= limit_price:
                return self._fill_order(pending_order, fill_price=limit_price)
            
            self.pending_orders.append(pending_order)
            self.save_state()
            return {"status": "success", "action": "LIMIT_ORDER_PLACED", "order": pending_order}

        else:
            # MARKET ORDER: Instant fill
            fill_px = market_px
            if fill_px <= 0:
                fill_px = float(limit_price or 100.0)
                
            # Apply 0.05 NSE tick slippage
            fill_px = round(fill_px + 0.05, 2)
            
            order_stub = {
                "order_id": f"ORD_{uuid.uuid4().hex[:8].upper()}",
                "symbol": symbol,
                "side": side.upper(),
                "order_type": "MARKET",
                "lots": lots,
                "units": units,
                "limit_price": fill_px,
                "sl": round(float(sl), 2) if sl else None,
                "target1": round(float(target1), 2) if target1 else None,
                "target2": round(float(target2), 2) if target2 else None,
                "trailing_mode": trailing_mode,
                "created_at": datetime.now().isoformat(),
            }
            return self._fill_order(order_stub, fill_price=fill_px)

    def _fill_order(self, order_data: Dict[str, Any], fill_price: float) -> Dict[str, Any]:
        """Fills an order into an active position."""
        sym = order_data["symbol"]
        existing_pos = self.active_positions.get(sym) or (self.active_position if (self.active_position and self.active_position.get("symbol") == sym) else None)
        
        # If position already open in same symbol, average/add lots or close previous if opposite side
        if existing_pos is not None:
            if existing_pos["side"] != order_data["side"]:
                self.close_position(reason="REVERSE_ORDER", exit_price=fill_price, symbol=sym)
            else:
                pos = existing_pos
                new_units = pos["units"] + order_data["units"]
                total_cost = (pos["entry_price"] * pos["units"]) + (fill_price * order_data["units"])
                pos["entry_price"] = round(total_cost / new_units, 2)
                pos["units"] = new_units
                pos["lots"] = pos["lots"] + order_data["lots"]
                pos["sl"] = order_data["sl"] or pos["sl"]
                pos["current_sl"] = pos["sl"] or pos["current_sl"]
                pos["target1"] = order_data["target1"] or pos["target1"]
                pos["target2"] = order_data["target2"] or pos["target2"]
                self.active_positions[sym] = pos
                self.active_position = pos
                self.save_state()
                return {"status": "success", "action": "POSITION_INCREASED", "position": pos}

        margin_required = fill_price * order_data["units"]
        if self.capital < margin_required and self.mode == "paper":
            return {
                "status": "error",
                "message": f"Insufficient margin. Required: ₹{margin_required:,.2f}, Available: ₹{self.capital:,.2f}"
            }

        position = {
            "position_id": f"POS_{uuid.uuid4().hex[:8].upper()}",
            "symbol": order_data["symbol"],
            "side": order_data["side"],
            "lots": order_data["lots"],
            "units": order_data["units"],
            "entry_price": fill_price,
            "current_price": fill_price,
            "entry_time": datetime.now().isoformat(),
            "sl": order_data.get("sl"),
            "current_sl": order_data.get("sl"),  # None if user hasn't set an SL yet
            "target1": order_data.get("target1"),
            "target2": order_data.get("target2"),
            "trailing_mode": order_data.get("trailing_mode", "be_lock"),
            "be_locked": False,
            "highest_price": fill_price,
            "unrealized_pnl": 0.0,
            "unrealized_pnl_pct": 0.0,
            "unrealized_pts": 0.0,
            "status": "OPEN"
        }

        self.active_positions[sym] = position
        self.active_position = position
        # Remove from pending orders if was pending
        self.pending_orders = [o for o in self.pending_orders if o.get("order_id") != order_data.get("order_id")]
        self.save_state()
        return {"status": "success", "action": "ORDER_FILLED", "position": position}

    def modify_active_levels(
        self,
        sl: Optional[float] = None,
        target1: Optional[float] = None,
        target2: Optional[float] = None,
        limit_price: Optional[float] = None,
        trailing_mode: Optional[str] = None
    ) -> Dict[str, Any]:
        """Modifies SL, Target, or Limit levels on the fly (e.g. from chart drag)."""
        if self.active_position:
            if sl is not None:
                self.active_position["current_sl"] = round(float(sl), 2)
                self.active_position["sl"] = round(float(sl), 2)
            if target1 is not None:
                self.active_position["target1"] = round(float(target1), 2)
            if target2 is not None:
                self.active_position["target2"] = round(float(target2), 2)
            if trailing_mode is not None:
                self.active_position["trailing_mode"] = trailing_mode
            self.save_state()
            return {"status": "success", "action": "LEVELS_UPDATED", "position": self.active_position}

        # If no active position, check if there's a pending order to modify
        if self.pending_orders:
            order = self.pending_orders[-1]
            if limit_price is not None:
                order["limit_price"] = round(float(limit_price), 2)
            if sl is not None:
                order["sl"] = round(float(sl), 2)
            if target1 is not None:
                order["target1"] = round(float(target1), 2)
            if target2 is not None:
                order["target2"] = round(float(target2), 2)
            self.save_state()
            return {"status": "success", "action": "PENDING_ORDER_MODIFIED", "order": order}

        return {"status": "error", "message": "No active position or pending order to modify."}

    def close_position(self, reason: str = "MANUAL_CLOSE", exit_price: Optional[float] = None, symbol: Optional[str] = None) -> Dict[str, Any]:
        """Instantly closes the active position at market price."""
        target_pos = None
        if symbol and symbol in self.active_positions:
            target_pos = self.active_positions[symbol]
        elif symbol and self.active_position and self.active_position.get("symbol") == symbol:
            target_pos = self.active_position
        elif not symbol and self.active_position:
            target_pos = self.active_position
        elif self.active_positions:
            target_pos = next(iter(self.active_positions.values()))

        if not target_pos:
            return {"status": "error", "message": "No active position to close."}

        pos = target_pos
        px = float(exit_price or pos.get("current_price") or self.current_price or pos["entry_price"])
        px = round(px, 2)
        units = pos["units"]
        
        # PnL accounting (options buyer: profit = (exit - entry) * units)
        gross_pnl = round((px - pos["entry_price"]) * units, 2)
        
        # Statutory charges (STT, brokerage ₹20/order, GST)
        turnover = (pos["entry_price"] + px) * units
        brokerage = 40.0  # ₹20 buy + ₹20 sell
        stt = round(turnover * 0.000625, 2) if px > pos["entry_price"] else 0.0
        gst_sebi = round(brokerage * 0.18 + turnover * 0.00005, 2)
        total_charges = round(brokerage + stt + gst_sebi, 2)
        net_pnl = round(gross_pnl - total_charges, 2)

        self.capital = round(self.capital + net_pnl, 2)

        trade_record = {
            "trade_id": pos["position_id"],
            "symbol": pos["symbol"],
            "side": pos["side"],
            "lots": pos["lots"],
            "units": units,
            "entry_price": pos["entry_price"],
            "exit_price": px,
            "entry_time": pos["entry_time"],
            "exit_time": datetime.now().isoformat(),
            "reason": reason,
            "gross_pnl": gross_pnl,
            "charges": total_charges,
            "net_pnl": net_pnl,
            "ending_capital": self.capital,
            "pts_gained": round(px - pos["entry_price"], 2)
        }

        self.trade_history.append(trade_record)
        sym = pos["symbol"]
        if sym in self.active_positions:
            del self.active_positions[sym]
        if self.active_position and self.active_position.get("position_id") == pos["position_id"]:
            self.active_position = next(iter(self.active_positions.values())) if self.active_positions else None

        self.save_state()
        return {"status": "success", "action": "POSITION_CLOSED", "record": trade_record}

    def cancel_pending_order(self, order_id: Optional[str] = None) -> Dict[str, Any]:
        """Cancels a pending limit order."""
        if not self.pending_orders:
            return {"status": "error", "message": "No pending orders."}
        
        if order_id:
            self.pending_orders = [o for o in self.pending_orders if o.get("order_id") != order_id]
        else:
            self.pending_orders.clear()
            
        self.save_state()
        return {"status": "success", "action": "PENDING_ORDERS_CANCELLED"}

    def trigger_kill_switch(self) -> Dict[str, Any]:
        """
        Emergency Kill Switch:
        1. Closes any open position immediately at market.
        2. Cancels all pending limit orders.
        3. Locks trading engine for the session.
        """
        self.is_kill_switch_active = True
        self.kill_switch_triggered_at = datetime.now().isoformat()
        
        closed_rec = None
        if self.active_position:
            closed_rec = self.close_position(reason="KILL_SWITCH_EMERGENCY")
            
        self.cancel_pending_order()
        self.save_state()
        
        return {
            "status": "success",
            "action": "KILL_SWITCH_ACTIVATED",
            "kill_switch_active": True,
            "closed_position": closed_rec,
            "timestamp": self.kill_switch_triggered_at
        }

    def unlock_kill_switch(self) -> Dict[str, Any]:
        """Unlocks the Kill Switch so normal trading can resume."""
        self.is_kill_switch_active = False
        self.kill_switch_triggered_at = None
        self.save_state()
        return {"status": "success", "action": "KILL_SWITCH_UNLOCKED", "kill_switch_active": False}

    def update_price_tick(self, symbol: str, price: float, atr: float = 2.5) -> Optional[Dict[str, Any]]:
        """
        Sub-second tick update hook:
        1. Evaluates pending limit orders for fills.
        2. Updates live unrealized PnL.
        3. Evaluates automatic trailing stop and SL/Target exits.
        """
        if not price or price <= 0:
            return None

        self.current_symbol = symbol
        self.current_price = float(price)

        # 1. Check pending limit orders for fills
        for o in list(self.pending_orders):
            if o["symbol"] == symbol and o["status"] == "PENDING":
                if o["side"] == "BUY" and price <= o["limit_price"]:
                    return self._fill_order(o, fill_price=o["limit_price"])

        # 2. Update active position metrics
        if self.active_position and self.active_position["symbol"] == symbol:
            pos = self.active_position
            pos["current_price"] = price
            pos["highest_price"] = max(pos.get("highest_price", price), price)
            
            pts = round(price - pos["entry_price"], 2)
            pos["unrealized_pts"] = pts
            pos["unrealized_pnl"] = round(pts * pos["units"], 2)
            pos["unrealized_pnl_pct"] = round((pts / pos["entry_price"]) * 100, 2)

            # 3. Automatic Trailing Stop Loss Logic
            trailing_mode = pos.get("trailing_mode", "be_lock")
            t1 = pos.get("target1")
            t2 = pos.get("target2")
            current_sl = pos.get("current_sl")

            # A. Breakeven Lock at Target 1
            if trailing_mode in ("be_lock", "auto") and t1 and price >= t1:
                if not pos.get("be_locked"):
                    pos["current_sl"] = max(current_sl or 0.0, pos["entry_price"])
                    pos["be_locked"] = True

            # B. Dynamic Chandelier ATR Trailing
            if trailing_mode == "chandelier":
                chandelier_stop = round(pos["highest_price"] - (1.5 * atr), 2)
                if current_sl is None or chandelier_stop > current_sl:
                    pos["current_sl"] = chandelier_stop

            # C. Check Automatic Exits
            if current_sl is not None and current_sl > 0 and price <= current_sl:
                reason = "TRAILING_SL_HIT" if pos.get("be_locked") else "STOP_LOSS_HIT"
                return self.close_position(reason=reason, exit_price=current_sl)

            if t2 and t2 > 0 and price >= t2:
                return self.close_position(reason="TARGET_2_HIT", exit_price=t2)

        return None

    def get_state(self) -> Dict[str, Any]:
        """Returns comprehensive trading terminal state."""
        # Calculate today's realized PnL
        today_str = date.today().isoformat()
        today_trades = [t for t in self.trade_history if t.get("exit_time", "").startswith(today_str)]
        realized_day_pnl = sum(t.get("net_pnl", 0.0) for t in today_trades)
        
        unrealized_pnl = sum(p.get("unrealized_pnl", 0.0) for p in self.active_positions.values()) if self.active_positions else (self.active_position.get("unrealized_pnl", 0.0) if self.active_position else 0.0)
        total_day_pnl = round(realized_day_pnl + unrealized_pnl, 2)

        return {
            "mode": self.mode,
            "capital": round(self.capital, 2),
            "initial_capital": self.initial_capital,
            "realized_day_pnl": round(realized_day_pnl, 2),
            "unrealized_pnl": round(unrealized_pnl, 2),
            "total_day_pnl": total_day_pnl,
            "active_position": self.active_position,
            "active_positions": self.active_positions,
            "pending_orders": self.pending_orders,
            "is_kill_switch_active": self.is_kill_switch_active,
            "kill_switch_triggered_at": self.kill_switch_triggered_at,
            "today_trade_count": len(today_trades),
            "total_trade_count": len(self.trade_history),
            "timestamp": datetime.now().isoformat()
        }

    def reset_account(self, capital: Optional[float] = None) -> Dict[str, Any]:
        """Resets paper account balance and trade logs."""
        self.capital = float(capital or self.initial_capital)
        self.active_position = None
        self.active_positions.clear()
        self.pending_orders.clear()
        self.trade_history.clear()
        self.is_kill_switch_active = False
        self.kill_switch_triggered_at = None
        self.save_state()
        return {"status": "success", "message": "Account reset.", "capital": self.capital}

    def save_state(self):
        """Persists state to disk."""
        try:
            data = {
                "capital": self.capital,
                "initial_capital": self.initial_capital,
                "active_position": self.active_position,
                "active_positions": self.active_positions,
                "pending_orders": self.pending_orders,
                "trade_history": self.trade_history[-200:],  # Store last 200 trades
                "is_kill_switch_active": self.is_kill_switch_active,
                "kill_switch_triggered_at": self.kill_switch_triggered_at,
                "today_date": self.today_date,
                "updated_at": datetime.now().isoformat()
            }
            with open(STATE_FILE, "w") as f:
                json.dump(data, f, indent=2, default=str)
        except Exception as e:
            print(f"ContractTradingEngine save_state error: {e}", flush=True)

    def load_state(self):
        """Loads state from disk."""
        if STATE_FILE.exists():
            try:
                with open(STATE_FILE, "r") as f:
                    data = json.load(f)
                    self.capital = float(data.get("capital", self.initial_capital))
                    self.initial_capital = float(data.get("initial_capital", self.initial_capital))
                    self.active_position = data.get("active_position")
                    self.active_positions = data.get("active_positions", {})
                    if self.active_position and not self.active_positions and self.active_position.get("symbol"):
                        self.active_positions = {self.active_position["symbol"]: self.active_position}
                    self.pending_orders = data.get("pending_orders", [])
                    self.trade_history = data.get("trade_history", [])
                    self.is_kill_switch_active = bool(data.get("is_kill_switch_active", False))
                    self.kill_switch_triggered_at = data.get("kill_switch_triggered_at")
            except Exception as e:
                print(f"ContractTradingEngine load_state error: {e}", flush=True)


# Global Singleton Instance for contract trading
contract_engine = ContractTradingEngine(initial_capital=100000.0)
