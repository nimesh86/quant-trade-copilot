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
    "SENSEX": "BSESEN",
    "BSESEN": "BSESEN",
    "BSX": "BSESEN",
    "BANKEX": "BKX",
    "BKX": "BKX",
}

# Reverse mapping: Breeze Stock Code -> Canonical Underlying
REVERSE_STOCK_CODE_MAP = {
    "NIFTY": "NIFTY",
    "CNXBAN": "BANKNIFTY",
    "NIFFIN": "FINNIFTY",
    "NIFMID": "MIDCPNIFTY",
    "BSESEN": "SENSEX",
    "BSX": "SENSEX",
    "BKX": "BANKEX",
}

MONTH_MAP = {
    "JAN": 1, "FEB": 2, "MAR": 3, "APR": 4, "MAY": 5, "JUN": 6,
    "JUL": 7, "AUG": 8, "SEP": 9, "OCT": 10, "NOV": 11, "DEC": 12
}

KITE_MONTH_CHAR_MAP = {
    "1": 1, "2": 2, "3": 3, "4": 4, "5": 5, "6": 6,
    "7": 7, "8": 8, "9": 9, "O": 10, "N": 11, "D": 12
}

MONTH_NUM_TO_NAME = {v: k for k, v in MONTH_MAP.items()}


def _get_exchange_for_underlying(underlying: str, product_type: str = "options") -> str:
    """Returns correct exchange (BFO/BSE for BSE underlyings, NFO/NSE for NSE underlyings)."""
    und = underlying.strip().upper()
    is_bse = und in ("SENSEX", "BSX", "BANKEX", "BKX")
    if product_type == "cash":
        return "BSE" if is_bse else "NSE"
    return "BFO" if is_bse else "NFO"


