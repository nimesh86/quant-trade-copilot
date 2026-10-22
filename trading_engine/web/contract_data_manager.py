"""
Contract Data Manager
─────────────────────
In-memory transient tick storage and historical catch-up manager for the
dedicated Contract Analysis Page (/contract).

Responsibilities:
  1. Maintain per-symbol in-memory ring buffers (collections.deque) for live ticks.
  2. Aggregate ticks into 1-minute forming candles per symbol.
  3. Provide mid-session historical candle catch-up via Zerodha Kite Connect API.
  4. Auto-flush / discard tick buffers at EOD (16:00 IST).
  5. Resolve any Kite instrument token given a Zerodha trading symbol.
  6. Search contract symbols from the local symbol_master table with fuzzy matching.
"""

import os
import sys
import time
import json
import asyncio
import threading
from collections import deque
from datetime import datetime, date, timedelta
from typing import Dict, List, Optional, Any, Tuple

ENGINE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
AI_TRADER_DIR = "/Users/admin/Documents/Nimesh/Projects/ai-trade/AI-trader"

# ── In-Memory Tick Buffers ────────────────────────────────────────────────────

# symbol -> deque of raw tick dicts
_tick_buffers: Dict[str, deque] = {}
# symbol -> forming candle dict
_forming_candles: Dict[str, Optional[Dict]] = {}
# symbol -> list of completed 1-min candle dicts for today
_session_candles: Dict[str, List[Dict]] = {}

BUFFER_MAX = 100_000  # max ticks per symbol
_lock = threading.Lock()

# ── In-Memory Live Price Bus ──────────────────────────────────────────────────
# Written directly by run_live_tick_broadcaster on every tick — no disk I/O.
# Format: { symbol: {"price": float, "ts": str} }
_live_price_bus: Dict[str, Dict] = {}
_price_bus_lock = threading.Lock()


def push_to_price_bus(symbol: str, price: float, ts: Optional[str] = None) -> None:
    """Called by the main tick broadcaster to publish a new price with zero file I/O."""
    with _price_bus_lock:
        _live_price_bus[symbol] = {"price": price, "ts": ts or datetime.now().isoformat()}


def get_live_price_from_bus(symbol: str) -> Optional[float]:
    """Read the latest price for a symbol directly from the in-memory bus."""
    with _price_bus_lock:
        entry = _live_price_bus.get(symbol) or _live_price_bus.get(symbol.upper())
        return float(entry["price"]) if entry and entry.get("price") else None


def get_all_bus_prices() -> Dict[str, Dict]:
    """Return a snapshot of the full in-memory price bus."""
    with _price_bus_lock:
        return dict(_live_price_bus)


def _get_ist_now() -> datetime:
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo("Asia/Kolkata"))
    except Exception:
        from datetime import timezone
        return datetime.now(timezone.utc).astimezone()


def is_market_open() -> bool:
    now = _get_ist_now()
    if now.weekday() >= 5:
        return False
    t = now.time()
    from datetime import time as dtime
    return dtime(9, 15) <= t <= dtime(15, 30)


def is_pre_market() -> bool:
    now = _get_ist_now()
    if now.weekday() >= 5:
        return False
    from datetime import time as dtime
    return now.time() < dtime(9, 15)


def get_today_str() -> str:
    return _get_ist_now().strftime("%Y-%m-%d")


# ── Kite Client ───────────────────────────────────────────────────────────────

_kite_client = None


def _get_kite():
    global _kite_client
    if _kite_client is not None:
        return _kite_client
    try:
        if AI_TRADER_DIR not in sys.path:
            sys.path.insert(0, AI_TRADER_DIR)
        from dotenv import load_dotenv
        load_dotenv(os.path.join(AI_TRADER_DIR, ".env"))
        try:
            from trading_engine.core.keyvault import get_secret
            api_key = get_secret("KITE_API_KEY") or os.environ.get("KITE_API_KEY") or os.environ.get("ZERODHA_API_KEY")
            access_token = get_secret("KITE_ACCESS_TOKEN") or os.environ.get("KITE_ACCESS_TOKEN") or os.environ.get("ZERODHA_ACCESS_TOKEN")
        except Exception:
            api_key = os.environ.get("KITE_API_KEY") or os.environ.get("ZERODHA_API_KEY")
            access_token = os.environ.get("KITE_ACCESS_TOKEN") or os.environ.get("ZERODHA_ACCESS_TOKEN")

        if not api_key or not access_token:
            return None
        kite = KiteConnect(api_key=api_key)
        kite.set_access_token(access_token)
        _kite_client = kite
        return kite
    except Exception as e:
        print(f"ContractDataManager: Kite init error: {e}", flush=True)
        return None


def reset_kite():
    """Force re-init on next use (e.g. after token refresh)."""
    global _kite_client
    _kite_client = None


# ── Instrument Token Resolution ───────────────────────────────────────────────

# Cache: trading_symbol -> instrument_token
_token_cache: Dict[str, int] = {}
# full instrument list cache
_instruments_cache: Optional[List[Dict]] = None
_instruments_ts: float = 0.0
INSTRUMENTS_TTL = 6 * 3600  # refresh every 6 hours


