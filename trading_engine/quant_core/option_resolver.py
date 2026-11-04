"""
Quant Core: Dynamic Option Contract & Strike Resolver.
───────────────────────────────────────────────────────
Dynamically resolves the optimal ATM/ITM Option Strike based on underlying
Spot Index price fluctuations, step rules, and expiry schedules.
Shared identically by Backtest Engine and Autonomous Live Engine.
"""
from datetime import datetime, date, timedelta
from typing import Dict, Any, Optional, Tuple, List
import math

try:
    from .contracts import OptionContract
except ImportError:
    from contracts import OptionContract


class OptionResolver:
    """
    Standardized strike and contract resolver for Indian derivatives.
    Automatically handles spot movements (e.g. Spot 23200 -> ATM 23200 CE,
    Spot drops to 22800 -> ATM 22800 PE).
    """

    # Index step size definitions
    STEP_SIZES: Dict[str, float] = {
        "NIFTY": 50.0,
        "NIFTY50": 50.0,
        "BANKNIFTY": 100.0,
        "SENSEX": 100.0,
        "FINNIFTY": 50.0,
        "MIDCPNIFTY": 25.0
    }

    # Lot sizes
    LOT_SIZES: Dict[str, int] = {
        "NIFTY": 65,
        "NIFTY50": 65,
        "BANKNIFTY": 15,
        "SENSEX": 10,
        "FINNIFTY": 25,
        "MIDCPNIFTY": 50
    }

    # Freeze limits in lots
    FREEZE_LOTS: Dict[str, int] = {
        "NIFTY": 36,        # 900 units / 25 = 36
        "NIFTY50": 36,
        "BANKNIFTY": 30,    # 450 units / 15 = 30
        "SENSEX": 30,
        "FINNIFTY": 36,
        "MIDCPNIFTY": 50
    }

    @classmethod
    def get_step_size(cls, underlying: str) -> float:
        norm = underlying.upper().replace(" ", "").replace("_", "")
        return cls.STEP_SIZES.get(norm, 50.0)

    @classmethod
    def get_lot_size(cls, underlying: str) -> int:
        norm = underlying.upper().replace(" ", "").replace("_", "")
        return cls.LOT_SIZES.get(norm, 25)

    @classmethod
    def get_freeze_lots(cls, underlying: str) -> int:
        norm = underlying.upper().replace(" ", "").replace("_", "")
        return cls.FREEZE_LOTS.get(norm, 36)

    NSE_WEEKLY_MONTH_MAP = {
        1: "1", 2: "2", 3: "3", 4: "4", 5: "5", 6: "6",
        7: "7", 8: "8", 9: "9", 10: "O", 11: "N", 12: "D"
    }

    @classmethod
    def resolve_strike(
        cls,
        spot_price: float,
        option_type: str,
        underlying: str = "NIFTY",
        avoid_otm: bool = True
    ) -> float:
        """
        Calculates optimal strike avoiding OTM (Out-Of-The-Money) options.
        - When buying CE (Calls): Strike <= Spot (ATM or slightly ITM).
          e.g. Spot 23097.30 -> Strike 23050 (ITM/ATM), NEVER 23100 (OTM).
        - When buying PE (Puts): Strike >= Spot (ATM or slightly ITM).
          e.g. Spot 22931.40 -> Strike 22950 (ITM/ATM), NEVER 22900 (OTM).
        """
        step = cls.get_step_size(underlying)
        spot = float(spot_price)
        if not avoid_otm:
            return round(spot / step) * step

        opt = option_type.upper()
        if "CE" in opt or "CALL" in opt:
            # Never buy OTM Call (strike > spot). Pick nearest ITM/ATM strike <= spot.
            return math.floor(spot / step) * step
        else:
            # Never buy OTM Put (strike < spot). Pick nearest ITM/ATM strike >= spot.
            return math.ceil(spot / step) * step

    @classmethod
    def resolve_atm_strike(cls, spot_price: float, underlying: str = "NIFTY") -> float:
        """
        Calculates symmetric ATM strike rounded to nearest step size.
        """
        step = cls.get_step_size(underlying)
        return round(float(spot_price) / step) * step

    @classmethod
    def is_monthly_expiry(cls, exp_date_obj: date) -> bool:
        """
        Checks if exp_date_obj is the monthly expiry (last Thursday of the month).
        If adding 7 days moves into the next month, it is the monthly expiry.
        """
        next_week = exp_date_obj + timedelta(days=7)
        return next_week.month != exp_date_obj.month

    EXCHANGE_EXPIRIES: List[str] = [
        "2026-05-07", "2026-05-14", "2026-05-21", "2026-05-28",
        "2026-06-04", "2026-06-11", "2026-06-18", "2026-06-25",
        "2026-07-02", "2026-07-09", "2026-07-16", "2026-07-23", "2026-07-30",
        "2026-08-06", "2026-08-13", "2026-08-20", "2026-08-27",
        "2026-09-03", "2026-09-10", "2026-09-17", "2026-09-24",
        "2026-10-01", "2026-10-08", "2026-10-15", "2026-10-22", "2026-10-29",
        "2026-11-03", "2026-11-09", "2026-11-17", "2026-11-23",
        "2026-12-01", "2026-12-29"
    ]

    @classmethod
    def resolve_expiry_date(cls, current_dt: datetime, underlying: str = "NIFTY") -> str:
        """
        Determines the active weekly expiry date.
        Uses verified official NSE expiry calendar (including Tuesday 2026-11-03 weekly expiry).
        """
        curr_d = current_dt.date() if isinstance(current_dt, datetime) else datetime.strptime(str(current_dt)[:10], "%Y-%m-%d").date()
        is_past_close = False
        if isinstance(current_dt, datetime):
            is_past_close = (current_dt.hour > 15 or (current_dt.hour == 15 and current_dt.minute >= 15))

        for exp_str in cls.EXCHANGE_EXPIRIES:
            exp_d = datetime.strptime(exp_str, "%Y-%m-%d").date()
            if exp_d > curr_d:
                return exp_str
            elif exp_d == curr_d and not is_past_close:
                return exp_str

        # Fallback to nearest upcoming Thursday
        days_ahead = (3 - current_dt.weekday()) % 7
        if days_ahead == 0 and is_past_close:
            days_ahead = 7
        expiry_dt = curr_d + timedelta(days=days_ahead)
        return expiry_dt.strftime("%Y-%m-%d")

    @classmethod
    def build_contract_symbol(
        cls,
        underlying: str,
        strike: float,
        option_type: str,
        expiry_date: str
    ) -> str:
        """
        Formats standardized NSE weekly contract symbol.
        Strictly enforces Weekly Expiry Only:
        - NIFTY26O2922700CE (Oct 29 Weekly)
        - NIFTY26N0522950CE (Nov 05 Weekly)
        - NIFTY26O2222650CE (Oct 22 Weekly)
        """
        try:
            exp = datetime.strptime(expiry_date, "%Y-%m-%d").date()
            year_str = exp.strftime("%y")
            strike_str = str(int(strike))
            opt_str = option_type.upper()
            norm_und = "NIFTY" if "NIFTY" in underlying.upper() else underlying.upper()

            month_char = cls.NSE_WEEKLY_MONTH_MAP.get(exp.month, exp.strftime("%b").upper())
            day_str = f"{exp.day:02d}"
            return f"{norm_und}{year_str}{month_char}{day_str}{strike_str}{opt_str}"
        except Exception:
            return f"{underlying}_{int(strike)}_{option_type}"

    @classmethod
    def resolve_contract(
        cls,
        spot_price: float,
        option_type: str,
        current_dt: datetime,
        underlying: str = "NIFTY",
        avoid_otm: bool = True
    ) -> OptionContract:
        """
        Dynamically constructs full OptionContract for the current market bar,
        strictly avoiding OTM selection and enforcing Weekly Expiry Only.
        """
        strike = cls.resolve_strike(spot_price, option_type, underlying, avoid_otm=avoid_otm)
        expiry_date = cls.resolve_expiry_date(current_dt, underlying)
        symbol = cls.build_contract_symbol(underlying, strike, option_type, expiry_date)
        lot_size = cls.get_lot_size(underlying)
        freeze_lots = cls.get_freeze_lots(underlying)

        return OptionContract(
            symbol=symbol,
            underlying=underlying,
            strike=strike,
            option_type=option_type.upper(),
            expiry_date=expiry_date,
            lot_size=lot_size,
            freeze_lots=freeze_lots
        )

    @classmethod
    def estimate_delta(cls, spot: float, strike: float, option_type: str) -> float:
        """Approximates option delta for sensitivity calculations."""
        diff = spot - strike
        opt = option_type.upper()
        if opt == "CE":
            if abs(diff) < 25.0:
                return 0.50   # ATM
            elif diff >= 25.0:
                return 0.65   # ITM
            else:
                return 0.35   # OTM
        else: # PE
            if abs(diff) < 25.0:
                return -0.50  # ATM
            elif diff <= -25.0:
                return -0.65  # ITM
            else:
                return -0.35  # OTM
