"""
Trading Engine Global Configuration.
"""
from dataclasses import dataclass, field
import os

def _default_db_url() -> str:
    try:
        from trading_engine.core.keyvault import get_secret
        return get_secret("DATABASE_URL") or os.getenv("DATABASE_URL", "postgresql+psycopg2://postgres:postgres@localhost:5432/trading")
    except Exception:
        return os.getenv("DATABASE_URL", "postgresql+psycopg2://postgres:postgres@localhost:5432/trading")

@dataclass
class EngineConfig:
    # Database
    db_url: str = field(default_factory=_default_db_url)
    symbol: str = "NIFTY 50"
    futures_symbol: str = "NIFTY-I"
    option_prefix: str = "NIFTY"
    tz: str = "Asia/Kolkata"

    # Evaluation & Lookback
    start_date: str = "2026-04-01"
    end_date: str = "2026-09-30"
    lookback_candles: int = 89490      # Optimal session_prime lookback anchor (exp_id 80)

    # Strategy Parameters (Economic Target >=10% & High Profit Architecture)
    target_atr_mult: float = 1.50      # 1.50x ATR Target (matches benchmark multi-crore model)
    stop_atr_mult: float = 1.50        # 1.50x ATR Stop Loss
    trading_start_time: str = "10:05"  # 10:05 IST start (avoids open 50-candle chop)
    trading_end_time: str = "15:15"    # 15:15 IST end (captures full afternoon expansion)
    loss_cooldown_mins: int = 0        # Unblocked entry stream matching 2.86 Cr benchmark
    consecutive_losses_for_cooldown: int = 999
    retrain_monthly: bool = False      # Pure benchmark static model architecture
    use_trailing_ladder: bool = False  # Pure target / stop exit (matches benchmark multi-crore system)
    be_trigger_mult: float = 0.85      # Trigger profit lock when trade reaches >= +0.85x ATR
    be_lock_amt_mult: float = 0.50     # Lock +0.50x ATR
    profit_lock_trigger: float = 1.20  # Trigger secondary lock when trade reaches >= +1.20x ATR
    profit_lock_amt: float = 0.80      # Lock +0.80x ATR
    max_trades_per_day: int = 5
    score_threshold: float = 0.55
    use_confirmed_sar: bool = False    # Pure direct setup
    max_or_pos_call: float = 0.88      # Exhaustion Filter: Avoid buying Call at top of Opening Range (>0.88)
    min_or_pos_put: float = 0.12       # Exhaustion Filter: Avoid buying Put at bottom of Opening Range (<0.12)

    # Portfolio & Execution
    initial_capital: float = 100000.0   # 1 Lakh INR
    lot_size: int = 65                 # NIFTY lot size (65 units)
    risk_per_trade_pct: float = 0.07   # 7.0% compounding risk
    slippage_per_side: float = 0.50    # ₹0.50 baseline bid-ask spread
    brokerage_per_order: float = 20.0  # ₹20 flat brokerage per order leg

    # Institutional Iceberg & Slicing Execution Engine
    freeze_lots: int = 30              # NSE statutory freeze limit per single leg (1,950 units)
    max_slices: int = 35               # Maximum slices per parent order (supports up to 1,050 lots)
    max_lots: int = 1000               # Multi-crore compounding ceiling: 1,000 lots (65,000 units)
    depth_slip_per_slice: float = 0.05 # Realistic market depth impact: +₹0.05 slippage per additional slice
    execution_mode: str = "custom_slicing" # 'broker_native' (max 10 legs), 'custom_slicing', 'strike_dispersion'
    strike_dispersion: bool = True     # Disperse across ATM/ITM/OTM if order > 300 lots to minimize market impact
    slice_delay_ms: int = 250          # 250ms interval between sequential child order submissions