def _load_instruments() -> List[Dict]:
    global _instruments_cache, _instruments_ts
    now = time.time()
    if _instruments_cache and (now - _instruments_ts) < INSTRUMENTS_TTL:
        return _instruments_cache
    try:
        kite = _get_kite()
        if not kite:
            return []
        nfo = kite.instruments("NFO")
        nse = kite.instruments("NSE")
        _instruments_cache = nfo + nse
        _instruments_ts = now
        print(f"ContractDataManager: Loaded {len(_instruments_cache)} instruments from Kite.", flush=True)
        return _instruments_cache
    except Exception as e:
        print(f"ContractDataManager: Failed to load instruments: {e}", flush=True)
        return _instruments_cache or []


def resolve_token(trading_symbol: str) -> Optional[int]:
    """Return Kite instrument_token for a given trading symbol (e.g. NIFTY26O2022800CE)."""
    if trading_symbol in _token_cache:
        return _token_cache[trading_symbol]
    instruments = _load_instruments()
    for inst in instruments:
        ts = inst.get("tradingsymbol", "")
        if ts == trading_symbol:
            tok = inst["instrument_token"]
            _token_cache[trading_symbol] = tok
            return tok
    # Try NSE / NFO prefix
    for inst in instruments:
        ts = inst.get("tradingsymbol", "")
        if ts.upper() == trading_symbol.upper():
            tok = inst["instrument_token"]
            _token_cache[trading_symbol] = tok
            return tok
    return None


def resolve_token_for_kite_symbol(symbol: str) -> Tuple[Optional[int], Optional[str]]:
    """Return (token, exchange) for built-in symbols like NIFTY-I, NIFTY 50 etc."""
    # Well-known spot symbols
    spot_map = {
        "NIFTY 50": (256265, "NSE"),
        "NIFTY BANK": (260105, "NSE"),
        "BANKNIFTY": (260105, "NSE"),
        "NIFTY": (256265, "NSE"),
        "INDIAVIX": (264969, "NSE"),
    }
    if symbol in spot_map:
        return spot_map[symbol]
    
    # Futures
    if symbol.endswith("-I") or symbol.endswith("-II"):
        instruments = _load_instruments()
        base = symbol.replace("-I", "").replace("-II", "").strip()
        fut_suffix = "FUT"
        matches = [i for i in instruments if i.get("instrument_type") == "FUT" and i["name"].upper() == base.upper()]
        if matches:
            matches.sort(key=lambda i: i.get("expiry", date.max))
            if symbol.endswith("-II") and len(matches) > 1:
                return (matches[1]["instrument_token"], "NFO")
            return (matches[0]["instrument_token"], "NFO")
    
    # Option symbol from symbol_master (NIFTY26O2022800CE)
    token = resolve_token(symbol)
    if token:
        return (token, "NFO")
    
    # Equity
    instruments = _load_instruments()
    for inst in instruments:
        if inst.get("tradingsymbol") == symbol and inst.get("segment") == "NSE":
            return (inst["instrument_token"], "NSE")
    
    return (None, None)


# ── Expiry Dates & Option Chain Matrix ────────────────────────────────────────

def get_available_expiries(underlying: str = "NIFTY") -> List[Dict[str, Any]]:
    """
    Return sorted list of active future expiry dates for the given underlying from Kite instruments,
    plus recent traded historical session expiries from DB so historical sessions can be analyzed.
    """
    insts = _load_instruments()
    today = _get_ist_now().date()
    und_upper = underlying.strip().upper()
    
    exps = set()
    for i in insts:
        if i.get("name") == und_upper and i.get("segment") == "NFO-OPT":
            exp = i.get("expiry")
            if exp and exp >= today:
                exps.add(exp)
                
    sorted_exps = sorted(list(exps))
    res = []
    for exp in sorted_exps:
        days = (exp - today).days
        d_str = exp.strftime("%d %b %Y")
        tag = " (Weekly)" if days <= 7 else (" (Monthly)" if days <= 35 and exp.day >= 24 else "")
        res.append({
            "date": exp.strftime("%Y-%m-%d"),
            "display": f"{d_str}{tag}",
            "days_to_expiry": days,
            "is_near": days <= 4,
        })

    # Include recent traded weekly expiries from DB minute_candles
    try:
        from sqlalchemy import create_engine, text
        db_engine = create_engine(os.environ.get("DB_URL", "postgresql+psycopg2://postgres:postgres@localhost:5432/trading"))
        with db_engine.connect() as conn:
            q_db = text("""
                SELECT DISTINCT substring(symbol from '^' || :und || '26([1-9OND][0-9]{2})') as exp_code
                FROM minute_candles
                WHERE symbol ~ ('^' || :und || '26[1-9OND][0-9]{2}[0-9]{5}(CE|PE)$')
                ORDER BY 1 DESC
                LIMIT 5;
            """)
            codes = conn.execute(q_db, {"und": und_upper}).fetchall()
            month_map = {'1': 1, '2': 2, '3': 3, '4': 4, '5': 5, '6': 6, '7': 7, '8': 8, '9': 9, 'O': 10, 'N': 11, 'D': 12}
            traded_items = []
            for (code,) in codes:
                if not code or len(code) != 3:
                    continue
                m_code = code[0]
                day = int(code[1:])
                m_num = month_map.get(m_code)
                if m_num:
                    try:
                        d_val = date(2026, m_num, day)
                        d_iso = d_val.strftime("%Y-%m-%d")
                        d_str = d_val.strftime("%d %b %Y")
                        if not any(x["date"] == d_iso for x in res):
                            is_latest = len(traded_items) == 0
                            traded_items.append({
                                "date": d_iso,
                                "display": f"{d_str} (Traded Historical)",
                                "days_to_expiry": (d_val - today).days,
                                "is_near": False,
                                "is_traded": True,
                            })
                    except Exception:
                        pass
            # Append historical traded sessions after active future expiries
            # so active upcoming expiries (e.g. 27 Oct 2026) are always first and default
            for item in traded_items:
                res.append(item)
    except Exception as e:
        print(f"ContractDataManager: error checking DB expiries: {e}", flush=True)

    return res


