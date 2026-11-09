"""
Quant Core: Institutional Black-76 & Black-Scholes Option Pricing Engine.
──────────────────────────────────────────────────────────────────────────
Provides exact theoretical and empirical pricing for:
1. Commodity Futures Options (MCX Crude Oil, Natural Gas) using Black-76.
2. Equity Index Options (NSE Nifty, BankNifty, BSE Sensex) using Black-Scholes.
3. Expiry date resolution and canonical exchange contract construction.
4. Capital gating and lot sizing enforcement.
"""

import math
from datetime import datetime, date, timedelta
from typing import Dict, Any, Optional, Tuple


def norm_pdf(x: float) -> float:
    """Standard normal probability density function (PDF)."""
    return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)


def norm_cdf(x: float) -> float:
    """Standard normal cumulative distribution function (CDF) using error function."""
    return (1.0 + math.erf(x / math.sqrt(2.0))) / 2.0


def black_76_price(
    F: float,
    K: float,
    T: float,
    r: float = 0.07,
    sigma: float = 0.52,
    option_type: str = "PE"
) -> float:
    """
    Fisher Black (1976) model for European/American options on futures contracts.
    Authoritative standard for MCX Crude Oil and Natural Gas options.
    
    Args:
        F: Futures underlying price
        K: Strike price
        T: Time to expiry in years (e.g. days / 365.0)
        r: Risk-free rate (default 0.07 = 7% RBI repo rate)
        sigma: Annualized implied volatility (default 0.52 = 52% for Crude Oil)
        option_type: "CE" (Call) or "PE" (Put)
    """
    F = max(1.0, float(F))
    K = max(1.0, float(K))
    T = max(1.0 / 365.0, float(T))
    sigma = max(0.01, float(sigma))
    r = max(0.0, float(r))

    d1 = (math.log(F / K) + 0.5 * sigma * sigma * T) / (sigma * math.sqrt(T))
    d2 = d1 - sigma * math.sqrt(T)
    disc = math.exp(-r * T)

    opt = option_type.upper().strip()
    if opt in ("CE", "CALL", "C"):
        val = disc * (F * norm_cdf(d1) - K * norm_cdf(d2))
        intrinsic = max(0.0, disc * (F - K))
    else:
        val = disc * (K * norm_cdf(-d2) - F * norm_cdf(-d1))
        intrinsic = max(0.0, disc * (K - F))

    return round(max(intrinsic, val, 0.05), 2)


def black_scholes_price(
    S: float,
    K: float,
    T: float,
    r: float = 0.07,
    sigma: float = 0.135,
    option_type: str = "CE"
) -> float:
    """
    Black-Scholes-Merton model for options on spot index (Nifty, BankNifty, Sensex).
    
    Args:
        S: Spot index price
        K: Strike price
        T: Time to expiry in years
        r: Risk-free rate (0.07)
        sigma: Implied volatility (e.g. 0.135 = 13.5% for Nifty)
        option_type: "CE" or "PE"
    """
    S = max(1.0, float(S))
    K = max(1.0, float(K))
    T = max(1.0 / 365.0, float(T))
    sigma = max(0.01, float(sigma))
    r = max(0.0, float(r))

    d1 = (math.log(S / K) + (r + 0.5 * sigma * sigma) * T) / (sigma * math.sqrt(T))
    d2 = d1 - sigma * math.sqrt(T)

    opt = option_type.upper().strip()
    if opt in ("CE", "CALL", "C"):
        val = S * norm_cdf(d1) - K * math.exp(-r * T) * norm_cdf(d2)
        intrinsic = max(0.0, S - K)
    else:
        val = K * math.exp(-r * T) * norm_cdf(-d2) - S * norm_cdf(-d1)
        intrinsic = max(0.0, K - S)

    return round(max(intrinsic, val, 0.05), 2)


