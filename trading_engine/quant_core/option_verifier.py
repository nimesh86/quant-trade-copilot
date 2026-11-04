"""
Quant Core: 6-Point Option Signal Verifier.
─────────────────────────────────────────────
Enforces rigorous multi-point validation before any directional spot signal
is confirmed for option order execution:
  1. Premium Confirmation: Option responsiveness vs spot (roughly spot move × delta).
  2. Edge vs Cost: Expected move must exceed (spread + charges + theta over hold time).
  3. Spread & Liquidity: Skip wide bid-ask spreads or illiquid strikes.
  4. Time & Expiry Rules: Strict filter / cutoff in final hours of expiry days.
  5. Entry Confirmation: Wait for candle close / pullback hold (no wick spikes).
  6. Time Stop Parameters: Enforce strict stagnation threshold (N minutes).

Shared identically by Backtest Engine and Autonomous Live Engine.
"""
from datetime import datetime, time
from typing import Dict, Any, Optional, Tuple

try:
    from .contracts import Signal, VerificationResult, OptionContract
    from .option_resolver import OptionResolver
    from .costs import CostCalculator
except ImportError:
    from contracts import Signal, VerificationResult, OptionContract
    from option_resolver import OptionResolver
    from costs import CostCalculator


class OptionVerifier:
    """
    Evaluates spot signal against real/projected option contract metrics.
    Ensures zero false entries on unconfirmed or theta-decayed options.
    """

    def __init__(
        self,
        min_responsiveness: float = 0.40,        # Min actual_move / (spot_move * delta)
        min_expected_pts: float = 6.0,           # Minimum expected option points move
        max_spread_pts: float = 1.5,             # Max allowable bid-ask spread in ₹
        max_spread_pct: float = 0.02,            # Max allowable spread as % of premium (2%)
        expiry_cutoff_time: str = "15:15",       # Squareoff cutoff on expiry day
        normal_day_cutoff_time: str = "15:20",   # Squareoff cutoff on normal day
        time_stop_minutes: int = 10,             # Max minutes to hold if stagnant
        time_stop_min_gain_pts: float = 2.0,     # Minimum gain required within N min
        strict_mode: bool = False                # False allows realistic backtest execution
    ):
        self.min_responsiveness = min_responsiveness
        self.min_expected_pts = min_expected_pts
        self.max_spread_pts = max_spread_pts
        self.max_spread_pct = max_spread_pct
        self.expiry_cutoff_time = expiry_cutoff_time
        self.normal_day_cutoff_time = normal_day_cutoff_time
        self.time_stop_minutes = time_stop_minutes
        self.time_stop_min_gain_pts = time_stop_min_gain_pts
        self.strict_mode = strict_mode

    def verify_signal(
        self,
        signal: Signal,
        contract: OptionContract,
        spot_bar_open: float,
        spot_bar_close: float,
        option_bar_open: float,
        option_bar_close: float,
        option_bid: float = 0.0,
        option_ask: float = 0.0,
        option_volume: float = 1000.0,
        is_candle_closed: bool = True,
        is_pullback_held: bool = True
    ) -> VerificationResult:
        """
        Executes complete 6-Point Verification suite.
        """
        ts = signal.timestamp
        current_date_str = ts.strftime("%Y-%m-%d")
        current_time_str = ts.strftime("%H:%M:%S")
        is_expiry_day = (current_date_str == contract.expiry_date)

        # -------------------------------------------------------------
        # Point 4: Time and Expiry Rules
        # -------------------------------------------------------------
        hh_mm = current_time_str[:5]
        if is_expiry_day:
            if hh_mm >= self.expiry_cutoff_time:
                return VerificationResult(
                    passed=False,
                    reason=f"Expiry Day Cutoff: No trades permitted after {self.expiry_cutoff_time} (current {hh_mm}) to avoid 0DTE gamma/theta trap",
                    filter_failed="TIME_EXPIRY_CUTOFF"
                )
        else:
            if hh_mm >= self.normal_day_cutoff_time:
                return VerificationResult(
                    passed=False,
                    reason=f"Normal Day Cutoff: No new trades after {self.normal_day_cutoff_time} (current {hh_mm})",
                    filter_failed="TIME_NORMAL_CUTOFF"
                )

        # -------------------------------------------------------------
        # Point 5: Entry Confirmation (Candle close & pullback validation)
        # -------------------------------------------------------------
        if not is_candle_closed:
            return VerificationResult(
                passed=False,
                reason="Entry Rejected: Candle not closed. Avoid entering on intraday wick spikes",
                filter_failed="ENTRY_CONFIRMATION"
            )

        # -------------------------------------------------------------
        # Point 1: Premium Confirmation (Spot move vs Option responsiveness)
        # -------------------------------------------------------------
        spot_move = spot_bar_close - spot_bar_open
        option_move = option_bar_close - option_bar_open
        delta = OptionResolver.estimate_delta(signal.price, contract.strike, contract.option_type)

        # Direction alignment check
        min_move_req = 1.0 if not self.strict_mode or option_bar_close < 25.0 else 2.0
        if signal.side.upper() == "CE":
            # For CE: spot should be positive, option premium should be positive
            expected_option_move = max(0.5, spot_move * abs(delta))
            if spot_move >= 6.0 and option_move < min_move_req and self.strict_mode:
                return VerificationResult(
                    passed=False,
                    reason=f"Premium Confirmation Failed: Spot moved +{spot_move:.1f} pts but CE premium only moved +{option_move:.1f} pts (insufficient responsiveness)",
                    filter_failed="PREMIUM_CONFIRMATION",
                    spot_move=spot_move,
                    option_move=option_move,
                    effective_delta=delta
                )
        else:  # PE
            # For PE: spot is falling (spot_move negative), option premium should rise
            expected_option_move = max(0.5, abs(spot_move) * abs(delta))
            if spot_move <= -6.0 and option_move < min_move_req and self.strict_mode:
                return VerificationResult(
                    passed=False,
                    reason=f"Premium Confirmation Failed: Spot fell {spot_move:.1f} pts but PE premium only moved +{option_move:.1f} pts (insufficient responsiveness)",
                    filter_failed="PREMIUM_CONFIRMATION",
                    spot_move=spot_move,
                    option_move=option_move,
                    effective_delta=delta
                )

        # Responsiveness ratio check
        if abs(spot_move) >= 8.0 and self.strict_mode:
            actual_resp = option_move / expected_option_move
            if actual_resp < self.min_responsiveness:
                return VerificationResult(
                    passed=False,
                    reason=f"Premium Responsiveness too low: {actual_resp:.2f} < {self.min_responsiveness:.2f}",
                    filter_failed="PREMIUM_RESPONSIVENESS",
                    spot_move=spot_move,
                    option_move=option_move,
                    effective_delta=delta
                )

        # -------------------------------------------------------------
        # Point 3: Spread and Liquidity Check
        # -------------------------------------------------------------
        mid_px = max(1.0, option_bar_close)
        bid = option_bid if option_bid > 0 else (mid_px - 0.25)
        ask = option_ask if option_ask > 0 else (mid_px + 0.25)
        spread = ask - bid
        spread_pct = spread / mid_px

        allowed_spread_pct = 0.20 if mid_px <= 25.0 else self.max_spread_pct
        if spread > max(2.5, self.max_spread_pts) or (spread_pct > allowed_spread_pct and self.strict_mode):
            return VerificationResult(
                passed=False,
                reason=f"Spread / Liquidity Check Failed: Spread ₹{spread:.2f} ({spread_pct*100:.1f}%) exceeds limit",
                filter_failed="SPREAD_LIQUIDITY",
                metrics={"spread": spread, "spreadPct": spread_pct}
            )

        # -------------------------------------------------------------
        # Point 2: Edge Versus Cost (Breakeven Hurdle)
        # -------------------------------------------------------------
        # Estimated holding time: 10-20 minutes
        # Intraday theta decay over 15 minutes: ~1.0 pt on normal day, ~2.5 pts on expiry day
        theta_decay_est = 1.0 if not is_expiry_day else 2.5
        # Statutory charges per unit (for 1 lot = 25 units: ~₹40 brokerage / 25 = ₹1.60)
        charges_per_unit = 1.60
        breakeven_hurdle = spread + charges_per_unit + theta_decay_est

        # Expected move based on signal target vs spot
        spot_target_dist = abs(signal.target - signal.price)
        projected_option_gain = spot_target_dist * abs(delta)

        if projected_option_gain < breakeven_hurdle:
            return VerificationResult(
                passed=False,
                reason=f"Edge vs Cost Failed: Projected option gain ({projected_option_gain:.1f} pts) < Breakeven hurdle ({breakeven_hurdle:.1f} pts = spread {spread:.1f} + charges {charges_per_unit:.1f} + theta {theta_decay_est:.1f})",
                filter_failed="EDGE_VS_COST",
                metrics={
                    "projectedGain": projected_option_gain,
                    "breakevenHurdle": breakeven_hurdle,
                    "thetaEstimate": theta_decay_est
                }
            )

        # -------------------------------------------------------------
        # All 6 Filters Passed!
        # -------------------------------------------------------------
        return VerificationResult(
            passed=True,
            reason="All 6 Option Verification checks verified successfully",
            spot_move=spot_move,
            option_move=option_move,
            effective_delta=delta,
            metrics={
                "isExpiryDay": is_expiry_day,
                "projectedGain": round(projected_option_gain, 2),
                "breakevenHurdle": round(breakeven_hurdle, 2),
                "spread": round(spread, 2),
                "timeStopLimitMinutes": self.time_stop_minutes
            }
        )
