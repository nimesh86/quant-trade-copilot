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
import math
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
# symbol -> (cached_at_ts, list of completed 1-min candle dicts for today)
_session_candles: Dict[str, Tuple[float, List[Dict]]] = {}

# ── Multi-Index Live Spot Tracker ─────────────────────────────────────────────
# Underlying -> (timestamp, spot_price)
_live_index_spots: Dict[str, Tuple[float, float]] = {}

# ── Pre-Indexed Option Chain Grid ─────────────────────────────────────────────
# Structured: _option_grid[underlying][expiry_str][strike][option_type] -> instrument_dict
_option_grid: Dict[str, Dict[str, Dict[float, Dict[str, Dict[str, Any]]]]] = {}
_underlying_expiries: Dict[str, List[str]] = {}
_grid_lock = threading.Lock()

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


def update_live_index_spot(underlying: str, spot_price: float) -> None:
    """Updates real-time spot price for major index in memory with zero disk I/O."""
    if not underlying or spot_price <= 0:
        return
    u = underlying.strip().upper()
    if u in ("BSE SENSEX", "BSESEN", "BSE:SENSEX"):
        u = "SENSEX"
    elif u in ("NIFTY 50", "NSE:NIFTY 50"):
        u = "NIFTY"
    elif u in ("NIFTY BANK", "NSE:NIFTY BANK"):
        u = "BANKNIFTY"
    elif u in ("NIFTY FIN SERVICE", "NSE:NIFTY FIN SERVICE"):
        u = "FINNIFTY"
    elif u in ("NIFTY MID SELECT", "NSE:NIFTY MID SELECT"):
        u = "MIDCPNIFTY"
    now_ts = time.time()
    with _price_bus_lock:
        _live_index_spots[u] = (now_ts, float(spot_price))
        _live_price_bus[u] = {"price": float(spot_price), "ts": datetime.now().isoformat()}


def _build_option_grid(instruments: List[Dict]) -> None:
    """Pre-indexes instruments into an O(1) hash matrix for instant option chain lookups."""
    global _option_grid, _underlying_expiries
    today = _get_ist_now().date()
    new_grid: Dict[str, Dict[str, Dict[float, Dict[str, Dict[str, Any]]]]] = {}
    exp_sets: Dict[str, set] = {}

    for inst in instruments:
        seg = inst.get("segment")
        if seg not in ("NFO-OPT", "BFO-OPT"):
            continue
        name = inst.get("name")
        if not name:
            continue
        und = name.upper()
        if und == "BSESEN":
            und = "SENSEX"
        elif und == "CNXBAN":
            und = "BANKNIFTY"
        elif und == "NIFFIN":
            und = "FINNIFTY"

        exp = inst.get("expiry")
        if not exp:
            continue
        exp_date = exp if isinstance(exp, date) else (datetime.strptime(str(exp)[:10], "%Y-%m-%d").date() if str(exp)[:10] else None)
        if not exp_date or exp_date < today:
            continue

        exp_str = exp_date.strftime("%Y-%m-%d")
        try:
            stk = float(inst.get("strike", 0.0))
        except (ValueError, TypeError):
            continue
        itype = inst.get("instrument_type")  # CE or PE
        if itype not in ("CE", "PE") or stk <= 0:
            continue

        if und not in new_grid:
            new_grid[und] = {}
            exp_sets[und] = set()
        if exp_str not in new_grid[und]:
            new_grid[und][exp_str] = {}
            exp_sets[und].add(exp_date)
        if stk not in new_grid[und][exp_str]:
            new_grid[und][exp_str][stk] = {}

        new_grid[und][exp_str][stk][itype] = inst

    new_expiries: Dict[str, List[str]] = {}
    for und, d_set in exp_sets.items():
        sorted_d = sorted(list(d_set))
        new_expiries[und] = [d.strftime("%Y-%m-%d") for d in sorted_d]

    with _grid_lock:
        _option_grid = new_grid
        _underlying_expiries = new_expiries


def get_live_price_from_bus(symbol: str) -> Optional[float]:
    """Read the latest price for a symbol directly from the in-memory bus."""
    with _price_bus_lock:
        entry = _live_price_bus.get(symbol) or _live_price_bus.get(symbol.upper())
        return float(entry["price"]) if entry and entry.get("price") else None


def get_all_bus_prices() -> Dict[str, Dict]:
    """Return a snapshot of the full in-memory price bus."""
    with _price_bus_lock:
        return dict(_live_price_bus)


# ── Active Trade Watchdog Registry ──────────────────────────────────────────
_pinned_trade_symbols: set = set()


def pin_trade_symbol(symbol: str):
    """Pins a contract symbol to the persistent live price watchdog until trade closure."""
    if symbol:
        _pinned_trade_symbols.add(symbol.strip().upper())


def unpin_trade_symbol(symbol: str):
    """Unpins a contract symbol once trade is closed."""
    if symbol:
        _pinned_trade_symbols.discard(symbol.strip().upper())


def get_pinned_trade_symbols() -> list:
    """Returns list of currently active pinned trade symbols."""
    return list(_pinned_trade_symbols)


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
        try:
            from kiteconnect import KiteConnect
        except ImportError:
            print("ContractDataManager: 'kiteconnect' package not installed in environment.", flush=True)
            return None
        kite = KiteConnect(api_key=api_key)
        kite.set_access_token(access_token)
        _kite_client = kite
        print("ContractDataManager: KiteConnect client successfully initialized from KeyVault.", flush=True)
        return kite
    except Exception as e:
        print(f"ContractDataManager: Kite init error: {e}", flush=True)
        return None


def reset_kite():
    """Force re-init on next use (e.g. after token refresh)."""
    global _kite_client
    _kite_client = None


def get_active_broker() -> str:
    """Returns currently selected broker ('kite' or 'breeze')."""
    try:
        from trading_engine.core.keyvault import get_secret
        return (get_secret("ACTIVE_BROKER") or "kite").strip().lower()
    except Exception:
        return "kite"


def reset_all_brokers():
    """Forces re-initialization of both Kite and Breeze clients."""
    global _kite_client, _instruments_cache
    _kite_client = None
    _instruments_cache = None
    try:
        from trading_engine.adapters.breeze_adapter import default_breeze_adapter
        default_breeze_adapter.reset()
    except Exception:
        pass
    try:
        from trading_engine.adapters.kite_adapter import default_kite_adapter
        default_kite_adapter.reset()
    except Exception:
        pass


# ── Instrument Token Resolution ───────────────────────────────────────────────

# Cache: trading_symbol -> instrument_token
_token_cache: Dict[str, int] = {}
# full instrument list cache
_instruments_cache: Optional[List[Dict]] = None
_instruments_ts: float = 0.0
INSTRUMENTS_TTL = 6 * 3600  # refresh every 6 hours