def get_option_chain_data(
    underlying: str = "NIFTY",
    expiry: Optional[str] = None,
    spot_price: Optional[float] = None
) -> Dict[str, Any]:
    """
    Build real-time Option Chain for the underlying and expiry date.
    Returns full list of strikes centered around dynamic ATM with live LTPs.
    Falls back to TimescaleDB historical candles when market is closed or contracts expired.
    """
    insts = _load_instruments()
    today = _get_ist_now().date()
    und_upper = underlying.strip().upper()

    # Default expiry to closest active future expiry
    if not expiry:
        exp_list = get_available_expiries(und_upper)
        near_exp = next((x["date"] for x in exp_list if x.get("days_to_expiry", -1) >= 0), None)
        expiry = near_exp if near_exp else (exp_list[0]["date"] if exp_list else today.strftime("%Y-%m-%d"))

    # Determine spot price
    if spot_price is None:
        live_prices = get_all_live_prices()
        spot_entry = live_prices.get(und_upper) or live_prices.get(f"{und_upper} 50") or live_prices.get(f"{und_upper}-I")
        if spot_entry and spot_entry.get("price"):
            spot_price = float(spot_entry["price"])
        else:
            # Try DB futures/index price for the expiry date
            try:
                from sqlalchemy import create_engine, text
                db_engine = create_engine(os.environ.get("DB_URL", "postgresql+psycopg2://postgres:postgres@localhost:5432/trading"))
                with db_engine.connect() as conn:
                    q_spot = text("""
                        SELECT close FROM minute_candles
                        WHERE (symbol = :und OR symbol = :fut OR symbol = :sym50)
                          AND (timestamp AT TIME ZONE 'Asia/Kolkata')::date = :d
                        ORDER BY timestamp DESC LIMIT 1;
                    """)
                    row = conn.execute(q_spot, {"und": und_upper, "fut": f"{und_upper}-I", "sym50": f"{und_upper} 50", "d": expiry}).fetchone()
                    if row and row[0]:
                        spot_price = float(row[0])
            except Exception:
                pass
            if not spot_price:
                spot_price = 22960.0 if und_upper == "NIFTY" else 50000.0
    else:
        live_prices = get_all_live_prices()

    step = 50 if und_upper == "NIFTY" else (100 if "BANK" in und_upper else 50)
    atm_strike = int(round(spot_price / step) * step)

    # Filter instruments for this underlying and expiry
    opts = [
        i for i in insts
        if i.get("name") == und_upper and i.get("segment") == "NFO-OPT" and str(i.get("expiry")) == expiry
    ]

    strike_map: Dict[float, Dict] = {}
    for o in opts:
        stk = float(o["strike"])
        itype = o.get("instrument_type")  # CE or PE
        if stk not in strike_map:
            strike_map[stk] = {
                "strike": stk,
                "is_atm": (int(stk) == atm_strike),
            }

        sym = o.get("tradingsymbol")
        token = o.get("instrument_token")
        live_entry = live_prices.get(sym) or live_prices.get(sym.upper()) if live_prices else None
        ltp = float(live_entry["price"]) if live_entry and live_entry.get("price") else None

        d_str = o["expiry"].strftime("%d %b").upper() if o.get("expiry") else ""
        disp = f"{und_upper} {d_str} {int(stk)} {itype}"

        key = "call" if itype == "CE" else "put"
        strike_map[stk][key] = {
            "symbol": sym,
            "display_name": disp,
            "ltp": ltp,
            "token": token,
        }

    # If active Kite instruments has no records for this expiry (e.g. past/traded date), build from DB
    if not strike_map:
        try:
            from sqlalchemy import create_engine, text
            db_engine = create_engine(os.environ.get("DB_URL", "postgresql+psycopg2://postgres:postgres@localhost:5432/trading"))
            with db_engine.connect() as conn:
                q_db_chain = text("""
                    SELECT symbol,
                           substring(symbol from '([0-9]{5})(CE|PE)$') as strike,
                           substring(symbol from '(CE|PE)$') as itype,
                           close,
                           timestamp
                    FROM minute_candles
                    WHERE (timestamp AT TIME ZONE 'Asia/Kolkata')::date = :d
                      AND symbol ~ ('^' || :und || '.*[0-9]{5}(CE|PE)$')
                    ORDER BY timestamp DESC;
                """)
                rows = conn.execute(q_db_chain, {"d": expiry, "und": und_upper}).fetchall()
                if not rows:
                    q_db_chain2 = text("""
                        SELECT symbol,
                               substring(symbol from '([0-9]{5})(CE|PE)$') as strike,
                               substring(symbol from '(CE|PE)$') as itype,
                               close,
                               timestamp
                        FROM option_minute_candles
                        WHERE (timestamp AT TIME ZONE 'Asia/Kolkata')::date = :d
                          AND symbol ~ ('^' || :und || '.*[0-9]{5}(CE|PE)$')
                        ORDER BY timestamp DESC;
                    """)
                    rows = conn.execute(q_db_chain2, {"d": expiry, "und": und_upper}).fetchall()

                for r in rows:
                    sym, stk_str, itype, close_val, ts = r
                    if not stk_str or not itype:
                        continue
                    stk = float(stk_str)
                    if stk not in strike_map:
                        strike_map[stk] = {
                            "strike": stk,
                            "is_atm": (int(stk) == atm_strike),
                        }
                    key = "call" if itype == "CE" else "put"
                    if key not in strike_map[stk]:
                        strike_map[stk][key] = {
                            "symbol": sym,
                            "display_name": f"{und_upper} {int(stk)} {itype}",
                            "ltp": round(float(close_val), 2) if close_val is not None else None,
                            "token": None,
                        }
        except Exception as e:
            print(f"ContractDataManager: DB option chain fallback error: {e}", flush=True)

    # Fill any missing LTPs (from market closed, pre-market, or historical sessions)
    missing_syms = []
    for s_data in strike_map.values():
        for side in ("call", "put"):
            if side in s_data and s_data[side].get("ltp") is None and s_data[side].get("symbol"):
                missing_syms.append(s_data[side]["symbol"])

    if missing_syms:
        try:
            from sqlalchemy import create_engine, text
            db_engine = create_engine(os.environ.get("DB_URL", "postgresql+psycopg2://postgres:postgres@localhost:5432/trading"))
            with db_engine.connect() as conn:
                q_closes = text("""
                    SELECT DISTINCT ON (symbol) symbol, close
                    FROM minute_candles
                    WHERE symbol = ANY(:syms)
                    ORDER BY symbol, timestamp DESC;
                """)
                c_rows = conn.execute(q_closes, {"syms": missing_syms}).fetchall()
                c_map = {r[0]: round(float(r[1]), 2) for r in c_rows if r[1] is not None}

                rem_syms = [s for s in missing_syms if s not in c_map]
                if rem_syms:
                    q_closes2 = text("""
                        SELECT DISTINCT ON (symbol) symbol, close
                        FROM option_minute_candles
                        WHERE symbol = ANY(:syms)
                        ORDER BY symbol, timestamp DESC;
                    """)
                    c_rows2 = conn.execute(q_closes2, {"syms": rem_syms}).fetchall()
                    for r in c_rows2:
                        if r[1] is not None:
                            c_map[r[0]] = round(float(r[1]), 2)

                for s_data in strike_map.values():
                    for side in ("call", "put"):
                        if side in s_data and s_data[side].get("ltp") is None:
                            sym = s_data[side].get("symbol")
                            if sym in c_map:
                                s_data[side]["ltp"] = c_map[sym]
        except Exception as e:
            print(f"ContractDataManager: error filling missing LTPs from DB: {e}", flush=True)

    sorted_strikes = sorted(strike_map.keys())
    if sorted_strikes:
        # Center around ATM strike (±15 strikes)
        atm_idx = min(range(len(sorted_strikes)), key=lambda idx: abs(sorted_strikes[idx] - atm_strike))
        start_idx = max(0, atm_idx - 15)
        end_idx = min(len(sorted_strikes), atm_idx + 16)
        chain = [strike_map[s] for s in sorted_strikes[start_idx:end_idx]]
    else:
        chain = []

    return {
        "underlying": und_upper,
        "expiry": expiry,
        "spot": round(spot_price, 2),
        "atm_strike": atm_strike,
        "total_strikes": len(sorted_strikes),
        "chain": chain,
    }