def calculate_option_greeks(
    underlying_price: float,
    strike: float,
    T: float,
    r: float = 0.07,
    sigma: float = 0.52,
    option_type: str = "PE",
    is_futures: bool = True
) -> Dict[str, float]:
    """
    Calculates Delta, Gamma, Theta, and Vega.
    """
    F = max(1.0, float(underlying_price))
    K = max(1.0, float(strike))
    T = max(1.0 / 365.0, float(T))
    sigma = max(0.01, float(sigma))
    r = max(0.0, float(r))

    d1 = (math.log(F / K) + 0.5 * sigma * sigma * T) / (sigma * math.sqrt(T))
    d2 = d1 - sigma * math.sqrt(T)
    disc = math.exp(-r * T)
    opt = option_type.upper().strip()

    if is_futures:
        if opt in ("CE", "CALL", "C"):
            delta = disc * norm_cdf(d1)
        else:
            delta = -disc * norm_cdf(-d1)
        gamma = (disc * norm_pdf(d1)) / (F * sigma * math.sqrt(T))
        vega = F * disc * norm_pdf(d1) * math.sqrt(T) / 100.0  # per 1% vol move
        theta = (-(F * sigma * disc * norm_pdf(d1)) / (2.0 * math.sqrt(T)) + r * disc * (K * norm_cdf(d2) - F * norm_cdf(d1))) / 365.0
    else:
        if opt in ("CE", "CALL", "C"):
            delta = norm_cdf(d1)
        else:
            delta = norm_cdf(d1) - 1.0
        gamma = norm_pdf(d1) / (F * sigma * math.sqrt(T))
        vega = F * norm_pdf(d1) * math.sqrt(T) / 100.0
        theta = (-(F * sigma * norm_pdf(d1)) / (2.0 * math.sqrt(T)) - r * K * disc * norm_cdf(d2)) / 365.0

    return {
        "delta": round(delta, 3),
        "gamma": round(gamma, 5),
        "theta": round(theta, 2),
        "vega": round(vega, 2)
    }


def resolve_mcx_crude_expiry(current_dt: Optional[datetime] = None) -> date:
    """
    Resolves the active MCX Crude Oil monthly option expiry date.
    MCX Crude options expire ~15th-19th of the contract month (typically the 17th).
    """
    if current_dt is None:
        current_dt = datetime.now()
    elif isinstance(current_dt, str):
        try:
            current_dt = datetime.fromisoformat(current_dt)
        except Exception:
            current_dt = datetime.now()

    year = current_dt.year
    month = current_dt.month

    # Check 17th of current month
    cand = date(year, month, 17)
    if current_dt.date() > cand or (current_dt.date() == cand and current_dt.hour >= 23):
        # Roll to next month
        if month == 12:
            cand = date(year + 1, 1, 17)
        else:
            cand = date(year, month + 1, 17)

    # Adjust if weekend
    if cand.weekday() == 5:  # Saturday -> Friday 16th
        cand = cand - timedelta(days=1)
    elif cand.weekday() == 6:  # Sunday -> Friday 15th
        cand = cand - timedelta(days=2)

    return cand


def resolve_mcx_naturalgas_expiry(current_dt: Optional[datetime] = None) -> date:
    """
    Resolves the active MCX Natural Gas monthly option expiry date (~24th of the month).
    """
    if current_dt is None:
        current_dt = datetime.now()

    year = current_dt.year
    month = current_dt.month
    cand = date(year, month, 24)
    if current_dt.date() > cand or (current_dt.date() == cand and current_dt.hour >= 23):
        if month == 12:
            cand = date(year + 1, 1, 24)
        else:
            cand = date(year, month + 1, 24)

    if cand.weekday() == 5:
        cand = cand - timedelta(days=1)
    elif cand.weekday() == 6:
        cand = cand - timedelta(days=2)

    return cand


