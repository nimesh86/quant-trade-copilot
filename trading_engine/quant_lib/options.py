"""
Options Analytics & Black-Scholes Greeks for quant_lib.
Calculates Delta, Gamma, Theta, Vega, Implied Volatility (IV Solver), and ATM Straddle premium.
"""
from dataclasses import dataclass
from typing import Tuple, Union, Optional
import numpy as np
from scipy.stats import norm
from scipy.optimize import brentq


@dataclass
class GreeksResult:
    """Container for Black-Scholes Greeks."""
    price: float
    delta: float
    gamma: float
    theta: float      # Time decay per calendar day
    vega: float       # Sensitivity per 1% change in IV (0.01)
    rho: float


def black_scholes_greeks(
    spot: float,
    strike: float,
    t_years: float,
    r: float,
    iv: float,
    option_type: str = "CE"
) -> GreeksResult:
    """
    Computes theoretical option price and Greeks using Black-Scholes-Merton model.
    spot: Current underlying price (e.g. 24200.0)
    strike: Option strike price (e.g. 24200.0)
    t_years: Time to expiry in years (e.g. 3 / 365.0)
    r: Risk-free rate (e.g. 0.07 for 7%)
    iv: Annualized implied volatility (e.g. 0.15 for 15%)
    option_type: "CE" (Call) or "PE" (Put)
    """
    s = float(spot)
    k = float(strike)
    t = max(1e-6, float(t_years))
    rate = float(r)
    sigma = max(1e-4, float(iv))
    is_call = option_type.upper() in ("CE", "CALL")

    sqrt_t = np.sqrt(t)
    d1 = (np.log(s / k) + (rate + 0.5 * sigma * sigma) * t) / (sigma * sqrt_t)
    d2 = d1 - sigma * sqrt_t

    pdf_d1 = norm.pdf(d1)
    cdf_d1 = norm.cdf(d1)
    cdf_d2 = norm.cdf(d2)

    exp_rt = np.exp(-rate * t)

    if is_call:
        price = s * cdf_d1 - k * exp_rt * cdf_d2
        delta = float(cdf_d1)
        theta = float(-(s * pdf_d1 * sigma) / (2.0 * sqrt_t) - rate * k * exp_rt * cdf_d2) / 365.0
        rho = float(k * t * exp_rt * cdf_d2) * 0.01
    else:
        cdf_minus_d1 = norm.cdf(-d1)
        cdf_minus_d2 = norm.cdf(-d2)
        price = k * exp_rt * cdf_minus_d2 - s * cdf_minus_d1
        delta = float(cdf_d1 - 1.0)
        theta = float(-(s * pdf_d1 * sigma) / (2.0 * sqrt_t) + rate * k * exp_rt * cdf_minus_d2) / 365.0
        rho = float(-k * t * exp_rt * cdf_minus_d2) * 0.01

    gamma = float(pdf_d1 / (s * sigma * sqrt_t))
    # Vega per 1% change in volatility
    vega = float(s * sqrt_t * pdf_d1) * 0.01

    return GreeksResult(
        price=round(float(price), 2),
        delta=round(delta, 4),
        gamma=round(gamma, 6),
        theta=round(theta, 2),
        vega=round(vega, 2),
        rho=round(rho, 4)
    )


def implied_volatility(
    market_price: float,
    spot: float,
    strike: float,
    t_years: float,
    r: float = 0.07,
    option_type: str = "CE"
) -> float:
    """
    Solves for implied volatility (IV) from observed market premium using Brent's method.
    Returns annualized IV as a decimal (e.g. 0.145 for 14.5%).
    """
    mkt = float(market_price)
    if mkt <= 0.0:
        return 0.0

    s = float(spot)
    k = float(strike)
    t = max(1e-6, float(t_years))
    is_call = option_type.upper() in ("CE", "CALL")

    # Intrinsic value floor
    intrinsic = max(0.0, (s - k) if is_call else (k - s))
    if mkt < intrinsic:
        return 0.0

    def objective(iv_guess: float) -> float:
        res = black_scholes_greeks(s, k, t, r, iv_guess, option_type)
        return res.price - mkt

    try:
        # Bracket search from 1% to 500% IV
        root = brentq(objective, 0.01, 5.0, maxiter=50)
        return round(float(root), 4)
    except Exception:
        return 0.0


def atm_straddle(
    ce_price: float,
    pe_price: float,
    atm_strike: Optional[float] = None
) -> Tuple[float, Optional[float], Optional[float]]:
    """
    Calculates combined ATM straddle premium and breakevens.
    Returns (straddle_premium, upper_breakeven, lower_breakeven).
    """
    straddle = round(float(ce_price) + float(pe_price), 2)
    if atm_strike is not None:
        upper = round(float(atm_strike) + straddle, 2)
        lower = round(float(atm_strike) - straddle, 2)
        return straddle, upper, lower
    return straddle, None, None


def put_call_ratio(
    pe_oi_or_vol: Union[np.ndarray, list, float],
    ce_oi_or_vol: Union[np.ndarray, list, float]
) -> float:
    """Calculates Put-Call Ratio (PCR = Sum(PE) / Sum(CE))."""
    pe_sum = float(np.sum(pe_oi_or_vol))
    ce_sum = float(np.sum(ce_oi_or_vol))
    return round(pe_sum / ce_sum, 2) if ce_sum > 0 else 1.0