# ── Symbol Search ─────────────────────────────────────────────────────────────

MONTH_CODE_MAP = {'1': 'JAN', '2': 'FEB', '3': 'MAR', '4': 'APR', '5': 'MAY', '6': 'JUN', '7': 'JUL', '8': 'AUG', '9': 'SEP', 'O': 'OCT', 'N': 'NOV', 'D': 'DEC'}

def parse_option_symbol(sym: str) -> Optional[Dict[str, Any]]:
    """Parse NSE option symbol into structured dictionary."""
    import re
    # Weekly: NIFTY26O1922950PE
    m = re.match(r'^([A-Z]+)(\d{2})([1-9OND])(\d{2})(\d{5})(CE|PE)$', sym)
    if m:
        und, yr, m_code, day, stk, itype = m.groups()
        mon = MONTH_CODE_MAP.get(m_code, m_code)
        m_idx = list(MONTH_CODE_MAP.keys()).index(m_code) + 1 if m_code in MONTH_CODE_MAP else 10
        disp = f"{und} {int(day)} {mon} {int(stk)} {itype}"
        return {
            "symbol": sym,
            "display_name": f"{disp} (Traded)",
            "underlying": und,
            "strike": float(stk),
            "option_type": itype,
            "expiry": f"20{yr}-{m_idx:02d}-{int(day):02d}",
            "segment": "NFO-OPT",
            "is_traded": True,
        }
    # Monthly: NIFTY26SEP22950PE
    m2 = re.match(r'^([A-Z]+)(\d{2})([A-Z]{3})(\d{5})(CE|PE)$', sym)
    if m2:
        und, yr, mon, stk, itype = m2.groups()
        disp = f"{und} {mon} {int(stk)} {itype}"
        return {
            "symbol": sym,
            "display_name": f"{disp} (Traded)",
            "underlying": und,
            "strike": float(stk),
            "option_type": itype,
            "expiry": None,
            "segment": "NFO-OPT",
            "is_traded": True,
        }
    return None