def resolve_nse_equity_expiry(underlying: str, current_dt: Optional[datetime] = None) -> date:
    """
    Resolves weekly expiry date for NSE indices (Thursdays) and BSE Sensex (Fridays).
    """
    if current_dt is None:
        current_dt = datetime.now()

    target_weekday = 4 if "SENSEX" in underlying.upper() else 3  # 4=Friday (BSE), 3=Thursday (NSE)
    days_ahead = (target_weekday - current_dt.weekday()) % 7
    if days_ahead == 0 and (current_dt.hour > 15 or (current_dt.hour == 15 and current_dt.minute >= 15)):
        days_ahead = 7
    return current_dt.date() + timedelta(days=days_ahead)


def get_canonical_contract_info(
    underlying: str,
    spot_price: float,
    direction: str,
    current_dt: Optional[datetime] = None
) -> Dict[str, Any]:
    """
    Constructs full canonical contract details including ATM strike, expiry, symbol name,
    exchange code, and lot size.
    """
    und = underlying.upper().strip()
    is_crude = "CRUDE" in und
    is_ng = "NATURALGAS" in und or "NATGAS" in und
    is_commodity = is_crude or is_ng
    is_bse = "SENSEX" in und or "BANKEX" in und

    # Step size and ATM Strike
    if is_crude:
        step = 50.0
        lot_size = 100
        exchange = "MCX"
        exp_date = resolve_mcx_crude_expiry(current_dt)
        default_iv = 0.52
    elif is_ng:
        step = 5.0
        lot_size = 1250
        exchange = "MCX"
        exp_date = resolve_mcx_naturalgas_expiry(current_dt)
        default_iv = 0.65
    elif is_bse:
        step = 100.0
        lot_size = 10
        exchange = "BFO"
        exp_date = resolve_nse_equity_expiry(und, current_dt)
        default_iv = 0.130
    elif "BANK" in und:
        step = 100.0
        lot_size = 15
        exchange = "NFO"
        exp_date = resolve_nse_equity_expiry(und, current_dt)
        default_iv = 0.165
    else:  # NIFTY
        step = 50.0
        lot_size = 65
        exchange = "NFO"
        exp_date = resolve_nse_equity_expiry(und, current_dt)
        default_iv = 0.135

    atm_strike = int(round(spot_price / step) * step)
    opt_type = "CE" if direction.upper() in ("CALL", "CE", "BUY_CE", "BULLISH", "LONG") else "PE"

    # Contract Display Symbol
    # E.g. CRUDEOIL 17 NOV 8200 PE
    exp_day = f"{exp_date.day:02d}"
    exp_month = exp_date.strftime("%b").upper()
    exp_year = exp_date.strftime("%y")

    display_sym = f"{und} {exp_day} {exp_month} {atm_strike} {opt_type}"
    # Standard exchange symbol without spaces e.g. CRUDEOIL26NOV8200PE
    exchange_sym = f"{und}{exp_year}{exp_month}{atm_strike}{opt_type}"

    return {
        "underlying": und,
        "is_commodity": is_commodity,
        "exchange": exchange,
        "strike": atm_strike,
        "option_type": opt_type,
        "expiry_date": exp_date.isoformat(),
        "display_symbol": display_sym,
        "exchange_symbol": exchange_sym,
        "lot_size": lot_size,
        "default_iv": default_iv
    }


