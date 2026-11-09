"""
Unit tests for Black-76 / Black-Scholes Option Pricing Engine and Capital Gating.
─────────────────────────────────────────────────────────────────────────────────
Validates:
1. Black-76 commodity option pricing on futures (MCX Crude Oil, Natural Gas).
2. Black-Scholes equity index option pricing (NSE Nifty, BankNifty, BSE Sensex).
3. Exact MCX contract expiry resolution (e.g. 17 Nov 2026).
4. Strict capital gating and margin enforcement (prevents over-allocating beyond account equity).
5. Symbol translation and canonical MCX exchange recognition.
"""

import pytest
from datetime import datetime, date
from trading_engine.quant_core.option_pricing import (
    black_76_price,
    black_scholes_price,
    calculate_option_greeks,
    resolve_mcx_crude_expiry,
    resolve_mcx_naturalgas_expiry,
    resolve_nse_equity_expiry,
    get_canonical_contract_info,
    estimate_option_premium,
    calculate_realistic_lots_and_margin,
)
from trading_engine.adapters.symbol_translator import (
    parse_canonical_symbol,
    to_breeze_params,
    _get_exchange_for_underlying,
)


class TestBlack76CommodityOptionPricing:
    """Verifies Black-76 model for MCX Crude Oil and Natural Gas."""

    def test_crude_oil_atm_put_pricing(self):
        """
        Crude Oil at F=8177, K=8200, 8 days to expiry (T=8/365), IV=52%, r=7%.
        The theoretical Black-76 price must be in the realistic market range of ₹250 to ₹290,
        never the hardcoded dummy value ₹137.
        """
        F = 8177.0
        K = 8200.0
        T = 8.0 / 365.0
        prem_pe = black_76_price(F=F, K=K, T=T, r=0.07, sigma=0.52, option_type="PE")
        prem_ce = black_76_price(F=F, K=K, T=T, r=0.07, sigma=0.52, option_type="CE")

        # PE is slightly ITM (8200 - 8177 = 23 pts intrinsic), should be higher than CE
        assert 240.0 <= prem_pe <= 310.0, f"Crude 8200 PE premium ₹{prem_pe} out of realistic range!"
        assert 210.0 <= prem_ce <= 280.0, f"Crude 8200 CE premium ₹{prem_ce} out of realistic range!"
        assert prem_pe > prem_ce, "Put premium must exceed Call premium since strike (8200) > futures (8177)"

    def test_crude_oil_delta_and_greeks(self):
        """Delta for near-ATM Crude put should be approximately -0.50 to -0.60."""
        greeks = calculate_option_greeks(
            underlying_price=8177.0,
            strike=8200.0,
            T=8.0 / 365.0,
            r=0.07,
            sigma=0.52,
            option_type="PE",
            is_futures=True
        )
        assert -0.65 <= greeks["delta"] <= -0.45, f"Unexpected Put Delta: {greeks['delta']}"
        assert greeks["gamma"] > 0, "Gamma must be positive"
        assert greeks["vega"] > 0, "Vega must be positive"

    def test_intrinsic_value_floor(self):
        """Deep ITM option must never price below discounted intrinsic value."""
        # Deep ITM Put: F=7500, K=8200 -> intrinsic approx 700 pts
        prem = black_76_price(F=7500.0, K=8200.0, T=5.0 / 365.0, r=0.07, sigma=0.52, option_type="PE")
        assert prem >= 690.0, f"Deep ITM put must not violate intrinsic floor, got {prem}"


class TestBlackScholesEquityPricing:
    """Verifies Black-Scholes model for NSE Nifty index options."""

    def test_nifty_atm_call_pricing(self):
        """
        NIFTY spot at 24500, K=24500, 3 days to weekly expiry (T=3/365), IV=13.5%, r=7%.
        Premium should be around ₹110 to ₹160.
        """
        S = 24500.0
        K = 24500.0
        T = 3.0 / 365.0
        prem_ce = black_scholes_price(S=S, K=K, T=T, r=0.07, sigma=0.135, option_type="CE")
        prem_pe = black_scholes_price(S=S, K=K, T=T, r=0.07, sigma=0.135, option_type="PE")

        assert 100.0 <= prem_ce <= 180.0, f"Nifty ATM CE premium ₹{prem_ce} out of realistic range!"
        assert 90.0 <= prem_pe <= 170.0, f"Nifty ATM PE premium ₹{prem_pe} out of realistic range!"