def search_contracts(
    query: str,
    expiry: Optional[str] = None,
    limit: int = 15,
    db_engine=None
) -> List[Dict]:
    """
    Search DB traded contracts, active Kite Connect instruments (including weekly/monthly options and futures)
    plus known indices. Supports filtering by expiry and multi-token queries.
    """
    q = query.strip().upper()
    tokens = q.split()
    results = []
    seen_syms = set()
    today = _get_ist_now().date()
    
    # ── 1. Built-in indices always available ──
    indices = [
        {"symbol": "NIFTY 50",   "display_name": "NIFTY 50 (Spot)",    "underlying": "NIFTY",     "segment": "NSE-INDEX"},
        {"symbol": "NIFTY BANK", "display_name": "BANKNIFTY (Spot)",   "underlying": "BANKNIFTY", "segment": "NSE-INDEX"},
        {"symbol": "NIFTY-I",    "display_name": "NIFTY-I (Futures)",  "underlying": "NIFTY",     "segment": "NFO-FUT"},
    ]
    for idx in indices:
        if not expiry:
            if all(t in idx["display_name"].upper() or t in idx["symbol"].upper() or t in idx["underlying"].upper() for t in tokens):
                results.append(idx)
                seen_syms.add(idx["symbol"])

    # ── 2. Search Active Kite Instruments (Primary Active Contracts) ──
    insts = _load_instruments()
    if insts:
        opt_results = []
        for i in insts:
            seg = i.get("segment")
            if expiry:
                if seg not in ("NFO-OPT", "NFO-FUT"):
                    continue
            else:
                if seg not in ("NFO-OPT", "NFO-FUT", "NSE"):
                    continue

            exp = i.get("expiry")
            # Filter expired contracts
            if exp and exp < today:
                continue

            exp_str = str(exp) if exp else None
            if expiry and exp_str != expiry:
                continue

            underlying = i.get("name") or ""
            tsym = i.get("tradingsymbol") or ""
            if tsym in seen_syms:
                continue

            stk = i.get("strike")
            itype = i.get("instrument_type") or ""

            # Format clean human-readable display name
            if seg == "NFO-OPT" and exp and stk:
                d_str = exp.strftime("%d %b").upper()
                display = f"{underlying} {d_str} {int(stk)} {itype}"
            elif seg == "NFO-FUT" and exp:
                d_str = exp.strftime("%b").upper()
                display = f"{underlying} {d_str} FUT"
            else:
                display = tsym

            # Match all tokens
            search_target = f"{tsym} {display} {underlying} {exp_str or ''}".upper()
            if all(t in search_target for t in tokens):
                # Prioritize primary indices (NIFTY, BANKNIFTY)
                is_core = 0 if underlying in ("NIFTY", "BANKNIFTY", "FINNIFTY") else 1
                opt_results.append({
                    "symbol": tsym,
                    "display_name": display,
                    "underlying": underlying,
                    "strike": float(stk) if stk else None,
                    "option_type": itype,
                    "expiry": exp_str,
                    "segment": seg,
                    "instrument_token": i.get("instrument_token"),
                    "_core": is_core,
                    "_dist": abs((stk or 22950) - 22950),
                    "_exp": exp or today,
                })

        # Sort by core underlying, nearest expiry, then strike distance to spot
        opt_results.sort(key=lambda x: (x["_core"], x["_exp"], x["_dist"]))
        for r in opt_results:
            clean_r = {k: v for k, v in r.items() if not k.startswith("_")}
            results.append(clean_r)
            seen_syms.add(clean_r["symbol"])
            if len(results) >= limit:
                return results[:limit]

    # ── 3. Search Historical Traded Contracts in DB (minute_candles) as Fallback ──
    if db_engine is None:
        try:
            from sqlalchemy import create_engine
            db_engine = create_engine(os.environ.get("DB_URL", "postgresql+psycopg2://postgres:postgres@localhost:5432/trading"))
        except Exception:
            pass

    if db_engine is not None and tokens and len(results) < limit:
        try:
            from sqlalchemy import text
            conds = ' AND '.join([f'symbol ILIKE :t{i}' for i in range(len(tokens))])
            sql_traded = text(f"""
                SELECT DISTINCT symbol 
                FROM minute_candles 
                WHERE symbol ~ '^[A-Z]+[0-9]+' AND {conds} 
                ORDER BY symbol DESC 
                LIMIT 25;
            """)
            params = {f"t{i}": f"%{t}%" for i, t in enumerate(tokens)}
            with db_engine.connect() as conn:
                db_rows = conn.execute(sql_traded, params).fetchall()

            db_parsed = []
            for (sym_val,) in db_rows:
                if sym_val in seen_syms:
                    continue
                parsed = parse_option_symbol(sym_val)
                if parsed:
                    if expiry and parsed.get("expiry") and parsed["expiry"] != expiry:
                        continue
                    db_parsed.append(parsed)
                    seen_syms.add(sym_val)

            # Sort traded contracts by expiry descending (latest first)
            db_parsed.sort(key=lambda x: (x.get("expiry") or "2026-01-01"), reverse=True)
            for item in db_parsed:
                results.append(item)
                if len(results) >= limit:
                    break
        except Exception as e:
            print(f"ContractDataManager: DB traded search error: {e}", flush=True)

    return results[:limit]

    return results[:limit]


