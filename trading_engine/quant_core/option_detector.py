"""
Smart Option Detector: Dynamic ATM Strike & Contract Tracker
Monitors Spot movement and periodically (e.g. every 5 or 10 minutes) snaps to the
current At-The-Money (ATM) Call (CE) and Put (PE) option contracts.
"""
from datetime import datetime
from typing import Optional, Tuple
from trading_engine.quant_core.contracts import OptionContract
from trading_engine.quant_core.option_resolver import OptionResolver

DEFAULT_ATM_INTERVAL_MINUTES: int = 5


class SmartOptionDetector:
    """
    Tracks and dynamically switches ATM CE and PE contracts at configurable minute intervals
    or immediately after a closed trade.
    """

    def __init__(
        self,
        interval_minutes: int = DEFAULT_ATM_INTERVAL_MINUTES,
        underlying: str = "NIFTY"
    ):
        self.interval_minutes = max(1, int(interval_minutes))
        self.underlying = underlying.upper().strip()
        self.current_atm_strike: Optional[float] = None
        self.current_ce_contract: Optional[OptionContract] = None
        self.current_pe_contract: Optional[OptionContract] = None
        self.last_evaluated_minute: Optional[int] = None

    def evaluate(
        self,
        spot_price: float,
        current_dt: datetime,
        force_redetect: bool = False
    ) -> Tuple[bool, Optional[OptionContract], Optional[OptionContract]]:
        """
        Evaluates current spot price to resolve the active ATM CE and PE contracts.

        Returns:
            (strike_changed: bool, current_ce_contract, current_pe_contract)
        """
        cur_min = current_dt.minute if hasattr(current_dt, "minute") else 0
        should_check = (
            force_redetect
            or self.current_atm_strike is None
            or (cur_min % self.interval_minutes == 0 and cur_min != self.last_evaluated_minute)
        )

        if not should_check:
            return False, self.current_ce_contract, self.current_pe_contract

        self.last_evaluated_minute = cur_min
        new_atm_strike = OptionResolver.resolve_atm_strike(spot_price, self.underlying)

        strike_changed = (new_atm_strike != self.current_atm_strike)
        if strike_changed or self.current_ce_contract is None:
            self.current_atm_strike = new_atm_strike
            self.current_ce_contract = OptionResolver.resolve_contract(
                spot_price=new_atm_strike,
                option_type="CE",
                current_dt=current_dt,
                underlying=self.underlying,
                avoid_otm=False
            )
            self.current_pe_contract = OptionResolver.resolve_contract(
                spot_price=new_atm_strike,
                option_type="PE",
                current_dt=current_dt,
                underlying=self.underlying,
                avoid_otm=False
            )
            return True, self.current_ce_contract, self.current_pe_contract

        return False, self.current_ce_contract, self.current_pe_contract

    def reset(self):
        """Resets detector state between daily sessions."""
        self.current_atm_strike = None
        self.current_ce_contract = None
        self.current_pe_contract = None
        self.last_evaluated_minute = None

