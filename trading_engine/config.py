"""
Trading Engine Global Configuration.
"""
from dataclasses import dataclass

@dataclass
class EngineConfig:
    # Database
    db_url: str = "postgresql+psycopg2://postgres:postgres@localhost:5432/trading"
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

