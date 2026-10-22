"""
Symbol Translator for Multi-Broker Support
──────────────────────────────────────────
Translates contract symbols between Canonical NSE / Zerodha format and ICICI Direct Breeze format.
"""

import re
from datetime import datetime
from typing import Dict, Any, Optional, Tuple


# Stock code mapping: Canonical Underlying -> ICICI Direct Breeze Stock Code
STOCK_CODE_MAP = {
    "NIFTY": "NIFTY",
    "NIFTY 50": "NIFTY",
    "BANKNIFTY": "CNXBAN",
    "NIFTY BANK": "CNXBAN",
    "FINNIFTY": "NIFFIN",
    "NIFTY FIN SERVICE": "NIFFIN",
    "MIDCPNIFTY": "NIFMID",
}

# Reverse mapping: Breeze Stock Code -> Canonical Underlying
REVERSE_STOCK_CODE_MAP = {
    "NIFTY": "NIFTY",
    "CNXBAN": "BANKNIFTY",
    "NIFFIN": "FINNIFTY",
    "NIFMID": "MIDCPNIFTY",
}

MONTH_MAP = {
    "JAN": 1, "FEB": 2, "MAR": 3, "APR": 4, "MAY": 5, "JUN": 6,
    "JUL": 7, "AUG": 8, "SEP": 9, "OCT": 10, "NOV": 11, "DEC": 12
}

MONTH_NUM_TO_NAME = {v: k for k, v in MONTH_MAP.items()}


def parse_canonical_symbol(symbol: str) -> Optional[Dict[str, Any]]:
    """
    Parses a standard NSE option trading symbol.
    Examples:
      - NIFTY26OCT22500CE -> Underlying: NIFTY, Year: 2026, Month: OCT, Strike: 22500, Type: CE
      - BANKNIFTY24DEC48000PE -> Underlying: BANKNIFTY, Year: 2024, Month: DEC, Strike: 48000, Type: PE
      - NIFTY24O2422500CE -> Weekly option with single letter month code
    """
    clean_sym = symbol.strip().upper()

    # Pattern 1: Standard Monthly / Dated (e.g. NIFTY26OCT22500CE or NIFTY24OCT3122500CE)
    m = re.match(r"^([A-Z]+)(\d{2})([A-Z]{3})(\d+)(CE|PE)$", clean_sym)
    if m:
        underlying = m.group(1)
        year_short = int(m.group(2))
        month_str = m.group(3)
        num_part = m.group(4)
        opt_type = m.group(5)

        # Disambiguate day vs strike
        if len(num_part) >= 6 and 1 <= int(num_part[:2]) <= 31 and int(num_part[2:]) % 50 == 0:
            day_num = int(num_part[:2])
            strike = float(num_part[2:])
        else:
            day_num = 28
            strike = float(num_part)

        year_full = 2000 + year_short
        month_num = MONTH_MAP.get(month_str, 1)

        try:
            exp_date = datetime(year_full, month_num, min(day_num, 28))
        except Exception:
            exp_date = datetime(year_full, month_num, 28)

        right = "Call" if opt_type == "CE" else "Put"
        breeze_code = STOCK_CODE_MAP.get(underlying, underlying)

        return {
            "canonical_symbol": clean_sym,
            "underlying": underlying,
            "breeze_stock_code": breeze_code,
            "strike_price": int(strike) if strike.is_integer() else strike,
            "option_type": opt_type,
            "right": right,
            "expiry_date": exp_date,
            "product_type": "options",
            "exchange_code": "NFO"
        }

    # Pattern 2: Futures (e.g. NIFTY26OCTFUT)
    m_fut = re.match(r"^([A-Z]+)(\d{2})([A-Z]{3})FUT$", clean_sym)
    if m_fut:
        underlying = m_fut.group(1)
        year_short = int(m_fut.group(2))
        month_str = m_fut.group(3)
        year_full = 2000 + year_short
        month_num = MONTH_MAP.get(month_str, 1)
        exp_date = datetime(year_full, month_num, 28)

        breeze_code = STOCK_CODE_MAP.get(underlying, underlying)
        return {
            "canonical_symbol": clean_sym,
            "underlying": underlying,
            "breeze_stock_code": breeze_code,
            "strike_price": 0,
            "option_type": "FUT",
            "right": "Others",
            "expiry_date": exp_date,
            "product_type": "futures",
            "exchange_code": "NFO"
        }

    # Fallback for Spot Index (e.g. NIFTY 50, NIFTY)
    if "NIFTY" in clean_sym and not any(clean_sym.endswith(x) for x in ["CE", "PE", "FUT"]):
        return {
            "canonical_symbol": clean_sym,
            "underlying": "NIFTY",
            "breeze_stock_code": "NIFTY",
            "strike_price": 0,
            "option_type": "SPOT",
            "right": "Others",
            "expiry_date": None,
            "product_type": "cash",
            "exchange_code": "NSE"
        }

    return None


def to_breeze_params(symbol: str, exact_expiry_iso: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """
    Converts a canonical symbol to the exact parameters required by BreezeConnect methods.
    """
    parsed = parse_canonical_symbol(symbol)
    if not parsed:
        return None

    if exact_expiry_iso:
        expiry_str = exact_expiry_iso
    elif parsed["expiry_date"]:
        # Breeze format: YYYY-MM-DDT06:00:00.000Z
        expiry_str = parsed["expiry_date"].strftime("%Y-%m-%dT06:00:00.000Z")
    else:
        expiry_str = ""

    return {
        "stock_code": parsed["breeze_stock_code"],
        "exchange_code": parsed["exchange_code"],
        "product_type": parsed["product_type"],
        "strike_price": str(parsed["strike_price"]),
        "right": parsed["right"],
        "expiry_date": expiry_str,
    }


def to_canonical_symbol(breeze_stock_code: str, strike: Any, right: str, expiry_date: Any) -> str:
    """
    Constructs a standard NSE symbol from Breeze parameters.
    """
    underlying = REVERSE_STOCK_CODE_MAP.get(breeze_stock_code, breeze_stock_code)
    opt_type = "CE" if str(right).lower() in ["call", "ce"] else "PE"

    dt = None
    if isinstance(expiry_date, str):
        for fmt in ["%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%d", "%d-%b-%Y"]:
            try:
                dt = datetime.strptime(expiry_date, fmt)
                break
            except Exception:
                pass
    elif isinstance(expiry_date, datetime):
        dt = expiry_date

    if dt:
        year_str = dt.strftime("%y")
        month_str = dt.strftime("%b").upper()
        # Canonical format e.g. NIFTY26OCT22500CE
        return f"{underlying}{year_str}{month_str}{int(float(strike))}{opt_type}"
    else:
        return f"{underlying}{int(float(strike))}{opt_type}"