# ── Historical Catch-Up ───────────────────────────────────────────────────────

def fetch_contract_candles(
    trading_symbol: str,
    target_date: Optional[str] = None,
    db_engine=None
) -> Dict[str, Any]:
    """
    Fetch 1-minute candles for the given symbol.
    If target_date is specified, loads for that date.
    If market is closed (weekend, holiday, pre-market) or today has 0 candles:
    Automatically falls back to the most recent available trading day's data!
    """
    today_str = get_today_str()
    now_ist = _get_ist_now()
    is_weekend = now_ist.weekday() >= 5

    # 1. Resolve DB Engine
    if db_engine is None:
        try:
            from sqlalchemy import create_engine
            db_engine = create_engine(os.environ.get("DB_URL", "postgresql+psycopg2://postgres:postgres@localhost:5432/trading"))
        except Exception:
            pass

    active_date = target_date if (target_date and target_date != "today") else None
    is_last_day = False

    # If target_date is not specified and it's a weekday, check if today has candles in DB
    if not active_date and not is_weekend and db_engine is not None:
        try:
            from sqlalchemy import text
            with db_engine.connect() as conn:
                q_cnt = text("""
                    SELECT count(*) FROM minute_candles
                    WHERE symbol = :sym AND (timestamp AT TIME ZONE 'Asia/Kolkata')::date = :today
                """)
                cnt = conn.execute(q_cnt, {"sym": trading_symbol, "today": today_str}).scalar()
                if cnt and cnt > 0:
                    active_date = today_str
        except Exception as e:
            print(f"ContractDataManager: error checking today's candles: {e}", flush=True)

    # If active_date is still None (weekend, holiday, or today has 0 candles), fallback to latest trading day
    if not active_date and db_engine is not None:
        try:
            from sqlalchemy import text
            with db_engine.connect() as conn:
                q_max = text("""
                    SELECT max((timestamp AT TIME ZONE 'Asia/Kolkata')::date)
                    FROM minute_candles WHERE symbol = :sym
                """)
                max_d = conn.execute(q_max, {"sym": trading_symbol}).scalar()
                if not max_d:
                    q_max2 = text("""
                        SELECT max((timestamp AT TIME ZONE 'Asia/Kolkata')::date)
                        FROM option_minute_candles WHERE symbol = :sym
                    """)
                    max_d = conn.execute(q_max2, {"sym": trading_symbol}).scalar()
                if max_d:
                    active_date = str(max_d)
                    is_last_day = True
        except Exception as e:
            print(f"ContractDataManager: error finding latest date for {trading_symbol}: {e}", flush=True)

    if not active_date:
        active_date = today_str

    # 2. Query candles for active_date from DB
    candles = []
    if db_engine is not None:
        try:
            from sqlalchemy import text
            import pandas as pd
            with db_engine.connect() as conn:
                for tbl in ["minute_candles", "option_minute_candles"]:
                    sql = text(f"""
                        SELECT timestamp, open, high, low, close, volume
                        FROM {tbl}
                        WHERE symbol = :sym
                          AND (timestamp AT TIME ZONE 'Asia/Kolkata')::date = :d
                        ORDER BY timestamp ASC
                    """)
                    df = pd.read_sql(sql, conn, params={"sym": trading_symbol, "d": active_date})
                    if not df.empty:
                        candles = _df_to_candle_list(df)
                        break
        except Exception as e:
            print(f"ContractDataManager: DB candle fetch error for {trading_symbol}: {e}", flush=True)

    # 3. If DB has no candles and active_date is today on a weekday, try Kite
    if not candles and active_date == today_str and not is_weekend:
        candles = _fetch_from_kite(trading_symbol, db_engine)

    market_status = "OPEN" if is_market_open() else "CLOSED"
    return {
        "symbol": trading_symbol,
        "date": active_date,
        "is_last_day_data": is_last_day,
        "market_status": market_status,
        "num_candles": len(candles),
        "candles": candles,
    }