class TestContractExpiryResolution:
    """Verifies contract expiry dates match exchange specifications."""

    def test_mcx_crude_expiry_resolution(self):
        """For November 2026 before the 17th, expiry must be 2026-11-17."""
        dt = datetime(2026, 11, 9, 19, 17)
        exp = resolve_mcx_crude_expiry(dt)
        assert exp == date(2026, 11, 17), f"Expected 2026-11-17, got {exp}"

    def test_canonical_contract_info_crude(self):
        """Contract info must include canonical display and exchange symbols."""
        dt = datetime(2026, 11, 9, 19, 17)
        info = get_canonical_contract_info("CRUDEOIL", 8177.0, "PUT", dt)
        assert info["underlying"] == "CRUDEOIL"
        assert info["exchange"] == "MCX"
        assert info["strike"] == 8200
        assert info["option_type"] == "PE"
        assert info["lot_size"] == 100
        assert info["display_symbol"] == "CRUDEOIL 17 NOV 8200 PE"
        assert info["exchange_symbol"] == "CRUDEOIL26NOV8200PE"


class TestCapitalGatingAndPositionSizing:
    """Verifies strict capital gating prevents over-allocating beyond available equity."""

    def test_crude_sizing_on_50k_account(self):
        """
        At ₹280 premium, 1 lot requires ₹28,000 margin.
        2 lots require ₹56,000 margin (> ₹50,000 capital).
        A ₹50,000 account MUST be capped at 1 lot!
        """
        prem = 280.0
        lot_size = 100
        capital = 50000.0
        avail = 50000.0

        lots, units, margin, allowed, reason = calculate_realistic_lots_and_margin(
            capital=capital,
            available_capital=avail,
            option_premium=prem,
            lot_size=lot_size,
            risk_pct=0.07,
            opt_sl_pts=12.0
        )

        assert allowed is True, f"Trade should be allowed for 1 lot: {reason}"
        assert lots == 1, f"Expected 1 lot, but got {lots} lots! Account cannot afford 2 lots (₹56,000 > ₹50,000)"
        assert units == 100
        assert margin == 28000.0
        assert margin <= avail

    def test_crude_sizing_on_20k_insufficient_capital(self):
        """
        A ₹20,000 account CANNOT afford 1 lot at ₹280 premium (₹28,000 required).
        Trade must be strictly aborted with allowed=False.
        """
        prem = 280.0
        lot_size = 100
        capital = 20000.0
        avail = 20000.0

        lots, units, margin, allowed, reason = calculate_realistic_lots_and_margin(
            capital=capital,
            available_capital=avail,
            option_premium=prem,
            lot_size=lot_size,
            risk_pct=0.07,
            opt_sl_pts=12.0
        )

        assert allowed is False, "Trade must be rejected due to insufficient margin!"
        assert lots == 0
        assert "Insufficient available capital" in reason

    def test_nifty_equity_sizing(self):
        """At ₹140 premium, 1 lot (65 units) costs ₹9,100 margin on a ₹20,000 account."""
        prem = 140.0
        lot_size = 65
        capital = 20000.0
        avail = 20000.0

        lots, units, margin, allowed, reason = calculate_realistic_lots_and_margin(
            capital=capital,
            available_capital=avail,
            option_premium=prem,
            lot_size=lot_size,
            risk_pct=0.07,
            opt_sl_pts=20.0
        )

        assert allowed is True
        assert lots == 1
        assert units == 65
        assert margin == 9100.0
        assert margin <= avail


class TestSymbolTranslatorMCXSupport:
    """Verifies symbol translator handles MCX commodities."""

    def test_mcx_exchange_mapping(self):
        assert _get_exchange_for_underlying("CRUDEOIL") == "MCX"
        assert _get_exchange_for_underlying("NATURALGAS") == "MCX"
        assert _get_exchange_for_underlying("NIFTY") == "NFO"
        assert _get_exchange_for_underlying("SENSEX") == "BFO"

    def test_canonical_parsing_crude_options(self):
        p1 = parse_canonical_symbol("CRUDEOIL 17 NOV 8200 PE")
        assert p1 is not None
        assert p1["underlying"] == "CRUDEOIL"
        assert p1["strike_price"] == 8200
        assert p1["option_type"] == "PE"
        assert p1["exchange_code"] == "MCX"

        p2 = parse_canonical_symbol("CRUDEOIL 8200 PE")
        assert p2 is not None
        assert p2["underlying"] == "CRUDEOIL"
        assert p2["strike_price"] == 8200
        assert p2["exchange_code"] == "MCX"

        p3 = parse_canonical_symbol("CRUDEOIL26NOV8200PE")
        assert p3 is not None
        assert p3["underlying"] == "CRUDEOIL"
        assert p3["strike_price"] == 8200
        assert p3["exchange_code"] == "MCX"