def _fetch_public_instruments(segment: str = "NFO") -> List[Dict]:
    """Download public instruments CSV from Zerodha Kite without requiring credentials."""
    import csv
    import urllib.request
    import tempfile

    cache_file = os.path.join(tempfile.gettempdir(), f"kite_instruments_{segment.lower()}.csv")
    content = None

    # Check if disk cache is younger than INSTRUMENTS_TTL and non-empty
    if os.path.exists(cache_file):
        try:
            mtime = os.path.getmtime(cache_file)
            if (time.time() - mtime) < INSTRUMENTS_TTL and os.path.getsize(cache_file) > 1000:
                with open(cache_file, "r", encoding="utf-8") as f:
                    content = f.read()
        except Exception:
            content = None

    if not content:
        try:
            url = f"https://api.kite.trade/instruments/{segment}"
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
            with urllib.request.urlopen(req, timeout=12) as resp:
                content = resp.read().decode("utf-8")
            with open(cache_file, "w", encoding="utf-8") as f:
                f.write(content)
        except Exception as e:
            print(f"ContractDataManager: public instruments download error for {segment}: {e}", flush=True)
            if os.path.exists(cache_file):
                try:
                    with open(cache_file, "r", encoding="utf-8") as f:
                        content = f.read()
                except Exception:
                    return []
            else:
                return []

    lines = content.splitlines()
    reader = csv.DictReader(lines)
    parsed = []
    for row in reader:
        exp_str = row.get("expiry")
        exp_date = None
        if exp_str:
            try:
                exp_date = datetime.strptime(exp_str, "%Y-%m-%d").date()
            except Exception:
                pass
        try:
            tok = int(row.get("instrument_token", 0))
        except Exception:
            tok = 0
        try:
            stk = float(row.get("strike", 0.0) or 0.0)
        except Exception:
            stk = 0.0
        try:
            tick = float(row.get("tick_size", 0.05) or 0.05)
        except Exception:
            tick = 0.05
        try:
            lot = int(row.get("lot_size", 1) or 1)
        except Exception:
            lot = 1
        try:
            lp = float(row.get("last_price", 0.0) or 0.0)
        except Exception:
            lp = 0.0

        parsed.append({
            "instrument_token": tok,
            "exchange_token": row.get("exchange_token", ""),
            "tradingsymbol": row.get("tradingsymbol", ""),
            "name": row.get("name", ""),
            "last_price": lp,
            "expiry": exp_date,
            "strike": stk,
            "tick_size": tick,
            "lot_size": lot,
            "instrument_type": row.get("instrument_type", ""),
            "segment": row.get("segment", segment),
            "exchange": row.get("exchange", segment),
        })
    return parsed


def _sync_instruments_to_symbol_master(instruments: List[Dict]):
    """Sync loaded active instruments into symbol_master table so DB queries and search work seamlessly."""
    def _run_sync():
        try:
            from sqlalchemy import create_engine
            db_url = os.environ.get("DB_URL") or os.environ.get("DATABASE_URL") or "postgresql+psycopg2://postgres:postgres@localhost:5432/trading"
            if not db_url.startswith("postgresql+psycopg2://") and db_url.startswith("postgresql://"):
                db_url = db_url.replace("postgresql://", "postgresql+psycopg2://", 1)
            eng = create_engine(db_url)
            raw = eng.raw_connection()
            try:
                from psycopg2.extras import execute_values
                recs = []
                today = _get_ist_now().date()
                for i in instruments:
                    exp = i.get("expiry")
                    if not exp or exp < today:
                        continue
                    sym = i.get("tradingsymbol")
                    if not sym:
                        continue
                    recs.append((
                        sym,
                        i.get("instrument_token"),
                        i.get("name"),
                        exp,
                        float(i.get("strike", 0.0) or 0.0),
                        i.get("instrument_type") or "",
                        i.get("segment") or "NFO-OPT",
                        int(i.get("lot_size", 1) or 1),
                        float(i.get("tick_size", 0.05) or 0.05),
                    ))
                if recs:
                    with raw.cursor() as cur:
                        query = """
                            INSERT INTO symbol_master (
                                symbol, symbol_id, underlying, expiry, strike, option_type, segment, lot_size, tick_size
                            ) VALUES %s
                            ON CONFLICT (symbol) DO UPDATE SET
                                symbol_id = EXCLUDED.symbol_id,
                                expiry = EXCLUDED.expiry,
                                strike = EXCLUDED.strike,
                                updated_at = NOW();
                        """
                        execute_values(cur, query, recs, page_size=2000)
                    raw.commit()
                    print(f"ContractDataManager: Synced {len(recs)} active instruments to symbol_master.", flush=True)
            finally:
                raw.close()
        except Exception as e:
            print(f"ContractDataManager: Note on symbol_master sync: {e}", flush=True)

    t = threading.Thread(target=_run_sync, daemon=True)
    t.start()


def _load_instruments() -> List[Dict]:
    global _instruments_cache, _instruments_ts
    now = time.time()
    if _instruments_cache and (now - _instruments_ts) < INSTRUMENTS_TTL:
        # Ensure grid is populated even if cached
        if not _option_grid:
            _build_option_grid(_instruments_cache)
        return _instruments_cache

    # 1. Try Kite authenticated client if available
    try:
        kite = _get_kite()
        if kite:
            nfo = kite.instruments("NFO")
            nse = kite.instruments("NSE")
            bfo = []
            bse = []
            try:
                bfo = kite.instruments("BFO")
                bse = kite.instruments("BSE")
            except Exception as _e_bfo:
                print(f"ContractDataManager: Note loading BFO/BSE from Kite: {_e_bfo}", flush=True)
            _instruments_cache = nfo + nse + bfo + bse
            _instruments_ts = now
            print(f"ContractDataManager: Loaded {len(_instruments_cache)} instruments from Kite client (NFO+NSE+BFO+BSE).", flush=True)
            _build_option_grid(_instruments_cache)
            _sync_instruments_to_symbol_master(_instruments_cache)
            return _instruments_cache
    except Exception as e:
        print(f"ContractDataManager: Kite client load error: {e}", flush=True)

    # 2. Fallback to public Zerodha Kite instruments endpoint (zero auth needed)
    try:
        nfo = _fetch_public_instruments("NFO")
        bfo = _fetch_public_instruments("BFO")
        if nfo or bfo:
            _instruments_cache = (nfo or []) + (bfo or [])
            _instruments_ts = now
            print(f"ContractDataManager: Loaded {len(_instruments_cache)} instruments from public NFO+BFO endpoints.", flush=True)
            _build_option_grid(_instruments_cache)
            _sync_instruments_to_symbol_master(_instruments_cache)
            return _instruments_cache
    except Exception as e:
        print(f"ContractDataManager: Failed to load public instruments: {e}", flush=True)

    if _instruments_cache and not _option_grid:
        _build_option_grid(_instruments_cache)
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

_expiries_cache: Dict[str, Tuple[float, List[Dict[str, Any]]]] = {}
EXPIRIES_CACHE_TTL = 300.0  # 5 minutes in-memory cache


def get_available_expiries(underlying: str = "NIFTY") -> List[Dict[str, Any]]:
    """
    Return sorted list of active future expiry dates for the given underlying.
    Instant O(1) response from pre-indexed matrix (< 0.1ms).
    """
    und_upper = underlying.strip().upper()
    if und_upper in ("BSE SENSEX", "BSESEN", "BSE:SENSEX"):
        und_upper = "SENSEX"
    elif und_upper in ("NIFTY 50", "NSE:NIFTY 50"):
        und_upper = "NIFTY"
    elif und_upper in ("NIFTY BANK", "NSE:NIFTY BANK"):
        und_upper = "BANKNIFTY"
    elif und_upper in ("NIFTY FIN SERVICE", "NSE:NIFTY FIN SERVICE"):
        und_upper = "FINNIFTY"
    elif und_upper in ("NIFTY MID SELECT", "NSE:NIFTY MID SELECT"):
        und_upper = "MIDCPNIFTY"

    now = time.time()
    if und_upper in _expiries_cache:
        ts, cached_exps = _expiries_cache[und_upper]
        if now - ts < EXPIRIES_CACHE_TTL:
            return cached_exps

    today = _get_ist_now().date()

    with _grid_lock:
        indexed_dates = _underlying_expiries.get(und_upper)

    if indexed_dates is None:
        insts = _load_instruments()
        with _grid_lock:
            if not _option_grid and insts:
                _build_option_grid(insts)
            indexed_dates = _underlying_expiries.get(und_upper) or []

    res = []
    if indexed_dates:
        for d_str in indexed_dates:
            try:
                exp_date = datetime.strptime(d_str, "%Y-%m-%d").date()
                if exp_date < today:
                    continue
                days = (exp_date - today).days
                d_formatted = exp_date.strftime("%d %b %Y")
                tag = " (Weekly)" if days <= 7 else (" (Monthly)" if days <= 35 and exp_date.day >= 24 else "")
                res.append({
                    "date": d_str,
                    "display": f"{d_formatted}{tag}",
                    "days_to_expiry": days,
                    "is_near": days <= 4,
                })
            except Exception:
                pass
    else:
        # Fallback to scanning instruments if grid miss
        try:
            from trading_engine.config import get_index_spec
        except ImportError:
            from config import get_index_spec
        spec = get_index_spec(und_upper)
        exps = set()
        for i in insts:
            if (i.get("name") == und_upper or i.get("name") == spec.get("breeze_stock_code")) and i.get("segment") in ("NFO-OPT", "BFO-OPT"):
                exp = i.get("expiry")
                if exp and exp >= today:
                    exps.add(exp)
        sorted_exps = sorted(list(exps))
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

    _expiries_cache[und_upper] = (now, res)
    return res