def estimate_option_premium(
    underlying: str,
    underlying_price: float,
    strike: float,
    option_type: str,
    current_dt: Optional[datetime] = None,
    broker_adapter: Optional[Any] = None,
    custom_iv: Optional[float] = None
) -> Tuple[float, str]:
    """
    Computes realistic market option premium:
    1. First tries broker quote lookup if connected.
    2. Falls back to Black-76 (commodity) or Black-Scholes (equity) calibrated with real market IV.
    
    Returns:
        (premium_price, pricing_source)
    """
    und = underlying.upper().strip()
    is_commodity = "CRUDE" in und or "NATURALGAS" in und or "NATGAS" in und
    now = current_dt or datetime.now()

    # Expiry and DTE
    if "CRUDE" in und:
        exp_d = resolve_mcx_crude_expiry(now)
        default_iv = 0.52
    elif "NATURALGAS" in und or "NATGAS" in und:
        exp_d = resolve_mcx_naturalgas_expiry(now)
        default_iv = 0.65
    elif "SENSEX" in und:
        exp_d = resolve_nse_equity_expiry(und, now)
        default_iv = 0.130
    elif "BANK" in und:
        exp_d = resolve_nse_equity_expiry(und, now)
        default_iv = 0.165
    else:
        exp_d = resolve_nse_equity_expiry(und, now)
        default_iv = 0.135

    iv = custom_iv if custom_iv and custom_iv > 0 else default_iv

    # Check broker adapter live quote if available
    if broker_adapter and hasattr(broker_adapter, "get_quote"):
        try:
            exp_day = f"{exp_d.day:02d}"
            exp_month = exp_d.strftime("%b").upper()
            sym_to_check = f"{und} {exp_day} {exp_month} {int(strike)} {option_type}"
            q = broker_adapter.get_quote(sym_to_check)
            if q and q.get("price") and float(q["price"]) > 0:
                return float(q["price"]), "BROKER_LIVE_QUOTE"
        except Exception:
            pass

    # Black-76 or Black-Scholes mathematical model
    exp_dt = datetime(exp_d.year, exp_d.month, exp_d.day, 23, 30 if is_commodity else 15, 30)
    diff_secs = max(3600.0, (exp_dt - now).total_seconds())
    T = diff_secs / (365.0 * 86400.0)

    if is_commodity:
        prem = black_76_price(
            F=underlying_price,
            K=strike,
            T=T,
            r=0.07,
            sigma=iv,
            option_type=option_type
        )
        return prem, "BLACK_76_MODEL"
    else:
        prem = black_scholes_price(
            S=underlying_price,
            K=strike,
            T=T,
            r=0.07,
            sigma=iv,
            option_type=option_type
        )
        return prem, "BLACK_SCHOLES_MODEL"


def calculate_realistic_lots_and_margin(
    capital: float,
    available_capital: float,
    option_premium: float,
    lot_size: int,
    risk_pct: float = 0.07,
    opt_sl_pts: float = 12.0,
    brokerage_per_lot: float = 90.0,
    max_lots: int = 50
) -> Tuple[int, int, float, bool, str]:
    """
    Computes strict capital-gated position sizing:
    1. Computes desired lots from compounding risk budget (e.g. 7%).
    2. Strictly enforces that total premium (lots * lot_size * premium) does NOT exceed available capital.
    3. If available capital cannot afford even 1 lot, aborts trade.
    
    Returns:
        (lots, units, required_margin, allowed, reason)
    """
    cap = max(0.0, float(capital))
    avail = max(0.0, float(available_capital))
    prem = max(0.05, float(option_premium))
    cost_1_lot = round(prem * lot_size, 2)

    if cost_1_lot > avail:
        return 0, 0, 0.0, False, (
            f"Insufficient available capital. 1 lot requires ₹{cost_1_lot:,.2f} margin, "
            f"but available capital is only ₹{avail:,.2f}."
        )

    # Risk-based sizing
    risk_budget = cap * risk_pct
    risk_per_lot = (opt_sl_pts * lot_size) + brokerage_per_lot
    desired_lots = max(1, int(risk_budget // risk_per_lot))
    desired_lots = min(desired_lots, max_lots)

    # Capital affordability cap
    max_affordable_lots = int(avail // cost_1_lot)
    final_lots = min(desired_lots, max_affordable_lots)

    if final_lots <= 0:
        return 0, 0, 0.0, False, "Cannot afford 1 lot within available capital."

    units = final_lots * lot_size
    required_margin = round(final_lots * cost_1_lot, 2)

    return final_lots, units, required_margin, True, "OK"
