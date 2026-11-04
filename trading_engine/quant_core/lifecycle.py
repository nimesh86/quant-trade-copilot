"""
Quant Core: Stateful Trade Lifecycle Manager for Option Premiums.
─────────────────────────────────────────────────────────────────
Manages active Option Buying positions, stepped trailing stop loss ladders,
intraday square-offs (15:15 IST), Time-Stop theta decay exits (N minutes),
and institutional distribution smart exits.

Shared identically by Backtest Engine (Layer B) and Autonomous Live Engine (Layer C).
"""
from datetime import datetime, time
from typing import Dict, Any, Optional, Tuple
import math

try:
    from .contracts import Position, OrderSlice, StatutoryCharges, TradeRecord, ExitReason, OrderAction
    from .costs import CostCalculator
except ImportError:
    from contracts import Position, OrderSlice, StatutoryCharges, TradeRecord, ExitReason, OrderAction
    from costs import CostCalculator


class TradeLifecycleManager:
    """
    Standardized Trade Lifecycle & Position Monitor for Option Premiums.
    Ensures 100% identical execution logic, trailing stop ladders, and PnL accounting
    across historical backtests and live market order execution.
    """

    def __init__(
        self,
        be_trigger_pts: float = 10.0,        # Option points gain to trigger Break-Even
        be_lock_pts: float = 2.0,            # Points locked at BE (Locks +2.0 pts profit, covers charges)
        profit_lock_trigger_pts: float = 16.0, # Points gain to trigger Stage 2 profit lock
        profit_lock_pts: float = 10.0,       # Points locked at Stage 2 (Locks +10.0 pts runner protection)
        peak_trail_dist_pts: float = 8.0,    # Trailing distance from peak premium
        auto_square_off_time: str = "15:15", # NSE Intraday auto square-off
        time_stop_minutes: int = 0,          # 0 = disabled (prevents premature exit of consolidating breakout trades)
        time_stop_min_gain: float = 2.0,     # Required gain to prevent time stop exit
        stagnation_cutoff_mins: int = 6,     # Quick exit if trade has no momentum and drops below entry
        stagnation_min_gain: float = 1.0     # Minimum peak gain required to avoid stagnation exit
    ):
        self.be_trigger_pts = be_trigger_pts
        self.be_lock_pts = be_lock_pts
        self.profit_lock_trigger_pts = profit_lock_trigger_pts
        self.profit_lock_pts = profit_lock_pts
        self.peak_trail_dist_pts = peak_trail_dist_pts
        self.auto_square_off_time = auto_square_off_time
        self.time_stop_minutes = time_stop_minutes
        self.time_stop_min_gain = time_stop_min_gain
        self.stagnation_cutoff_mins = stagnation_cutoff_mins
        self.stagnation_min_gain = stagnation_min_gain

    def _parse_time(self, ts: Any) -> Tuple[str, str, str]:
        """Returns (date_str, day_str, time_str) from timestamp."""
        if isinstance(ts, str):
            try:
                dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            except Exception:
                try:
                    dt = datetime.strptime(ts[:19], "%Y-%m-%d %H:%M:%S")
                except Exception:
                    dt = datetime.now()
        elif isinstance(ts, datetime):
            dt = ts
        else:
            dt = datetime.now()

        date_str = dt.strftime("%Y-%m-%d")
        day_str = dt.strftime("%A")
        time_str = dt.strftime("%H:%M:%S")
        return date_str, day_str, time_str

    def evaluate_bar(
        self,
        position: Position,
        opt_bar_high: float,
        opt_bar_low: float,
        opt_bar_close: float,
        timestamp: Any,
        smart_exit_reason: Optional[str] = None
    ) -> Optional[TradeRecord]:
        """
        Evaluates active Option position against the current bar's option premium prices.
        Checks:
          1. Auto Square-Off Time (15:15 IST)
          2. External Smart Exit (Institutional Distribution / POC Break)
          3. Time-Stop Theta Decay (Exit if stagnant after N minutes)
          4. Trailing Stop Ladder adjustments
          5. Target hit
          6. Stop Loss hit
        
        Returns closed TradeRecord if exited, otherwise None.
        """
        date_str, day_str, time_str = self._parse_time(timestamp)
        hh_mm = time_str[:5]

        # Increment minutes held
        position.minutes_held += 1

        # 1. Intraday Auto Square-Off at 15:15 IST (Strict risk cutoff on Expiry Day)
        if hh_mm >= self.auto_square_off_time:
            return self.close_position(
                position=position,
                exit_price=opt_bar_close,
                exit_reason="AUTO_SQUARE_OFF_15_15",
                timestamp=timestamp
            )

        # 2. External Smart Exit triggered (e.g. SMART_EXIT_DISTRIBUTION)
        if smart_exit_reason:
            return self.close_position(
                position=position,
                exit_price=opt_bar_close,
                exit_reason=smart_exit_reason,
                timestamp=timestamp
            )

        # 3. Update peak premium reached
        position.peak_premium = max(position.peak_premium, opt_bar_high)
        entry_px = position.entry_premium
        cur_sl = position.current_sl_premium
        target_px = position.target_premium

        # 4. Dynamic Trailing Stop Ladder in Option Premium Points
        gain_from_entry = position.peak_premium - entry_px

        # Scale trailing thresholds dynamically for lower-premium options (<= 40)
        if entry_px <= 40.0:
            be_thresh = max(2.0, round(entry_px * 0.18, 2))
            be_lock = max(0.5, round(entry_px * 0.08, 2))
            p2_thresh = max(3.0, round(entry_px * 0.25, 2))
            p2_lock = max(1.5, round(entry_px * 0.16, 2))
            peak_trail_dist = max(1.5, round(entry_px * 0.12, 2))
            stage3_thresh = p2_thresh + 3.0
        else:
            be_thresh = self.be_trigger_pts
            be_lock = self.be_lock_pts
            p2_thresh = self.profit_lock_trigger_pts
            p2_lock = self.profit_lock_pts
            peak_trail_dist = self.peak_trail_dist_pts
            stage3_thresh = self.profit_lock_trigger_pts + 10.0

        # Stage 1: Break-Even Lock
        if position.trailing_stage < 1 and gain_from_entry >= be_thresh:
            position.trailing_stage = 1
            position.current_sl_premium = max(position.current_sl_premium, entry_px + be_lock)

        # Stage 2: Profit Lock
        if position.trailing_stage < 2 and gain_from_entry >= p2_thresh:
            position.trailing_stage = 2
            position.current_sl_premium = max(position.current_sl_premium, entry_px + p2_lock)

        # Stage 3: Peak Trailing
        if gain_from_entry >= stage3_thresh:
            position.trailing_stage = 3
            trailing_level = position.peak_premium - peak_trail_dist
            position.current_sl_premium = max(position.current_sl_premium, trailing_level)

        # 5. Check Target Hit (Prioritized when bar high touches target)
        if opt_bar_high >= target_px:
            return self.close_position(
                position=position,
                exit_price=target_px,
                exit_reason=ExitReason.TARGET.value,
                timestamp=timestamp
            )

        # 6. Check Stop Loss Hit
        if opt_bar_low <= position.current_sl_premium:
            reason = (
                ExitReason.TRAILING_SL.value
                if position.trailing_stage > 0
                else ExitReason.STOP_LOSS.value
            )
            return self.close_position(
                position=position,
                exit_price=position.current_sl_premium,
                exit_reason=reason,
                timestamp=timestamp
            )

        # 7. Check Time Stop & Stagnation Cutoff
        favorable_move = position.peak_premium - entry_px
        # Quick stagnation cutoff: If held >= stagnation_cutoff_mins with no momentum (< min_gain) and slipping below entry
        if self.stagnation_cutoff_mins > 0 and position.minutes_held >= self.stagnation_cutoff_mins and favorable_move < self.stagnation_min_gain and opt_bar_close < entry_px:
            return self.close_position(
                position=position,
                exit_price=opt_bar_close,
                exit_reason=f"STAGNATION_EXIT (Held {position.minutes_held}m with no momentum, Gain {favorable_move:.1f}pts)",
                timestamp=timestamp
            )

        if self.time_stop_minutes > 0 and position.minutes_held >= self.time_stop_minutes:
            if favorable_move < self.time_stop_min_gain:
                return self.close_position(
                    position=position,
                    exit_price=opt_bar_close,
                    exit_reason=f"{ExitReason.TIME_STOP.value} (Stagnant {position.minutes_held}m, Gain {favorable_move:.1f}pts)",
                    timestamp=timestamp
                )

        return None

    def close_position(
        self,
        position: Position,
        exit_price: float,
        exit_reason: str,
        timestamp: Any
    ) -> TradeRecord:
        """
        Closes position, computes Indian regulatory charges on premium, and generates TradeRecord.
        """
        date_str, day_str, time_str = self._parse_time(timestamp)
        exit_px = max(0.05, float(exit_price))
        entry_px = position.entry_premium
        units = position.units

        # PnL in Option Premium terms
        points = exit_px - entry_px
        gross_pnl = points * units

        # Compute statutory charges
        charges = CostCalculator.calculate_charges(
            entry_price=entry_px,
            exit_price=exit_px,
            units=units,
            n_slices=max(1, len(position.slices)),
            is_option=True
        )

        net_pnl = gross_pnl - charges.total
        invested_capital = entry_px * units
        roi_pct = (net_pnl / invested_capital * 100.0) if invested_capital > 0 else 0.0

        return TradeRecord(
            id=position.id,
            date=date_str,
            day=day_str,
            symbol=position.underlying,
            contract_symbol=position.contract_symbol,
            side="BULLISH" if position.option_type == "CE" else "BEARISH",
            action=position.action.value,
            option_type=position.option_type,
            strike=position.strike,
            indicator=position.strategy_name,
            probability=str(position.metadata.get("probability", "80%")),
            entry_time=position.entry_time.strftime("%H:%M:%S") if isinstance(position.entry_time, datetime) else str(position.entry_time)[11:19],
            entry_rate=round(entry_px, 2),
            exit_time=time_str,
            exit_rate=round(exit_px, 2),
            exit_reason=exit_reason,
            points=round(points, 2),
            gross_pnl=round(gross_pnl, 2),
            charges=round(charges.total, 2),
            net_pnl=round(net_pnl, 2),
            roi_pct=round(roi_pct, 2),
            lots=position.lots,
            units=units,
            charges_breakdown=charges,
            metadata=position.metadata
        )