def parse_canonical_symbol(symbol: str) -> Optional[Dict[str, Any]]:
    """
    Parses a standard NSE/BSE option or futures trading symbol.
    Examples:
      - NIFTY26OCT22500CE -> Underlying: NIFTY, Year: 2026, Month: OCT, Strike: 22500, Type: CE
      - SENSEX26OCT79500CE -> Underlying: SENSEX, Year: 2026, Month: OCT, Strike: 79500, Type: CE, Exchange: BFO
      - BANKNIFTY24DEC48000PE -> Underlying: BANKNIFTY, Year: 2024, Month: DEC, Strike: 48000, Type: PE
      - NIFTY24O2422500CE -> Weekly option with single letter month code
    """
    clean_sym = symbol.strip().upper()

    # Pattern 1: Standard Monthly / Dated 3-char month (e.g. NIFTY26OCT22500CE or SENSEX26OCT79500CE)
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
            day_num = 29
            strike = float(num_part)

        year_full = 2000 + year_short
        month_num = MONTH_MAP.get(month_str, 1)

        try:
            exp_date = datetime(year_full, month_num, min(day_num, 31))
        except Exception:
            exp_date = datetime(year_full, month_num, 29)

        right = "Call" if opt_type == "CE" else "Put"
        breeze_code = STOCK_CODE_MAP.get(underlying, underlying)
        exchange_code = _get_exchange_for_underlying(underlying, "options")

        return {
            "canonical_symbol": clean_sym,
            "underlying": underlying,
            "breeze_stock_code": breeze_code,
            "strike_price": int(strike) if strike.is_integer() else strike,
            "option_type": opt_type,
            "right": right,
            "expiry_date": exp_date,
            "product_type": "options",
            "exchange_code": exchange_code
        }

    # Pattern 2: Kite Weekly with single-char month [1-9OND] (e.g. NIFTY26O2424500CE or SENSEX26O2479500CE)
    m_weekly = re.match(r"^([A-Z]+)(\d{2})([1-9OND])(\d{2})(\d+)(CE|PE)$", clean_sym)
    if m_weekly:
        underlying = m_weekly.group(1)
        year_short = int(m_weekly.group(2))
        month_char = m_weekly.group(3)
        day_num = int(m_weekly.group(4))
        strike = float(m_weekly.group(5))
        opt_type = m_weekly.group(6)

        year_full = 2000 + year_short
        month_num = KITE_MONTH_CHAR_MAP.get(month_char, 1)

        try:
            exp_date = datetime(year_full, month_num, min(day_num, 31))
        except Exception:
            exp_date = datetime(year_full, month_num, 28)

        right = "Call" if opt_type == "CE" else "Put"
        breeze_code = STOCK_CODE_MAP.get(underlying, underlying)
        exchange_code = _get_exchange_for_underlying(underlying, "options")

        return {
            "canonical_symbol": clean_sym,
            "underlying": underlying,
            "breeze_stock_code": breeze_code,
            "strike_price": int(strike) if strike.is_integer() else strike,
            "option_type": opt_type,
            "right": right,
            "expiry_date": exp_date,
            "product_type": "options",
            "exchange_code": exchange_code
        }

    # Pattern 3: Standard 6-digit YYMMDD date code (e.g. NIFTY26042424500CE)
    m_6d = re.match(r"^([A-Z]+)(\d{2})(\d{2})(\d{2})(\d+)(CE|PE)$", clean_sym)
    if m_6d:
        underlying = m_6d.group(1)
        year_short = int(m_6d.group(2))
        month_num = int(m_6d.group(3))
        day_num = int(m_6d.group(4))
        strike = float(m_6d.group(5))
        opt_type = m_6d.group(6)

        year_full = 2000 + year_short
        try:
            exp_date = datetime(year_full, max(1, min(12, month_num)), min(day_num, 31))
        except Exception:
            exp_date = datetime(year_full, 1, 28)

        right = "Call" if opt_type == "CE" else "Put"
        breeze_code = STOCK_CODE_MAP.get(underlying, underlying)
        exchange_code = _get_exchange_for_underlying(underlying, "options")

        return {
            "canonical_symbol": clean_sym,
            "underlying": underlying,
            "breeze_stock_code": breeze_code,
            "strike_price": int(strike) if strike.is_integer() else strike,
            "option_type": opt_type,
            "right": right,
            "expiry_date": exp_date,
            "product_type": "options",
            "exchange_code": exchange_code
        }

    # Pattern 4: Futures (e.g. NIFTY26OCTFUT, SENSEX26OCTFUT)
    m_fut = re.match(r"^([A-Z]+)(\d{2})([A-Z]{3})FUT$", clean_sym)
    if m_fut:
        underlying = m_fut.group(1)
        year_short = int(m_fut.group(2))
        month_str = m_fut.group(3)
        year_full = 2000 + year_short
        month_num = MONTH_MAP.get(month_str, 1)
        exp_date = datetime(year_full, month_num, 28)

        breeze_code = STOCK_CODE_MAP.get(underlying, underlying)
        exchange_code = _get_exchange_for_underlying(underlying, "futures")
        return {
            "canonical_symbol": clean_sym,
            "underlying": underlying,
            "breeze_stock_code": breeze_code,
            "strike_price": 0,
            "option_type": "FUT",
            "right": "Others",
            "expiry_date": exp_date,
            "product_type": "futures",
            "exchange_code": exchange_code
        }

    # Pattern 4b: Human-readable Option with spaces (e.g. "NIFTY 03 NOV 22950 CE" or "NIFTY 29 OCT 22750 PE")
    m_human = re.match(r"^([A-Z]+)\s+(\d{1,2})\s*([A-Z]{3})\s*(\d+)\s*(CE|PE)$", clean_sym)
    if m_human:
        underlying = m_human.group(1)
        day_num = int(m_human.group(2))
        month_str = m_human.group(3)
        strike = float(m_human.group(4))
        opt_type = m_human.group(5)
        month_num = MONTH_MAP.get(month_str, 1)
        year_full = datetime.now().year
        try:
            exp_date = datetime(year_full, month_num, min(day_num, 31))
        except Exception:
            exp_date = datetime(year_full, month_num, 28)

        right = "Call" if opt_type == "CE" else "Put"
        breeze_code = STOCK_CODE_MAP.get(underlying, underlying)
        exchange_code = _get_exchange_for_underlying(underlying, "options")
        return {
            "canonical_symbol": clean_sym,
            "underlying": underlying,
            "breeze_stock_code": breeze_code,
            "strike_price": int(strike) if strike.is_integer() else strike,
            "option_type": opt_type,
            "right": right,
            "expiry_date": exp_date,
            "product_type": "options",
            "exchange_code": exchange_code
        }

    # Pattern 5: Spot Index (e.g. NIFTY 50, NIFTY, BANKNIFTY, SENSEX)
    for spot_name in ("NIFTY 50", "NIFTY", "BANKNIFTY", "NIFTY BANK", "SENSEX", "BSESEN", "BSX", "FINNIFTY", "MIDCPNIFTY"):
        if clean_sym == spot_name or (clean_sym.startswith(spot_name) and not ("CE" in clean_sym or "PE" in clean_sym or "FUT" in clean_sym)):
            und = "SENSEX" if ("SENSEX" in spot_name or "BSESEN" in spot_name or "BSX" in spot_name) else (
                "BANKNIFTY" if "BANK" in spot_name else (
                    "FINNIFTY" if "FIN" in spot_name else (
                        "MIDCPNIFTY" if "MID" in spot_name else "NIFTY"
                    )
                )
            )
            breeze_code = STOCK_CODE_MAP.get(und, und)
            exchange_code = _get_exchange_for_underlying(und, "cash")
            return {
                "canonical_symbol": clean_sym,
                "underlying": und,
                "breeze_stock_code": breeze_code,
                "strike_price": 0,
                "option_type": "SPOT",
                "right": "Others",
                "expiry_date": None,
                "product_type": "cash",
                "exchange_code": exchange_code
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