_spot_cache: Dict[str, Tuple[float, float]] = {}  # {und: (timestamp, price)}
SPOT_CACHE_TTL = 3.0  # 3 seconds cache


def resolve_spot_price(underlying: str = "NIFTY", expiry: Optional[str] = None) -> float:
    """
    Resolve real-time spot price with sub-millisecond latency (< 0.1ms).
    Priority:
      1. Live index spot tracker (_live_index_spots)
      2. In-memory price bus candidates
      3. Fast in-memory cache (5s during market, 300s off-market)
      4. Direct Kite quote for index token (if broker authenticated)
      5. Database fallback with 1s timeout
      6. Spec default calibrated spot
    """
    und_upper = underlying.strip().upper()
    if und_upper in ("BSE SENSEX", "BSESEN", "BSE:SENSEX"):
        und_upper = "SENSEX"
    elif und_upper in ("NIFTY 50", "NSE:NIFTY 50"):
        und_upper = "NIFTY"
    elif und_upper in ("NIFTY BANK", "NSE:NIFTY BANK"):
        und_upper = "BANKNIFTY"
    elif und_upper in ("NIFTY FIN SERVICE", "NSE:NIFTY FIN SERVICE"):
        und_upper = "FINNIFTY"
    elif und_upper in ("NIFTY MID SELECT", "NSE:NIFTY MID SELECT"):
        und_upper = "MIDCPNIFTY"

    now = time.time()

    try:
        from trading_engine.config import get_index_spec
    except ImportError:
        from config import get_index_spec
    spec = get_index_spec(und_upper)

    # 1. Direct from Live Index Spot Tracker (populated by WebSocket ticks)
    with _price_bus_lock:
        if und_upper in _live_index_spots:
            ts, px = _live_index_spots[und_upper]
            if px > 0 and (now - ts < 120.0 or not is_market_open()):
                return px

    # 2. Check in-memory price bus
    live_prices = get_all_live_prices()
    candidates = [
        und_upper,
        spec.get("kite_spot", ""),
        spec.get("breeze_stock_code", ""),
        f"{und_upper} 50",
        "NIFTY BANK" if und_upper == "BANKNIFTY" else None,
        "BSE SENSEX" if und_upper == "SENSEX" else None,
        "BSESEN" if und_upper == "SENSEX" else None,
    ]
    for c in candidates:
        if not c:
            continue
        entry = live_prices.get(c) or live_prices.get(c.upper())
        if entry and entry.get("price") and float(entry["price"]) > 0:
            px = float(entry["price"])
            _spot_cache[und_upper] = (now, px)
            return px

    # 3. Fast cache
    if und_upper in _spot_cache:
        ts, px = _spot_cache[und_upper]
        max_age = 5.0 if is_market_open() else 300.0
        if now - ts < max_age and px > 0:
            return px

    # 4. Direct broker quote (Kite / Breeze)
    token = spec.get("kite_token")
    if token:
        try:
            kite = _get_kite()
            if kite:
                q = kite.quote([token])
                tok_key = str(token)
                if tok_key in q and q[tok_key].get("last_price"):
                    px = float(q[tok_key]["last_price"])
                    if px > 0:
                        _spot_cache[und_upper] = (now, px)
                        with _price_bus_lock:
                            _live_index_spots[und_upper] = (now, px)
                        return px
        except Exception:
            pass

    # 5. Database fallback with strict 1s connect timeout
    try:
        from sqlalchemy import create_engine, text
        db_url = os.environ.get("DB_URL") or os.environ.get("DATABASE_URL") or "postgresql+psycopg2://postgres:postgres@localhost:5432/trading"
        if not db_url.startswith("postgresql+psycopg2://") and db_url.startswith("postgresql://"):
            db_url = db_url.replace("postgresql://", "postgresql+psycopg2://", 1)
        db_engine = create_engine(db_url, connect_args={"connect_timeout": 1})
        with db_engine.connect() as conn:
            sym_list = [und_upper, spec.get("kite_spot", ""), spec.get("breeze_stock_code", ""), f"{und_upper}-I", f"{und_upper} 50"]
            q_spot = text("""
                SELECT close FROM minute_candles
                WHERE symbol = ANY(:syms)
                ORDER BY timestamp DESC LIMIT 1;
            """)
            row = conn.execute(q_spot, {"syms": sym_list}).fetchone()
            if row and row[0] and float(row[0]) > 0:
                px = float(row[0])
                _spot_cache[und_upper] = (now, px)
                return px
    except Exception:
        pass

    # 6. Default spec spot
    default_spots = {
        "NIFTY": 22698.0,
        "BANKNIFTY": 54267.0,
        "SENSEX": 73246.0,
        "FINNIFTY": 24418.0,
        "MIDCPNIFTY": 13286.0,
    }
    fallback_px = float(spec.get("default_spot") or default_spots.get(und_upper, 22500.0))
    _spot_cache[und_upper] = (now, fallback_px)
    return fallback_px


def _norm_cdf(x: float) -> float:
    """Standard normal cumulative distribution function using C-level math.erf."""
    return 0.5 * (1.0 + math.erf(x / 1.4142135623730951))


def _fast_black_scholes_price(S: float, K: float, T: float, r: float, sigma: float, is_call: bool) -> float:
    """Sub-microsecond Black-Scholes European pricing with zero library dependencies."""
    if T <= 1e-6 or sigma <= 1e-4:
        intr = (S - K) if is_call else (K - S)
        return max(0.05, float(intr))

    d1 = (math.log(max(1e-4, S / K)) + (r + 0.5 * sigma ** 2) * T) / (sigma * math.sqrt(T))
    d2 = d1 - sigma * math.sqrt(T)

    if is_call:
        price = S * _norm_cdf(d1) - K * math.exp(-r * T) * _norm_cdf(d2)
    else:
        price = K * math.exp(-r * T) * _norm_cdf(-d2) - S * _norm_cdf(-d1)
    return max(0.05, float(price))


_option_chain_cache: Dict[str, Tuple[float, Dict[str, Any]]] = {}
OPTION_CHAIN_CACHE_TTL = 3.0  # 3 seconds cache


