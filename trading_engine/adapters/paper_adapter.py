"""
Paper Trading Execution Adapter.
Simulates real-time order fills, position tracking, and trailing stops against live market ticks.
Integrates institutional order slicing (NSE statutory freeze limits) and multi-strike dispersion.
"""
from typing import Dict, Any, Optional
from datetime import datetime
try:
    from .backtest_adapter import BacktestAdapter
    from ..config import EngineConfig
    from ..execution.order_slicer import OrderSlicer, SlicedExecutionReport
except ImportError:
    from adapters.backtest_adapter import BacktestAdapter
    from config import EngineConfig
    from execution.order_slicer import OrderSlicer, SlicedExecutionReport

class PaperAdapter(BacktestAdapter):
    """
    Inherits real-time position tracking and compounding logic,
    operates against live stream ticks, and executes parent orders
    via the institutional OrderSlicer.
    """
    def __init__(self, cfg: EngineConfig):
        super().__init__(cfg)
        self.slicer = OrderSlicer(cfg)
        print("PaperAdapter: Initialized with simulated capital ₹{:,.2f}".format(self.capital), flush=True)
        print(f"PaperAdapter: Slicer active (Freeze limit: {self.cfg.freeze_lots} lots, Strike dispersion: {getattr(self.cfg, 'strike_dispersion', True)})", flush=True)

    def open_position(self, symbol: str, side: str, price: float, atr: float, timestamp: Any, is_sar: bool = False,
                      custom_target: Optional[float] = None, custom_sl: Optional[float] = None,
                      strategy_name: Optional[str] = None):
        pos = super().open_position(symbol, side, price, atr, timestamp, is_sar, custom_target, custom_sl, strategy_name)
        if pos:
            est_premium = max(80.0, atr * 5.0)
            entry_dt = timestamp if isinstance(timestamp, datetime) else datetime.now()
            report = self.slicer.generate_child_slices(
                n_lots=pos["lots"],
                side=pos["side"],
                base_option_price=est_premium,
                spot_price=price,
                entry_time=entry_dt
            )
            pos["slicer_report"] = report
            pos["entry_vwap"] = report.vwap_price

            log_receipt = self.slicer.format_execution_log(report)
            print(f"\n{log_receipt}")
            strat_label = f" [{pos.get('strategy', 'AI_CORE')}]" if pos.get('strategy') else ""
            print(f"[PAPER POSITION OPENED]{strat_label} BUY {pos['side']} | Filled: {pos['units']:,} units ({pos['lots']:,} lots) | Target: ₹{pos['target_price']:.2f} | SL: ₹{pos['current_sl']:.2f}\n", flush=True)
        return pos

    def update_tick(self, tick: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Processes live sub-second tick for instantaneous Stop-Loss or Target execution."""
        if self.active_position is None:
            return None

        ltp = float(tick.get("last_price", 0.0))
        if ltp <= 0:
            return None

        pos = self.active_position
        side = pos["side"]

        if side == "CE":
            if ltp >= pos["target_price"]:
                return self.close_position("TARGET_HIT", pos["target_price"], tick.get("timestamp") or datetime.now())
            elif ltp <= pos["current_sl"]:
                reason = "LOCKED_PROFIT" if pos["lock_active"] else ("BREAKEVEN" if pos["be_active"] else "STOP_LOSS")
                return self.close_position(reason, pos["current_sl"], tick.get("timestamp") or datetime.now())
        else:  # PE
            if ltp <= pos["target_price"]:
                return self.close_position("TARGET_HIT", pos["target_price"], tick.get("timestamp") or datetime.now())
            elif ltp >= pos["current_sl"]:
                reason = "LOCKED_PROFIT" if pos["lock_active"] else ("BREAKEVEN" if pos["be_active"] else "STOP_LOSS")
                return self.close_position(reason, pos["current_sl"], tick.get("timestamp") or datetime.now())
        return None

    def close_position(self, reason: str, exit_price: float, timestamp: Any):
        pos = self.active_position
        if pos:
            exit_dt = timestamp if isinstance(timestamp, datetime) else datetime.now()
            est_premium = max(80.0, pos["entry_atr"] * 5.0)
            exit_report = self.slicer.generate_child_slices(
                n_lots=pos["lots"],
                side=pos["side"],
                base_option_price=est_premium,
                spot_price=exit_price,
                entry_time=exit_dt
            )
            print(f"\n[INSTITUTIONAL SQUARE-OFF] Slicing {pos['lots']:,} lots exit across {exit_report.total_slices} child orders...")

        rec = super().close_position(reason, exit_price, timestamp)
        print(f"[PAPER EXIT] CLOSED {rec['side']} @ ₹{exit_price:.2f} | Reason: {reason} | PnL: ₹{rec['net_pnl_inr']:+,.2f} | Balance: ₹{rec['ending_capital']:,.2f}\n", flush=True)
        return rec