def fetch_today_candles(trading_symbol: str, db_engine=None) -> List[Dict]:
    """
    Fetch today's 1-minute candles for the given symbol (falls back to last trading day if market is closed).
    """
    res = fetch_contract_candles(trading_symbol, db_engine=db_engine)
    return res.get("candles", [])


def _fetch_from_kite(trading_symbol: str, db_engine=None) -> List[Dict]:
    """Fetch today's 1-minute candles from Kite Connect historical API."""
    try:
        kite = _get_kite()
        if not kite:
            return []
        
        token, exchange = resolve_token_for_kite_symbol(trading_symbol)
        if not token:
            print(f"ContractDataManager: No token found for {trading_symbol}", flush=True)
            return []
        
        today = _get_ist_now().date()
        try:
            from zoneinfo import ZoneInfo
            ist = ZoneInfo("Asia/Kolkata")
        except Exception:
            import pytz
            ist = pytz.timezone("Asia/Kolkata")
        
        from_dt = datetime(today.year, today.month, today.day, 9, 15, 0)
        to_dt = datetime.now()
        
        print(f"ContractDataManager: Fetching Kite historical for {trading_symbol} (token={token}) from {from_dt} to {to_dt}", flush=True)
        
        hist = kite.historical_data(
            instrument_token=token,
            from_date=from_dt,
            to_date=to_dt,
            interval="minute"
        )
        
        if not hist:
            return []
        
        candles = []
        for bar in hist:
            dt = bar["date"]
            if hasattr(dt, "timestamp"):
                ts = int(dt.timestamp())
            else:
                ts = int(datetime.strptime(str(dt), "%Y-%m-%d %H:%M:%S").timestamp())
            
            candles.append({
                "time": dt.strftime("%Y-%m-%d %H:%M") if hasattr(dt, "strftime") else str(dt),
                "timestamp": ts,
                "open": float(bar.get("open", 0)),
                "high": float(bar.get("high", 0)),
                "low": float(bar.get("low", 0)),
                "close": float(bar.get("close", 0)),
                "volume": int(bar.get("volume", 0)),
            })
        
        print(f"ContractDataManager: Got {len(candles)} Kite candles for {trading_symbol}", flush=True)
        return candles
    
    except Exception as e:
        print(f"ContractDataManager: Kite historical fetch error for {trading_symbol}: {e}", flush=True)
        return []


def _df_to_candle_list(df) -> List[Dict]:
    """Convert a pandas DataFrame with timestamp, open, high, low, close, volume to candle list."""
    candles = []
    for _, row in df.iterrows():
        ts = row["timestamp"]
        if hasattr(ts, "timestamp"):
            unix_ts = int(ts.timestamp())
            time_str = ts.strftime("%Y-%m-%d %H:%M")
        else:
            unix_ts = int(ts)
            time_str = str(ts)
        candles.append({
            "time": time_str,
            "timestamp": unix_ts,
            "open": float(row.get("open", 0)),
            "high": float(row.get("high", 0)),
            "low": float(row.get("low", 0)),
            "close": float(row.get("close", 0)),
            "volume": int(row.get("volume", 0)),
        })
    return candles


# ── Live Price from Cache ─────────────────────────────────────────────────────

LIVE_CACHE_FILE = "/tmp/td_live_prices.json"


def get_all_live_prices() -> Dict[str, Dict]:
    """Return live prices — disk cache merged with instantaneous in-memory bus updates."""
    merged: Dict[str, Dict] = {}
    try:
        if os.path.exists(LIVE_CACHE_FILE):
            mtime = os.path.getmtime(LIVE_CACHE_FILE)
            if time.time() - mtime <= 120:
                with open(LIVE_CACHE_FILE, "r") as f:
                    merged = json.load(f)
    except Exception:
        pass
    with _price_bus_lock:
        merged.update(_live_price_bus)
    return merged


def get_live_price(symbol: str) -> Optional[Dict]:
    """Read live price — prefers in-memory bus, falls back to disk file."""
    # Fast path: check in-memory bus first
    bus_price = get_live_price_from_bus(symbol)
    if bus_price is not None:
        return {"price": bus_price}
    # Fallback: disk file
    cache = get_all_live_prices()
    return cache.get(symbol) or cache.get(symbol.upper())