INDEX_SPEC_REGISTRY = {
    "NIFTY": {
        "display_name": "NIFTY 50",
        "kite_spot": "NSE:NIFTY 50",
        "kite_fut_prefix": "NIFTY",
        "kite_token": 256265,
        "breeze_stock_code": "NIFTY",
        "exchange_code": "NFO",
        "cash_exchange": "NSE",
        "strike_gap": 50,
        "lot_size": 65,
        "freeze_limit_units": 1800,
        "freeze_lots": 27,
    },
    "BANKNIFTY": {
        "display_name": "BANK NIFTY",
        "kite_spot": "NSE:NIFTY BANK",
        "kite_fut_prefix": "BANKNIFTY",
        "kite_token": 260105,
        "breeze_stock_code": "CNXBAN",
        "exchange_code": "NFO",
        "cash_exchange": "NSE",
        "strike_gap": 100,
        "lot_size": 15,
        "freeze_limit_units": 900,
        "freeze_lots": 60,
    },
    "SENSEX": {
        "display_name": "SENSEX",
        "kite_spot": "BSE:SENSEX",
        "kite_fut_prefix": "SENSEX",
        "kite_token": 265,
        "breeze_stock_code": "BSX",
        "exchange_code": "BFO",
        "cash_exchange": "BSE",
        "strike_gap": 100,
        "lot_size": 10,
        "freeze_limit_units": 1000,
        "freeze_lots": 100,
    },
    "FINNIFTY": {
        "display_name": "FINNIFTY",
        "kite_spot": "NSE:NIFTY FIN SERVICE",
        "kite_fut_prefix": "FINNIFTY",
        "kite_token": 257801,
        "breeze_stock_code": "NIFFIN",
        "exchange_code": "NFO",
        "cash_exchange": "NSE",
        "strike_gap": 50,
        "lot_size": 25,
        "freeze_limit_units": 1800,
        "freeze_lots": 72,
    },
    "MIDCPNIFTY": {
        "display_name": "MIDCAP NIFTY",
        "kite_spot": "NSE:NIFTY MID SELECT",
        "kite_fut_prefix": "MIDCPNIFTY",
        "kite_token": 288009,
        "breeze_stock_code": "NIFMID",
        "exchange_code": "NFO",
        "cash_exchange": "NSE",
        "strike_gap": 25,
        "lot_size": 75,
        "freeze_limit_units": 4200,
        "freeze_lots": 56,
    },
}


def get_index_spec(underlying: str) -> dict:
    """Returns index specifications by underlying symbol with safe fallback to NIFTY."""
    if not underlying:
        return INDEX_SPEC_REGISTRY["NIFTY"]
    u = underlying.strip().upper()
    if u in INDEX_SPEC_REGISTRY:
        return INDEX_SPEC_REGISTRY[u]
    if "BANK" in u:
        return INDEX_SPEC_REGISTRY["BANKNIFTY"]
    if "SENSEX" in u or "BSX" in u:
        return INDEX_SPEC_REGISTRY["SENSEX"]
    if "FIN" in u:
        return INDEX_SPEC_REGISTRY["FINNIFTY"]
    if "MID" in u:
        return INDEX_SPEC_REGISTRY["MIDCPNIFTY"]
    return INDEX_SPEC_REGISTRY["NIFTY"]


# ─────────────────────────────────────────────────────────────────────────────
# BREEZE SDK COMPATIBILITY LAYER
# ─────────────────────────────────────────────────────────────────────────────
# ICICI BreezeConnect SDK has an un-namespaced 'import config' which collides
# with trading_engine/config.py when trading_engine is in sys.path.
# Providing these direct constants and __getattr__ proxy ensures 100% collision immunity.
SECURITY_MASTER_URL: str = "https://directlink.icicidirect.com/MotherAppMaster/SecurityMaster.zip"
API_URL: str = "https://api.icicidirect.com/breezeapi/api/v1/"
BREEZE_NEW_URL: str = "https://breezeapi.icicidirect.com/api/v2/"
LIVE_FEEDS_URL: str = "https://livefeeds.icicidirect.com"
LIVE_STREAM_URL: str = "https://livestream.icicidirect.com"
LIVE_OHLC_STREAM_URL: str = "https://breezeapi.icicidirect.com"
STOCK_SCRIPT_CSV_URL: str = "https://traderweb.icicidirect.com/Content/File/txtFile/ScripFile/StockScriptNew.csv"


def __getattr__(name: str):
    """Fallback proxy to breeze_connect.config to prevent third-party SDK namespace collisions."""
    try:
        import sys, importlib.util, pathlib
        for p in sys.path:
            cand = pathlib.Path(p) / "breeze_connect" / "config.py"
            if cand.exists():
                spec = importlib.util.spec_from_file_location("breeze_connect_config_proxy", str(cand))
                mod = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(mod)
                val = getattr(mod, name)
                globals()[name] = val
                return val
    except Exception:
        pass
    raise AttributeError(f"module 'config' has no attribute '{name}'")