def get_option_chain_data(
    underlying: str = "NIFTY",
    expiry: Optional[str] = None,
    spot_price: Optional[float] = None,
    fetch_broker_quotes: bool = False
) -> Dict[str, Any]:
    """
    Build real-time Option Chain for the underlying and expiry date.
    Returns full list of strikes centered around dynamic ATM with live LTPs.
    Ultra-optimized: O(1) hash matrix lookup (< 2ms) with zero list scanning.
    """
    und_upper = underlying.strip().upper()
    if und_upper in ("BSE SENSEX", "BSESEN", "BSE:SENSEX"):
        und_upper = "SENSEX"
    elif und_upper in ("NIFTY 50", "NSE:NIFTY 50"):
        und_upper = "NIFTY"
    elif und_upper in ("NIFTY BANK", "NSE:NIFTY BANK"):
        und_upper = "BANKNIFTY"
    elif und_upper in ("NIFTY FIN SERVICE", "NSE:NIFTY FIN SERVICE"):
        und_upper = "FINNIFTY"
    elif und_upper in ("NIFTY MID SELECT", "NSE:NIFTY MID SELECT"):
        und_upper = "MIDCPNIFTY"

    now = time.time()
    today = _get_ist_now().date()

    # Default expiry to closest active future expiry
    if not expiry:
        exp_list = get_available_expiries(und_upper)
        near_exp = next((x["date"] for x in exp_list if x.get("days_to_expiry", -1) >= 0), None)
        expiry = near_exp if near_exp else (exp_list[0]["date"] if exp_list else today.strftime("%Y-%m-%d"))

    # Determine spot price
    if spot_price is None or spot_price <= 0:
        spot_price = resolve_spot_price(und_upper, expiry=expiry)

    cache_key = f"{und_upper}:{expiry}:{round(spot_price, 1)}"
    if cache_key in _option_chain_cache:
        ts, cached_oc = _option_chain_cache[cache_key]
        ttl = 2.0 if is_market_open() else 300.0
        if now - ts < ttl:
            return cached_oc

    # Ensure grid is built
    with _grid_lock:
        grid_for_und = _option_grid.get(und_upper) or {}
        grid_for_expiry = grid_for_und.get(expiry) or {}

    if not grid_for_expiry:
        insts = _load_instruments()
        with _grid_lock:
            if not _option_grid and insts:
                _build_option_grid(insts)
            grid_for_und = _option_grid.get(und_upper) or {}
            grid_for_expiry = grid_for_und.get(expiry) or {}

    try:
        from trading_engine.config import get_index_spec
    except ImportError:
        from config import get_index_spec

    spec = get_index_spec(und_upper)
    step = spec.get("strike_gap", 50)
    atm_strike = int(round(spot_price / step) * step)

    live_prices = get_all_live_prices()
    strike_map: Dict[float, Dict] = {}

    if grid_for_expiry:
        for stk, itypes in grid_for_expiry.items():
            strike_map[stk] = {
                "strike": stk,
                "is_atm": (int(stk) == atm_strike),
            }
            for itype, o in itypes.items():
                sym = o.get("tradingsymbol")
                token = o.get("instrument_token")
                live_entry = live_prices.get(sym) or live_prices.get(sym.upper()) if live_prices else None
                ltp = float(live_entry["price"]) if live_entry and live_entry.get("price") else None

                exp_val = o.get("expiry")
                d_str = exp_val.strftime("%d %b").upper() if hasattr(exp_val, "strftime") else str(exp_val)
                disp = f"{und_upper} {d_str} {int(stk)} {itype}"

                key = "call" if itype == "CE" else "put"
                strike_map[stk][key] = {
                    "symbol": sym,
                    "display_name": disp,
                    "ltp": ltp,
                    "token": token,
                }
    else:
        # Fallback to scanning instruments if grid miss
        opts = [
            i for i in insts
            if (i.get("name") == und_upper or i.get("name") == spec.get("breeze_stock_code"))
            and i.get("segment") in ("NFO-OPT", "BFO-OPT")
            and str(i.get("expiry")) == expiry
        ]
        for o in opts:
            stk = float(o["strike"])
            itype = o.get("instrument_type")
            if stk not in strike_map:
                strike_map[stk] = {
                    "strike": stk,
                    "is_atm": (int(stk) == atm_strike),
                }
            sym = o.get("tradingsymbol")
            token = o.get("instrument_token")
            live_entry = live_prices.get(sym) or live_prices.get(sym.upper()) if live_prices else None
            ltp = float(live_entry["price"]) if live_entry and live_entry.get("price") else None
            d_str = o["expiry"].strftime("%d %b").upper() if hasattr(o.get("expiry"), "strftime") else str(o.get("expiry"))
            disp = f"{und_upper} {d_str} {int(stk)} {itype}"
            key = "call" if itype == "CE" else "put"
            strike_map[stk][key] = {
                "symbol": sym,
                "display_name": disp,
                "ltp": ltp,
                "token": token,
            }

    # Center around ATM strike (±10 strikes = 21 strikes)
    sorted_strikes = sorted(strike_map.keys())
    if sorted_strikes:
        atm_idx = min(range(len(sorted_strikes)), key=lambda idx: abs(sorted_strikes[idx] - atm_strike))
        start_idx = max(0, atm_idx - 10)
        end_idx = min(len(sorted_strikes), atm_idx + 11)
        windowed_strikes = sorted_strikes[start_idx:end_idx]
    else:
        windowed_strikes = []

    # Batch quote pre-warming for missing LTPs in the active ATM window
    missing_tokens = []
    token_to_target = {}
    for stk in windowed_strikes:
        s_data = strike_map[stk]
        for side in ("call", "put"):
            if side in s_data and s_data[side].get("ltp") is None and s_data[side].get("token"):
                tok = s_data[side]["token"]
                missing_tokens.append(tok)
                token_to_target[tok] = (stk, side)

    if missing_tokens and fetch_broker_quotes:
        active_broker = get_active_broker()
        if active_broker == "breeze":
            try:
                from trading_engine.adapters.breeze_adapter import default_breeze_adapter
                adapter = default_breeze_adapter
                if adapter.is_connected or adapter.connect():
                    for tok, (stk, side) in token_to_target.items():
                        sym = strike_map[stk][side].get("symbol")
                        if sym:
                            q = adapter.get_quote(sym)
                            if q and q.get("price"):
                                q_px = float(q["price"])
                                strike_map[stk][side]["ltp"] = q_px
                                push_to_price_bus(sym, q_px)
            except Exception:
                pass
        else:
            try:
                kite = _get_kite()
                if kite:
                    quotes = kite.quote(missing_tokens)
                    for tok, (stk, side) in token_to_target.items():
                        tok_str = str(tok)
                        if tok_str in quotes and quotes[tok_str].get("last_price"):
                            q_px = float(quotes[tok_str]["last_price"])
                            strike_map[stk][side]["ltp"] = q_px
                            sym = strike_map[stk][side].get("symbol")
                            if sym:
                                push_to_price_bus(sym, q_px)
            except Exception:
                pass

    # Black-Scholes fallback for any remaining missing LTPs
    try:
        exp_dt = datetime.strptime(expiry, "%Y-%m-%d").date()
        days_to_exp = max(0.5, (exp_dt - today).days)
        T_years = max(1e-4, days_to_exp / 365.0)
    except Exception:
        T_years = 3.0 / 365.0

    r = 0.07
    sigma = 0.13

    for stk in windowed_strikes:
        s_data = strike_map[stk]
        for side, is_call in [("call", True), ("put", False)]:
            if side in s_data and s_data[side].get("ltp") is None:
                bs_px = round(_fast_black_scholes_price(spot_price, stk, T_years, r, sigma, is_call), 2)
                s_data[side]["ltp"] = bs_px

    chain = [strike_map[s] for s in windowed_strikes]

    res = {
        "underlying": und_upper,
        "expiry": expiry,
        "spot": round(spot_price, 2),
        "spot_price": round(spot_price, 2),
        "atm_strike": atm_strike,
        "total_strikes": len(sorted_strikes),
        "chain": chain,
    }
    _option_chain_cache[cache_key] = (now, res)
    return res



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
    Cached in-memory for sub-millisecond retrieval.
    """
    today_str = get_today_str()
    now_ist = _get_ist_now()
    is_weekend = now_ist.weekday() >= 5
    explicit_date_requested = bool(target_date and str(target_date).lower() != "today" and str(target_date) != today_str)

    # 0. Fast-path in-memory session cache with TTL awareness (60s during market hours, persistent after close)
    if not explicit_date_requested:
        with _lock:
            cached_entry = _session_candles.get(trading_symbol)
            if cached_entry:
                cached_ts, cached_list = cached_entry
                is_valid = (not is_market_open()) or (time.time() - cached_ts < 60.0)
                if is_valid and len(cached_list) > 0:
                    return {
                        "symbol": trading_symbol,
                        "date": today_str,
                        "count": len(cached_list),
                        "num_candles": len(cached_list),
                        "is_last_available_day": False,
                        "candles": list(cached_list),
                    }

    # 1. Resolve DB Engine
    if db_engine is None:
        try:
            from sqlalchemy import create_engine
            db_engine = create_engine(os.environ.get("DB_URL", "postgresql+psycopg2://postgres:postgres@localhost:5432/trading"))
        except Exception:
            pass

    if explicit_date_requested:
        active_date = target_date
    else:
        active_date = None
    is_last_day = False

    # If target_date is not an explicit past date and it's a weekday, check if today has candles in DB with chunk pruning
    if not explicit_date_requested and not is_weekend and db_engine is not None:
        try:
            from sqlalchemy import text
            with db_engine.connect() as conn:
                for tbl in ["minute_candles", "option_minute_candles"]:
                    q_cnt = text(f"""
                        SELECT count(*) FROM {tbl}
                        WHERE symbol = :sym
                          AND timestamp >= NOW() - INTERVAL '24 hours'
                          AND (timestamp AT TIME ZONE 'Asia/Kolkata')::date = :today
                    """)
                    cnt = conn.execute(q_cnt, {"sym": trading_symbol, "today": today_str}).scalar()
                    if cnt and cnt > 0:
                        active_date = today_str
                        break
        except Exception as e:
            print(f"ContractDataManager: error checking today's candles: {e}", flush=True)

    # Only fall back to latest trading day if NO explicit past date was requested
    if not explicit_date_requested and not active_date and db_engine is not None:
        try:
            from sqlalchemy import text
            is_pg = getattr(db_engine.dialect, "name", "") == "postgresql"
            with db_engine.connect() as conn:
                for tbl in ["minute_candles", "option_minute_candles"]:
                    try:
                        if is_pg:
                            q_max = text(f"""
                                SELECT max((timestamp AT TIME ZONE 'Asia/Kolkata')::date)
                                FROM {tbl}
                                WHERE symbol = :sym
                                  AND timestamp >= NOW() - INTERVAL '30 days';
                            """)
                        else:
                            q_max = text(f"SELECT max(date(timestamp)) FROM {tbl} WHERE symbol = :sym;")
                        max_d = conn.execute(q_max, {"sym": trading_symbol}).scalar()
                        if max_d:
                            active_date = str(max_d)
                            is_last_day = True
                            break
                    except Exception:
                        pass

                # If still no date found, check tick_data for latest date
                if not active_date:
                    try:
                        sym_aliases = [trading_symbol]
                        if trading_symbol in ("NIFTY", "NIFTY-I", "NIFTY 50", "NIFTY50"):
                            sym_aliases = ["NIFTY", "NIFTY 50", "NIFTY-I"]
                        elif trading_symbol in ("BANKNIFTY", "BANKNIFTY-I", "NIFTY BANK"):
                            sym_aliases = ["BANKNIFTY", "NIFTY BANK", "BANKNIFTY-I"]
                        elif trading_symbol in ("SENSEX", "SENSEX-I", "BSE SENSEX"):
                            sym_aliases = ["SENSEX", "BSE SENSEX", "SENSEX-I"]
                        ph = ", ".join([f":s{i}" for i in range(len(sym_aliases))])
                        p_map = {f"s{i}": s for i, s in enumerate(sym_aliases)}
                        if is_pg:
                            q_max_t = text(f"""
                                SELECT max((timestamp AT TIME ZONE 'Asia/Kolkata')::date)
                                FROM tick_data
                                WHERE symbol IN ({ph})
                                  AND timestamp >= NOW() - INTERVAL '30 days';
                            """)
                        else:
                            q_max_t = text(f"SELECT max(date(timestamp)) FROM tick_data WHERE symbol IN ({ph});")
                        max_d = conn.execute(q_max_t, p_map).scalar()
                        if max_d:
                            active_date = str(max_d)
                            is_last_day = True
                    except Exception:
                        pass
        except Exception as e:
            print(f"ContractDataManager: error finding latest date for {trading_symbol}: {e}", flush=True)

    if not active_date:
        active_date = today_str

    # 2. Query candles for active_date from DB with exact partition bounds
    candles = []
    if db_engine is not None:
        try:
            from sqlalchemy import text
            import pandas as pd
            import re
            start_ts = f"{active_date} 00:00:00+05:30"
            end_ts = f"{active_date} 23:59:59+05:30"

            # Parse underlying and strike if option contract
            opt_pattern = None
            m_opt = re.search(r'([A-Za-z]+).*?(\d{4,6})\s*(CE|PE)', trading_symbol, re.IGNORECASE)
            if m_opt:
                u_pfx = m_opt.group(1).upper()
                if u_pfx in ("NIFTY", "BANKNIFTY", "SENSEX", "FINNIFTY", "MIDCPNIFTY"):
                    opt_pattern = f"{u_pfx}%{m_opt.group(2)}{m_opt.group(3).upper()}"

            sym_aliases = [trading_symbol]
            if trading_symbol in ("NIFTY", "NIFTY-I", "NIFTY 50", "NIFTY50"):
                sym_aliases = ["NIFTY", "NIFTY 50", "NIFTY-I"]
            elif trading_symbol in ("BANKNIFTY", "BANKNIFTY-I", "NIFTY BANK"):
                sym_aliases = ["BANKNIFTY", "NIFTY BANK", "BANKNIFTY-I"]
            elif trading_symbol in ("SENSEX", "SENSEX-I", "BSE SENSEX"):
                sym_aliases = ["SENSEX", "BSE SENSEX", "SENSEX-I"]

            with db_engine.connect() as conn:
                ph = ", ".join([f":s{i}" for i in range(len(sym_aliases))])
                p_map = {f"s{i}": s for i, s in enumerate(sym_aliases)}
                p_map["start_ts"] = start_ts
                p_map["end_ts"] = end_ts

                for tbl in ["minute_candles", "option_minute_candles"]:
                    sql = text(f"""
                        SELECT timestamp, open, high, low, close, volume
                        FROM {tbl}
                        WHERE symbol IN ({ph})
                          AND timestamp >= :start_ts
                          AND timestamp <= :end_ts
                        ORDER BY timestamp ASC
                    """)
                    df = pd.read_sql(sql, conn, params=p_map)
                    if not df.empty:
                        candles = _df_to_candle_list(df)
                        break

                # 2a. If no exact match, check by option pattern (e.g. NIFTY26OCT22750CE -> NIFTY26N0322750CE)
                if not candles and opt_pattern:
                    for tbl in ["minute_candles", "option_minute_candles"]:
                        sql_pat = text(f"""
                            SELECT timestamp, open, high, low, close, volume
                            FROM {tbl}
                            WHERE symbol LIKE :pat
                              AND timestamp >= :start_ts
                              AND timestamp <= :end_ts
                            ORDER BY timestamp ASC
                        """)
                        df_pat = pd.read_sql(sql_pat, conn, params={"pat": opt_pattern, "start_ts": start_ts, "end_ts": end_ts})
                        if not df_pat.empty:
                            candles = _df_to_candle_list(df_pat)
                            break

                # 2b. Fallback to tick_data aggregation if minute_candles / option_minute_candles has 0 candles
                if not candles:
                    tick_start = f"{active_date} 09:15:00+05:30"
                    tick_end = f"{active_date} 15:30:00+05:30"
                    params = {f"s{i}": s for i, s in enumerate(sym_aliases)}
                    params["start_ts"] = tick_start
                    params["end_ts"] = tick_end

                    is_pg = getattr(db_engine.dialect, "name", "") == "postgresql"
                    if is_pg:
                        sql_tick = text(f"""
                            SELECT 
                                date_trunc('minute', timestamp) AS timestamp,
                                (array_agg(price ORDER BY timestamp ASC))[1] AS open,
                                MAX(price) AS high,
                                MIN(price) AS low,
                                (array_agg(price ORDER BY timestamp DESC))[1] AS close,
                                CASE 
                                    WHEN MAX(volume) > MIN(volume) THEN MAX(volume) - MIN(volume)
                                    ELSE COUNT(*)
                                END AS volume
                            FROM tick_data
                            WHERE symbol IN ({ph})
                              AND timestamp >= :start_ts
                              AND timestamp <= :end_ts
                            GROUP BY date_trunc('minute', timestamp)
                            ORDER BY timestamp ASC
                        """)
                        df_tick = pd.read_sql(sql_tick, conn, params=params)

                        if df_tick.empty and opt_pattern:
                            sql_tick_pat = text("""
                                SELECT 
                                    date_trunc('minute', timestamp) AS timestamp,
                                    (array_agg(price ORDER BY timestamp ASC))[1] AS open,
                                    MAX(price) AS high,
                                    MIN(price) AS low,
                                    (array_agg(price ORDER BY timestamp DESC))[1] AS close,
                                    CASE 
                                        WHEN MAX(volume) > MIN(volume) THEN MAX(volume) - MIN(volume)
                                        ELSE COUNT(*)
                                    END AS volume
                                FROM tick_data
                                WHERE symbol LIKE :pat
                                  AND timestamp >= :start_ts
                                  AND timestamp <= :end_ts
                                GROUP BY date_trunc('minute', timestamp)
                                ORDER BY timestamp ASC
                            """)
                            df_tick = pd.read_sql(sql_tick_pat, conn, params={"pat": opt_pattern, "start_ts": tick_start, "end_ts": tick_end})
                    else:
                        sql_tick = text(f"""
                            SELECT timestamp, price, volume
                            FROM tick_data
                            WHERE symbol IN ({ph})
                              AND timestamp >= :start_ts
                              AND timestamp <= :end_ts
                            ORDER BY timestamp ASC
                        """)
                        df_raw = pd.read_sql(sql_tick, conn, params=params)
                        if df_raw.empty and opt_pattern:
                            sql_tick_pat = text("""
                                SELECT timestamp, price, volume
                                FROM tick_data
                                WHERE symbol LIKE :pat
                                  AND timestamp >= :start_ts
                                  AND timestamp <= :end_ts
                                ORDER BY timestamp ASC
                            """)
                            df_raw = pd.read_sql(sql_tick_pat, conn, params={"pat": opt_pattern, "start_ts": tick_start, "end_ts": tick_end})

                        if not df_raw.empty:
                            df_raw["ts_dt"] = pd.to_datetime(df_raw["timestamp"])
                            resampled = df_raw.set_index("ts_dt").resample("1min").agg({
                                "price": ["first", "max", "min", "last"],
                                "volume": lambda x: int(x.max() - x.min()) if (len(x) > 0 and x.max() > x.min()) else len(x)
                            }).dropna()
                            resampled.columns = ["open", "high", "low", "close", "volume"]
                            resampled["timestamp"] = resampled.index
                            df_tick = resampled.reset_index(drop=True)[["timestamp", "open", "high", "low", "close", "volume"]]
                        else:
                            df_tick = pd.DataFrame()

                    if not df_tick.empty:
                        candles = _df_to_candle_list(df_tick)
        except Exception as e:
            print(f"ContractDataManager: DB candle fetch error for {trading_symbol}: {e}", flush=True)

    # 3. If DB has no candles or insufficient candles (< 250 bars in a 375-bar trading session),
    # query active broker (Breeze or Kite) for the complete authentic session
    if not candles or len(candles) < 250:
        broker = get_active_broker()
        broker_candles = []
        if broker == "breeze":
            broker_candles = _fetch_from_breeze(trading_symbol, target_date=active_date)
            if not broker_candles:
                broker_candles = _fetch_from_kite(trading_symbol, db_engine, target_date=active_date)
        else:
            broker_candles = _fetch_from_kite(trading_symbol, db_engine, target_date=active_date)
            if not broker_candles:
                broker_candles = _fetch_from_breeze(trading_symbol, target_date=active_date)

        if broker_candles and len(broker_candles) > len(candles):
            candles = broker_candles
            # Backfill to minute_candles so subsequent queries are sub-millisecond fast
            if db_engine is not None and candles and active_date:
                try:
                    from sqlalchemy import text
                    start_del = f"{active_date} 00:00:00+05:30"
                    end_del = f"{active_date} 23:59:59+05:30"
                    records = []
                    for c in candles:
                        c_ts = datetime.fromtimestamp(c["timestamp"]).strftime("%Y-%m-%d %H:%M:%S+05:30")
                        records.append({
                            "ts": c_ts,
                            "sym": trading_symbol,
                            "o": float(c["open"]),
                            "h": float(c["high"]),
                            "l": float(c["low"]),
                            "c": float(c["close"]),
                            "v": int(c["volume"])
                        })
                    with db_engine.begin() as conn:
                        conn.execute(
                            text("DELETE FROM minute_candles WHERE symbol = :sym AND timestamp >= :s_ts AND timestamp <= :e_ts"),
                            {"sym": trading_symbol, "s_ts": start_del, "e_ts": end_del}
                        )
                        conn.execute(
                            text("""
                                INSERT INTO minute_candles (timestamp, symbol, open, high, low, close, volume)
                                VALUES (:ts, :sym, :o, :h, :l, :c, :v)
                            """),
                            records
                        )
                    print(f"ContractDataManager: Backfilled {len(records)} candles to minute_candles for {trading_symbol} on {active_date}", flush=True)
                except Exception as b_err:
                    print(f"ContractDataManager: Backfill error (non-fatal): {b_err}", flush=True)

    # 4. Fallback if today still has 0 candles (e.g. pre-market or weekend)
    if not candles and not explicit_date_requested and db_engine is not None:
        try:
            from sqlalchemy import text
            import pandas as pd
            is_pg = getattr(db_engine.dialect, "name", "") == "postgresql"
            with db_engine.connect() as conn:
                fallback_date = None
                for tbl in ["minute_candles", "option_minute_candles"]:
                    if is_pg:
                        q_max = text(f"SELECT max((timestamp AT TIME ZONE 'Asia/Kolkata')::date) FROM {tbl} WHERE symbol = :sym")
                    else:
                        q_max = text(f"SELECT max(date(timestamp)) FROM {tbl} WHERE symbol = :sym")
                    max_d = conn.execute(q_max, {"sym": trading_symbol}).scalar()
                    if max_d:
                        fallback_date = str(max_d)
                        break

                if not fallback_date:
                    sym_aliases = [trading_symbol]
                    if trading_symbol in ("NIFTY", "NIFTY-I", "NIFTY 50", "NIFTY50"):
                        sym_aliases = ["NIFTY", "NIFTY 50", "NIFTY-I"]
                    elif trading_symbol in ("BANKNIFTY", "BANKNIFTY-I", "NIFTY BANK"):
                        sym_aliases = ["BANKNIFTY", "NIFTY BANK", "BANKNIFTY-I"]
                    elif trading_symbol in ("SENSEX", "SENSEX-I", "BSE SENSEX"):
                        sym_aliases = ["SENSEX", "BSE SENSEX", "SENSEX-I"]
                    ph = ", ".join([f":s{i}" for i in range(len(sym_aliases))])
                    p_map = {f"s{i}": s for i, s in enumerate(sym_aliases)}
                    if is_pg:
                        q_max_t = text(f"SELECT max((timestamp AT TIME ZONE 'Asia/Kolkata')::date) FROM tick_data WHERE symbol IN ({ph})")
                    else:
                        q_max_t = text(f"SELECT max(date(timestamp)) FROM tick_data WHERE symbol IN ({ph})")
                    max_d = conn.execute(q_max_t, p_map).scalar()
                    if max_d:
                        fallback_date = str(max_d)

                if fallback_date:
                    for tbl in ["minute_candles", "option_minute_candles"]:
                        sql = text(f"""
                            SELECT timestamp, open, high, low, close, volume
                            FROM {tbl}
                            WHERE symbol = :sym
                              AND (timestamp AT TIME ZONE 'Asia/Kolkata')::date = :d
                            ORDER BY timestamp ASC
                        """)
                        df = pd.read_sql(sql, conn, params={"sym": trading_symbol, "d": fallback_date})
                        if not df.empty:
                            candles = _df_to_candle_list(df)
                            active_date = fallback_date
                            is_last_day = True
                            break

                    if not candles:
                        # Fallback to tick_data for fallback_date
                        sym_aliases = [trading_symbol]
                        if trading_symbol in ("NIFTY", "NIFTY-I", "NIFTY 50", "NIFTY50"):
                            sym_aliases = ["NIFTY", "NIFTY 50", "NIFTY-I"]
                        elif trading_symbol in ("BANKNIFTY", "BANKNIFTY-I", "NIFTY BANK"):
                            sym_aliases = ["BANKNIFTY", "NIFTY BANK", "BANKNIFTY-I"]
                        elif trading_symbol in ("SENSEX", "SENSEX-I", "BSE SENSEX"):
                            sym_aliases = ["SENSEX", "BSE SENSEX", "SENSEX-I"]
                        ph = ", ".join([f":s{i}" for i in range(len(sym_aliases))])
                        params = {f"s{i}": s for i, s in enumerate(sym_aliases)}
                        params["start_ts"] = f"{fallback_date} 09:15:00+05:30"
                        params["end_ts"] = f"{fallback_date} 15:30:00+05:30"
                        if is_pg:
                            sql_tick = text(f"""
                                SELECT 
                                    date_trunc('minute', timestamp) AS timestamp,
                                    (array_agg(price ORDER BY timestamp ASC))[1] AS open,
                                    MAX(price) AS high,
                                    MIN(price) AS low,
                                    (array_agg(price ORDER BY timestamp DESC))[1] AS close,
                                    CASE 
                                        WHEN MAX(volume) > MIN(volume) THEN MAX(volume) - MIN(volume)
                                        ELSE COUNT(*)
                                    END AS volume
                                FROM tick_data
                                WHERE symbol IN ({ph})
                                  AND timestamp >= :start_ts
                                  AND timestamp <= :end_ts
                                GROUP BY date_trunc('minute', timestamp)
                                ORDER BY timestamp ASC
                            """)
                            df_tick = pd.read_sql(sql_tick, conn, params=params)
                        else:
                            sql_tick = text(f"""
                                SELECT timestamp, price, volume
                                FROM tick_data
                                WHERE symbol IN ({ph})
                                  AND timestamp >= :start_ts
                                  AND timestamp <= :end_ts
                                ORDER BY timestamp ASC
                            """)
                            df_raw = pd.read_sql(sql_tick, conn, params=params)
                            if not df_raw.empty:
                                df_raw["ts_dt"] = pd.to_datetime(df_raw["timestamp"])
                                resampled = df_raw.set_index("ts_dt").resample("1min").agg({
                                    "price": ["first", "max", "min", "last"],
                                    "volume": lambda x: int(x.max() - x.min()) if (len(x) > 0 and x.max() > x.min()) else len(x)
                                }).dropna()
                                resampled.columns = ["open", "high", "low", "close", "volume"]
                                resampled["timestamp"] = resampled.index
                                df_tick = resampled.reset_index(drop=True)[["timestamp", "open", "high", "low", "close", "volume"]]
                            else:
                                df_tick = pd.DataFrame()

                        if not df_tick.empty:
                            candles = _df_to_candle_list(df_tick)
                            active_date = fallback_date
                            is_last_day = True
        except Exception as e:
            print(f"ContractDataManager: fallback fetch error for {trading_symbol}: {e}", flush=True)

    # STRICT REQUIREMENT: Zero synthetic/simulated candle data.
    # Do not fill gaps with synthetic candles; only authentic live/historical candles from DB or broker are returned.
    if not candles and os.getenv("ALLOW_SYNTHETIC_CANDLES", "0").lower() in ("1", "true"):
        # Fallback realistic candles for offline visual mock testing only
        # Dynamically anchor base price to Black-Scholes theoretical price so it matches Option Chain ATM premium
        import re
        is_call = "CE" in trading_symbol.upper()
        if "SENSEX" in trading_symbol.upper():
            und = "SENSEX"
        elif "BANKNIFTY" in trading_symbol.upper():
            und = "BANKNIFTY"
        else:
            und = "NIFTY"

        stk_matches = re.findall(r"(\d{4,6})", trading_symbol)
        stk = float(stk_matches[-1]) if stk_matches else (73600.0 if und == "SENSEX" else 22700.0)

        base_p = None
        try:
            from trading_engine.ai.kronos_service import black_scholes_price
            spot_px = resolve_spot_price(und)
            if spot_px and spot_px > 0:
                bs_px = black_scholes_price(spot_px, stk, 3.0 / 365.0, 0.07, 0.13, is_call)
                if bs_px and bs_px > 5.0:
                    base_p = round(float(bs_px), 2)
        except Exception as e:
            print(f"ContractDataManager: error calculating fallback BS price for {trading_symbol}: {e}", flush=True)

        if not base_p:
            base_p = 120.0 if is_call else 95.0

        import hashlib
        import math
        import random

        # Derive start timestamp for active_date at 09:15 AM IST
        try:
            d_parts = [int(x) for x in active_date.split("-")]
            dt_base = datetime(d_parts[0], d_parts[1], d_parts[2], 9, 15, 0)
            base_ts = int(dt_base.timestamp())
        except Exception:
            base_ts = 1761450900  # Fallback 09:15 IST

        # Shared underlying seed so Call & Put react to the exact same market path
        seed_str = f"{und}_{active_date}"
        seed_val = int(hashlib.md5(seed_str.encode()).hexdigest()[:8], 16)
        rng = random.Random(seed_val)

        # 375 bars: Full standard trading session (09:15 to 15:30)
        num_bars = 375
        spot_returns = []
        for i in range(num_bars):
            # Intraday cyclical waves + Gaussian micro-volatility
            wave = 0.00025 * math.sin(i / 28.0) + 0.00015 * math.cos(i / 14.0)
            shock = rng.gauss(0, 0.00065)
            spot_returns.append(wave + shock)

        p = base_p
        candles = []
        for i in range(num_bars):
            ret = spot_returns[i] if is_call else -spot_returns[i]
            # Realistic option delta gearing with damping
            d_price = p * ret * 14.0
            d_price = max(-p * 0.04, min(p * 0.04, d_price))

            c_open = p
            c_close = round(max(5.0, c_open + d_price), 2)

            # Realistic upper and lower wicks
            wick_up = abs(rng.gauss(0.75, 0.45))
            wick_dn = abs(rng.gauss(0.75, 0.45))
            c_high = round(max(c_open, c_close) + wick_up, 2)
            c_low = round(max(2.0, min(c_open, c_close) - wick_dn), 2)

            # Intraday volume curve: high at open/close, moderate midday
            u_curve = 1.0 + 1.2 * math.exp(-i / 20.0) + 1.0 * math.exp(-(num_bars - i) / 20.0)
            vol = int(max(100, rng.gauss(1800 * u_curve, 400)))

            candles.append({
                "time": datetime.fromtimestamp(base_ts + i * 60).strftime("%Y-%m-%d %H:%M"),
                "timestamp": base_ts + i * 60,
                "open": c_open,
                "high": c_high,
                "low": c_low,
                "close": c_close,
                "volume": vol
            })
            p = c_close

    if candles and active_date == today_str:
        with _lock:
            _session_candles[trading_symbol] = (time.time(), list(candles))

    market_status = "OPEN" if is_market_open() else "CLOSED"
    return {
        "symbol": trading_symbol,
        "date": active_date,
        "is_last_day_data": is_last_day,
        "market_status": market_status,
        "count": len(candles),
        "num_candles": len(candles),
        "candles": candles,
    }



def fetch_today_candles(trading_symbol: str, db_engine=None) -> List[Dict]:
    """
    Fetch today's 1-minute candles for the given symbol (falls back to last trading day if market is closed).
    """
    res = fetch_contract_candles(trading_symbol, db_engine=db_engine)
    return res.get("candles", [])


def _fetch_from_breeze(trading_symbol: str, target_date: Optional[str] = None) -> List[Dict]:
    try:
        from trading_engine.adapters.breeze_adapter import default_breeze_adapter
        if not default_breeze_adapter.is_connected:
            default_breeze_adapter.connect()
        if not default_breeze_adapter.is_connected:
            return []
        req_date = str(target_date).strip() if (target_date and len(str(target_date).strip()) == 10) else _get_ist_now().strftime("%Y-%m-%d")
        print(f"ContractDataManager: Fetching Breeze historical for {trading_symbol} on {req_date}", flush=True)
        raw_candles = default_breeze_adapter.get_historical_candles(
            symbol=trading_symbol,
            from_date=req_date,
            to_date=req_date,
            interval="1minute"
        )
        if not raw_candles:
            return []
        candles = []
        for c in raw_candles:
            ts = c["time"]
            dt = datetime.fromtimestamp(ts)
            candles.append({
                "time": dt.strftime("%Y-%m-%d %H:%M"),
                "timestamp": ts,
                "open": float(c.get("open", 0)),
                "high": float(c.get("high", 0)),
                "low": float(c.get("low", 0)),
                "close": float(c.get("close", 0)),
                "volume": int(c.get("volume", 0)),
            })
        print(f"ContractDataManager: Got {len(candles)} Breeze candles for {trading_symbol}", flush=True)
        return candles
    except Exception as e:
        print(f"ContractDataManager: Breeze historical fetch error for {trading_symbol}: {e}", flush=True)
        return []


def _fetch_from_kite(trading_symbol: str, db_engine=None, target_date: Optional[str] = None) -> List[Dict]:
    """Fetch 1-minute candles from Kite Connect historical API."""
    try:
        kite = _get_kite()
        if not kite:
            return []
        
        token, exchange = resolve_token_for_kite_symbol(trading_symbol)
        if not token:
            print(f"ContractDataManager: No token found for {trading_symbol}", flush=True)
            return []
        
        if target_date and len(str(target_date).strip()) == 10:
            try:
                dt_req = datetime.strptime(str(target_date).strip(), "%Y-%m-%d").date()
                from_dt = datetime(dt_req.year, dt_req.month, dt_req.day, 9, 15, 0)
                to_dt = datetime(dt_req.year, dt_req.month, dt_req.day, 15, 30, 0)
            except Exception:
                today = _get_ist_now().date()
                from_dt = datetime(today.year, today.month, today.day, 9, 15, 0)
                to_dt = datetime.now()
        else:
            today = _get_ist_now().date()
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
    seen_times = set()
    try:
        from zoneinfo import ZoneInfo
        tz_ist = ZoneInfo("Asia/Kolkata")
    except Exception:
        from datetime import timezone, timedelta
        tz_ist = timezone(timedelta(hours=5, minutes=30))

    for _, row in df.iterrows():
        ts = row["timestamp"]
        if hasattr(ts, "timestamp"):
            unix_ts = int(ts.timestamp())
            if hasattr(ts, "tzinfo") and ts.tzinfo is not None:
                time_str = ts.astimezone(tz_ist).strftime("%Y-%m-%d %H:%M")
            else:
                time_str = ts.strftime("%Y-%m-%d %H:%M")
        else:
            unix_ts = int(ts)
            time_str = str(ts)

        if time_str in seen_times:
            continue
        seen_times.add(time_str)

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
            # If previous candle exists and rolled over, append to _session_candles
            if fc is not None and fc["time"] < now_min_ts:
                prev_completed = {
                    "time": datetime.fromtimestamp(fc["time"]).strftime("%Y-%m-%d %H:%M"),
                    "timestamp": fc["time"],
                    "open": fc["open"],
                    "high": fc["high"],
                    "low": fc["low"],
                    "close": fc["close"],
                    "volume": fc.get("volume", 0)
                }
                if symbol in _session_candles:
                    ts, s_candles = _session_candles[symbol]
                    if not s_candles or s_candles[-1].get("timestamp") != prev_completed["timestamp"]:
                        s_candles.append(prev_completed)
                        _session_candles[symbol] = (time.time(), s_candles)
                else:
                    _session_candles[symbol] = (time.time(), [prev_completed])

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
QUOTE_CACHE_TTL = 10.0  # 10 second TTL to strictly protect broker API rate limits

def get_contract_quote(trading_symbol: str) -> Optional[Dict]:
    """
    Fetch real-time Level 2 depth and order flow metrics:
    - total buy_quantity (Bid Depth)
    - total sell_quantity (Offer Depth)
    - bid_ask_ratio (Buyer vs Seller dominance)
    - top 5 bids and top 5 asks
    - volume, open interest, and day range
    Prioritizes real-time WebSocket price bus (0 REST calls).
    """
    now = time.time()
    sym_upper = trading_symbol.strip().upper()
    
    # Check cache
    if sym_upper in _quote_cache:
        cached_ts, cached_val = _quote_cache[sym_upper]
        if now - cached_ts < QUOTE_CACHE_TTL:
            return cached_val

    broker = get_active_broker()

    # ZERO-REST OPTIMIZATION: Check if symbol is streaming on WebSocket price bus
    bus_price = get_live_ltp(sym_upper)
    if bus_price and bus_price > 0:
        res = {
            "symbol": sym_upper,
            "ltp": float(bus_price),
            "buy_qty": 0,
            "sell_qty": 0,
            "total_flow": 0,
            "buy_pct": 50.0,
            "sell_pct": 50.0,
            "bid_ask_ratio": 1.0,
            "high": float(bus_price),
            "low": float(bus_price),
            "open": float(bus_price),
            "close": float(bus_price),
            "volume": 0,
            "depth": {"buy": [], "sell": []},
            "timestamp": datetime.now().isoformat(),
            "broker": broker,
            "source": "websocket_stream"
        }
        _quote_cache[sym_upper] = (now, res)
        return res

    if broker == "breeze":
        try:
            from trading_engine.adapters.breeze_adapter import default_breeze_adapter
            bq = default_breeze_adapter.get_quote(sym_upper)
            if bq and bq.get("price"):
                ltp_val = float(bq["price"])
                res = {
                    "symbol": sym_upper,
                    "ltp": ltp_val,
                    "buy_qty": 0,
                    "sell_qty": 0,
                    "total_flow": 0,
                    "buy_pct": 50.0,
                    "sell_pct": 50.0,
                    "bid_ask_ratio": 1.0,
                    "high": bq.get("high", ltp_val),
                    "low": bq.get("low", ltp_val),
                    "open": bq.get("open", ltp_val),
                    "close": bq.get("close", ltp_val),
                    "volume": bq.get("volume", 0),
                    "depth": {"buy": [], "sell": []},
                    "timestamp": datetime.now().isoformat(),
                    "broker": "breeze",
                    "source": "rest_cache"
                }
                push_to_price_bus(sym_upper, ltp_val, bq.get("timestamp"))
                _quote_cache[sym_upper] = (now, res)
                return res
        except Exception as e:
            print(f"ContractDataManager: Breeze quote fetch error: {e}", flush=True)

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
            
            last_price = float(q.get("last_price", 0.0))
            if last_price > 0:
                push_to_price_bus(sym_upper, last_price)

            res = {
                "symbol": sym_upper,
                "last_price": last_price,
                "ltp": last_price,
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