def get_live_ltp(symbol: str) -> Optional[float]:
    """Return the live LTP — prefers in-memory bus for zero-latency reads."""
    # Fast path: in-memory bus
    bus_price = get_live_price_from_bus(symbol)
    if bus_price is not None:
        return bus_price
    # Fallback: disk file entry
    entry = get_live_price(symbol)
    if entry and entry.get("price"):
        return float(entry["price"])
    return None


# ── Forming Candle per Symbol ─────────────────────────────────────────────────

def update_forming_candle(symbol: str, price: float) -> Optional[Dict]:
    """Update the forming (current-minute) candle for a symbol and return it."""
    now = datetime.now()
    now_min = now.replace(second=0, microsecond=0)
    now_min_ts = int(now_min.timestamp())
    
    with _lock:
        fc = _forming_candles.get(symbol)
        if fc is None or fc["time"] != now_min_ts:
            _forming_candles[symbol] = {
                "time": now_min_ts,
                "open": price,
                "high": price,
                "low": price,
                "close": price,
                "volume": 0,
            }
        else:
            fc["high"] = max(fc["high"], price)
            fc["low"] = min(fc["low"], price)
            fc["close"] = price
        
        return dict(_forming_candles[symbol])


def get_forming_candle(symbol: str) -> Optional[Dict]:
    with _lock:
        return dict(_forming_candles[symbol]) if symbol in _forming_candles else None


# ── WebSocket client registry ─────────────────────────────────────────────────

# symbol -> list of WebSocket connections
_contract_ws_clients: Dict[str, List] = {}


def register_contract_client(symbol: str, ws) -> None:
    with _lock:
        if symbol not in _contract_ws_clients:
            _contract_ws_clients[symbol] = []
        _contract_ws_clients[symbol].append(ws)


def unregister_contract_client(symbol: str, ws) -> None:
    with _lock:
        if symbol in _contract_ws_clients:
            try:
                _contract_ws_clients[symbol].remove(ws)
            except ValueError:
                pass


def get_contract_clients(symbol: str) -> List:
    with _lock:
        return list(_contract_ws_clients.get(symbol, []))


def get_all_subscribed_symbols() -> List[str]:
    with _lock:
        return [s for s, clients in _contract_ws_clients.items() if clients]


# ── Live Order Flow & Market Depth Quote ──────────────────────────────────────

_quote_cache: Dict[str, Tuple[float, Dict]] = {}
QUOTE_CACHE_TTL = 1.0  # 1 second TTL to prevent Kite API rate limiting

def get_contract_quote(trading_symbol: str) -> Optional[Dict]:
    """
    Fetch real-time Level 2 depth and order flow metrics:
    - total buy_quantity (Bid Depth)
    - total sell_quantity (Offer Depth)
    - bid_ask_ratio (Buyer vs Seller dominance)
    - top 5 bids and top 5 asks
    - volume, open interest, and day range
    """
    now = time.time()
    sym_upper = trading_symbol.strip().upper()
    
    # Check 1s cache
    if sym_upper in _quote_cache:
        cached_ts, cached_val = _quote_cache[sym_upper]
        if now - cached_ts < QUOTE_CACHE_TTL:
            return cached_val
            
    kite = _get_kite()
    if not kite:
        return None
        
    try:
        # Determine prefix (NFO for options & futures, NSE for equities/indices)
        is_nfo = any(c.isdigit() for c in sym_upper) or sym_upper.endswith("-I") or sym_upper.endswith("-II")
        prefix = "NFO:" if is_nfo else "NSE:"
        key = f"{prefix}{sym_upper}"
        
        quotes = kite.quote([key])
        if key in quotes:
            q = quotes[key]
            depth = q.get("depth", {})
            buy_depth = depth.get("buy", [])
            sell_depth = depth.get("sell", [])
            
            total_buy_qty = int(q.get("buy_quantity") or sum(b.get("quantity", 0) for b in buy_depth) or 0)
            total_sell_qty = int(q.get("sell_quantity") or sum(s.get("quantity", 0) for s in sell_depth) or 0)
            
            total_flow = total_buy_qty + total_sell_qty
            buy_pct = round((total_buy_qty / total_flow * 100), 1) if total_flow > 0 else 50.0
            sell_pct = round((total_sell_qty / total_flow * 100), 1) if total_flow > 0 else 50.0
            ratio = round(total_buy_qty / max(total_sell_qty, 1), 2)
            
            res = {
                "symbol": sym_upper,
                "last_price": float(q.get("last_price", 0.0)),
                "volume": int(q.get("volume", 0)),
                "oi": int(q.get("oi", 0)),
                "buy_quantity": total_buy_qty,
                "sell_quantity": total_sell_qty,
                "buy_pct": buy_pct,
                "sell_pct": sell_pct,
                "bid_ask_ratio": ratio,
                "depth": depth,
                "ohlc": q.get("ohlc", {}),
                "timestamp": datetime.now().isoformat(),
            }
            _quote_cache[sym_upper] = (now, res)
            return res
    except Exception as e:
        print(f"ContractDataManager: quote error for {sym_upper}: {e}", flush=True)
    return None

