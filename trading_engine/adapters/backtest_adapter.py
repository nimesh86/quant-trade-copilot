"""
Backtest Execution Adapter.
Simulates high-precision order execution, slippage, brokerage,
dynamic multi-step trailing ladder, compounding lot sizing, and month-wise PnL accounting.
"""
from typing import Dict, Any, Optional, List
import numpy as np
import pandas as pd
try:
    from .base import BaseAdapter
    from ..config import EngineConfig
except ImportError:
    from adapters.base import BaseAdapter
    from config import EngineConfig

class BacktestAdapter(BaseAdapter):
    def __init__(self, cfg: EngineConfig):
        self.cfg = cfg
        self.capital = cfg.initial_capital
        self.active_position = None
        self.trade_history: List[Dict[str, Any]] = []
        self.daily_trade_count = 0
        self.current_day = None
        self.consecutive_losses = 0
        self.last_loss_exit_time = None

    def open_position(self, symbol: str, side: str, price: float, atr: float, timestamp: Any, is_sar: bool = False,
                      custom_target: Optional[float] = None, custom_sl: Optional[float] = None,
                      strategy_name: Optional[str] = None) -> Optional[Dict[str, Any]]:
        day = timestamp.date()
        if self.current_day != day:
            self.current_day = day
            self.daily_trade_count = 0
            self.consecutive_losses = 0
            self.last_loss_exit_time = None

        # Standard daily limit applies to generic AI trades, but institutional strategy setups bypass it
        if not strategy_name and self.daily_trade_count >= self.cfg.max_trades_per_day:
            return None

        # 15-minute Cooldown after 2 consecutive losses (applies to generic trades)
        if not strategy_name and self.consecutive_losses >= getattr(self.cfg, "consecutive_losses_for_cooldown", 2) and self.last_loss_exit_time is not None:
            try:
                elapsed_sec = (timestamp - self.last_loss_exit_time).total_seconds()
                cooldown_sec = getattr(self.cfg, "loss_cooldown_mins", 15) * 60
                if 0 <= elapsed_sec < cooldown_sec:
                    return None
            except Exception:
                pass

        # Position Sizing: Compounding 7% risk
        # Stop distance in Nifty points = custom SL distance or stop_atr_mult * ATR
        if custom_sl is not None:
            stop_dist_pts = max(5.0, abs(price - custom_sl))
        else:
            stop_dist_pts = self.cfg.stop_atr_mult * atr

        # Realistic Option Greeks dynamics:
        # ATM option premium is typically ~ 5x ATR (approx ₹100-120)
        est_premium = max(80.0, atr * 5.0)
        # On adverse stop out, average delta shrinks to ~0.35
        opt_sl_pts = stop_dist_pts * 0.35 + (self.cfg.slippage_per_side * 2)
        risk_per_lot = opt_sl_pts * self.cfg.lot_size

        risk_budget = self.capital * self.cfg.risk_per_trade_pct
        max_lots_by_risk = int(risk_budget // risk_per_lot) if risk_per_lot > 0 else 0
        max_lots_by_capital = int(self.capital // (est_premium * self.cfg.lot_size))

        # Iceberg Order Slicing & Statutory Freeze Limit:
        # NSE freeze limit per order is 30 lots (1,950 units)
        # Zerodha Iceberg limit is 10 legs -> Statutory ceiling = 300 lots (19,500 units)
        n_lots = max(1, min(max_lots_by_risk, max_lots_by_capital, self.cfg.max_lots))

        # Generate Iceberg slice legs
        slices = []
        rem = n_lots
        while rem > 0:
            take = min(rem, self.cfg.freeze_lots)
            slices.append(take)
            rem -= take

        n_slices = len(slices)
        units = n_lots * self.cfg.lot_size

        # Apply realistic Iceberg market depth slippage:
        # Base ₹0.50 + additional impact per slice leg
        leg_slip = self.cfg.slippage_per_side + (n_slices - 1) * self.cfg.depth_slip_per_slice
        exec_price = price + leg_slip if side == "CE" else price - leg_slip

        # Target and Stop Loss resolution
        init_sl = custom_sl if custom_sl is not None else (exec_price - stop_dist_pts if side == "CE" else exec_price + stop_dist_pts)
        target_px = custom_target if custom_target is not None else (exec_price + self.cfg.target_atr_mult * atr if side == "CE" else exec_price - self.cfg.target_atr_mult * atr)

        pos = {
            "symbol": symbol,
            "side": side,
            "entry_time": timestamp,
            "entry_price": exec_price,
            "entry_atr": atr,
            "units": units,
            "lots": n_lots,
            "slices": slices,
            "n_slices": n_slices,
            "leg_slip": leg_slip,
            "initial_sl": init_sl,
            "current_sl": init_sl,
            "target_price": target_px,
            "be_trigger": exec_price + self.cfg.be_trigger_mult * atr if side == "CE" else exec_price - self.cfg.be_trigger_mult * atr,
            "lock_trigger": exec_price + self.cfg.profit_lock_trigger * atr if side == "CE" else exec_price - self.cfg.profit_lock_trigger * atr,
            "be_active": False,
            "lock_active": False,
            "bars_held": 0,
            "is_sar": is_sar,
            "strategy": strategy_name or "AI_CORE"
        }

        self.active_position = pos
        self.daily_trade_count += 1
        return pos

    def update_position(self, bar: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        if self.active_position is None:
            return None

        pos = self.active_position
        pos["bars_held"] += 1
        h, l = bar["high"], bar["low"]
        side = pos["side"]
        entry_px = pos["entry_price"]
        atr = pos["entry_atr"]

        if side == "CE":
            # Multi-step ladder if enabled
            if getattr(self.cfg, "use_trailing_ladder", False):
                if not pos["be_active"] and h >= pos["be_trigger"]:
                    pos["be_active"] = True
                    pos["current_sl"] = max(pos["current_sl"], entry_px + self.cfg.be_lock_amt_mult * atr)

                if not pos["lock_active"] and h >= pos["lock_trigger"]:
                    pos["lock_active"] = True
                    pos["current_sl"] = max(pos["current_sl"], entry_px + self.cfg.profit_lock_amt * atr)

            # Target Check
            if h >= pos["target_price"] and l > pos["current_sl"]:
                return self.close_position("TARGET_HIT", pos["target_price"], bar["timestamp"])
            # Stop Check
            elif l <= pos["current_sl"]:
                reason = "LOCKED_PROFIT" if pos["lock_active"] else ("BREAKEVEN" if pos["be_active"] else "STOP_LOSS")
                return self.close_position(reason, pos["current_sl"], bar["timestamp"])

        else: # PE
            if getattr(self.cfg, "use_trailing_ladder", False):
                if not pos["be_active"] and l <= pos["be_trigger"]:
                    pos["be_active"] = True
                    pos["current_sl"] = min(pos["current_sl"], entry_px - self.cfg.be_lock_amt_mult * atr)

                if not pos["lock_active"] and l <= pos["lock_trigger"]:
                    pos["lock_active"] = True
                    pos["current_sl"] = min(pos["current_sl"], entry_px - self.cfg.profit_lock_amt * atr)

            if l <= pos["target_price"] and h < pos["current_sl"]:
                return self.close_position("TARGET_HIT", pos["target_price"], bar["timestamp"])
            elif h >= pos["current_sl"]:
                reason = "LOCKED_PROFIT" if pos["lock_active"] else ("BREAKEVEN" if pos["be_active"] else "STOP_LOSS")
                return self.close_position(reason, pos["current_sl"], bar["timestamp"])

        return None

    def close_position(self, reason: str, exit_price: float, timestamp: Any) -> Dict[str, Any]:
        pos = self.active_position
        n_slices = pos.get("n_slices", 1)
        leg_slip = pos.get("leg_slip", self.cfg.slippage_per_side)

        # Apply exit slippage matching Iceberg depth
        exec_exit = exit_price - leg_slip if pos["side"] == "CE" else exit_price + leg_slip
        gross_pts = (exec_exit - pos["entry_price"]) if pos["side"] == "CE" else (pos["entry_price"] - exec_exit)

        # Realistic Option Delta Asymmetry:
        # Favorable trend moves expand delta to ~0.65
        # Adverse moves shrink delta to ~0.35
        # Breakeven / partial locks average delta ~0.50
        roundtrip_slippage = leg_slip * 2.0
        if reason == "TARGET_HIT":
            option_pts = (abs(gross_pts) * 0.65) - roundtrip_slippage
            gross_pnl = option_pts * pos["units"]
        elif reason in ["BREAKEVEN", "LOCKED_PROFIT"]:
            option_pts = (gross_pts * 0.50) - roundtrip_slippage
            gross_pnl = option_pts * pos["units"]
        else: # STOP_LOSS
            option_pts = -(abs(gross_pts) * 0.35) - roundtrip_slippage
            gross_pnl = option_pts * pos["units"]

        # Exact Zerodha Multi-Leg Iceberg Cost Model:
        # 1. Base Brokerage: ₹20 entry + ₹20 exit per Iceberg leg
        brokerage = (self.cfg.brokerage_per_order * 2) * n_slices

        # 2. Premium Turnovers (est_premium * units)
        est_prem = max(80.0, pos["entry_atr"] * 5.0)
        buy_turnover = est_prem * pos["units"]
        sell_turnover = max(0.05, est_prem + option_pts) * pos["units"]
        total_turnover = buy_turnover + sell_turnover

        # 3. NSE Exchange Txn Fee: 0.03553%
        exchange_fee = 0.0003553 * total_turnover
        # 4. GST: 18% on (Brokerage + Exchange Fee)
        gst = 0.18 * (brokerage + exchange_fee)
        # 5. STT: 0.05% on sell turnover
        stt_sell = 0.0005 * sell_turnover
        # 6. Stamp duty: 0.003% on buy turnover
        stamp_duty = 0.00003 * buy_turnover
        # 7. SEBI turnover fee: ₹10 per crore
        sebi_fee = (total_turnover / 10000000.0) * 10.0

        total_friction = brokerage + exchange_fee + gst + stt_sell + stamp_duty + sebi_fee
        net_pnl = gross_pnl - total_friction

        # Update capital
        self.capital += net_pnl

        # Update consecutive loss tracking for cooldown
        if net_pnl < 0 or reason in ["STOP_LOSS"]:
            self.consecutive_losses += 1
            self.last_loss_exit_time = timestamp
        elif net_pnl > 0 or reason in ["TARGET_HIT", "LOCKED_PROFIT"]:
            self.consecutive_losses = 0

        record = {
            "entry_time": pos["entry_time"],
            "exit_time": timestamp,
            "month": pos["entry_time"].strftime("%Y-%m"),
            "side": pos["side"],
            "units": pos["units"],
            "lots": pos["lots"],
            "slices": pos.get("slices", []),
            "n_slices": n_slices,
            "brokerage": brokerage,
            "total_friction": total_friction,
            "entry_price": pos["entry_price"],
            "exit_price": exec_exit,
            "gross_pts": gross_pts,
            "option_pts": option_pts,
            "net_pnl_inr": net_pnl,
            "ending_capital": self.capital,
            "exit_reason": reason,
            "is_win": bool(net_pnl > 0),
            "bars_held": pos["bars_held"],
            "is_sar": pos["is_sar"],
            "strategy": pos.get("strategy", "AI_CORE")
        }

        self.trade_history.append(record)
        self.active_position = None
        return record

    def get_portfolio_summary(self) -> Dict[str, Any]:
        if not self.trade_history:
            return {"total_trades": 0, "final_capital": self.capital}
        
        df = pd.DataFrame(self.trade_history)
        total_trades = len(df)
        wins = df["is_win"].sum()
        losses = total_trades - wins
        wr = (wins / total_trades) * 100.0 if total_trades > 0 else 0.0

        # Calculate Trade Series (Setup Sequence: Primary Trade -> SAR Recovery)
        # An initial trade setup is a Series. It is a WIN if either:
        # A) The primary trade hit Target directly, OR
        # B) The primary trade hit SL and the immediate SAR flip hit Target!
        if "is_sar" not in df.columns:
            df["is_sar"] = False

        initial_trades = df[df["is_sar"] == False]
        total_series = len(initial_trades)
        direct_wins = initial_trades["is_win"].sum() if "is_win" in initial_trades.columns else 0
        sar_wins = df[(df["is_sar"] == True) & (df["is_win"] == True)].shape[0] if "is_win" in df.columns else 0
        series_wins = direct_wins + sar_wins
        series_wr = (series_wins / total_series) * 100.0 if total_series > 0 else 0.0

        return {
            "total_trades": total_trades,
            "wins": wins,
            "losses": losses,
            "win_rate": wr,
            "total_series": total_series,
            "series_wins": series_wins,
            "series_losses": total_series - series_wins,
            "series_win_rate": series_wr,
            "initial_capital": self.cfg.initial_capital,
            "final_capital": self.capital,
            "net_profit": self.capital - self.cfg.initial_capital,
            "return_pct": ((self.capital - self.cfg.initial_capital) / self.cfg.initial_capital) * 100.0,
            "trades_df": df
        }
