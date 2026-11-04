"""
Quant Core: Standardized Data Contracts & Types.
────────────────────────────────────────────────
Single source of truth data structures shared identically between
Backtest Engine (Layer B) and Autonomous Live Engine (Layer C).
"""
from dataclasses import dataclass, field
from datetime import datetime, date
from enum import Enum
from typing import Dict, Any, List, Optional


class OrderAction(str, Enum):
    """Supported trading actions (BUY enabled, SELL disabled/prepared for future)."""
    BUY_CE = "BUY_CE"
    BUY_PE = "BUY_PE"
    SELL_CE = "SELL_CE"  # Reserved for future Option Writing
    SELL_PE = "SELL_PE"  # Reserved for future Option Writing


class OrderSide(str, Enum):
    """Directional bias of signal."""
    CE = "CE"
    PE = "PE"


class ExitReason(str, Enum):
    """Standardized trade closing triggers."""
    TARGET = "TARGET"
    STOP_LOSS = "SL"
    TRAILING_SL = "TRAILING_SL"
    TIME_STOP = "TIME_STOP"                      # Theta decay stagnation exit (N minutes)
    TIME_EXIT = "TIME_EXIT"                      # 15:15 IST intraday square-off
    EXPIRY_CUTOFF = "EXPIRY_CUTOFF"              # Expiry day final hour risk cut
    SMART_EXIT_DISTRIBUTION = "SMART_EXIT_DISTRIBUTION"  # Institutional volume exit
    SMART_EXIT_POC = "SMART_EXIT_POC"            # Break below/above volume POC
    SAR_REVERSAL = "SAR"
    MANUAL = "MANUAL"


@dataclass
class Bar:
    """Standardized OHLCV candle representation."""
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0
    oi: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "timestamp": self.timestamp.isoformat() if isinstance(self.timestamp, datetime) else str(self.timestamp),
            "open": self.open,
            "high": self.high,
            "low": self.low,
            "close": self.close,
            "volume": self.volume,
            "oi": self.oi
        }


@dataclass
class OptionContract:
    """Standardized F&O Option Contract Descriptor."""
    symbol: str                         # e.g. "NIFTY26OCT24200CE"
    underlying: str                     # e.g. "NIFTY"
    strike: float                       # e.g. 24200.0
    option_type: str                    # "CE" or "PE"
    expiry_date: str                    # YYYY-MM-DD
    lot_size: int = 65                  # Lot size (65 for Nifty, 15 for BankNifty)
    tick_size: float = 0.05
    freeze_lots: int = 30               # Exchange freeze limit in lots

    def to_dict(self) -> Dict[str, Any]:
        return {
            "symbol": self.symbol,
            "underlying": self.underlying,
            "strike": self.strike,
            "optionType": self.option_type,
            "expiryDate": self.expiry_date,
            "lotSize": self.lot_size,
            "tickSize": self.tick_size,
            "freezeLots": self.freeze_lots
        }


@dataclass
class VerificationResult:
    """Result of 6-Point Option Signal Verification before execution."""
    passed: bool
    reason: str                         # Reason for pass or failure
    filter_failed: Optional[str] = None # e.g. "PREMIUM_CONFIRMATION", "EDGE_VS_COST"
    spot_move: float = 0.0
    option_move: float = 0.0
    effective_delta: float = 0.0
    metrics: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "passed": self.passed,
            "reason": self.reason,
            "filterFailed": self.filter_failed,
            "spotMove": round(self.spot_move, 2),
            "optionMove": round(self.option_move, 2),
            "effectiveDelta": round(self.effective_delta, 2),
            "metrics": self.metrics
        }


@dataclass
class Signal:
    """Deterministic trading signal generated on Spot Index."""
    timestamp: datetime
    symbol: str
    side: str                           # "CE" (Bullish) or "PE" (Bearish)
    price: float                        # Current spot / underlying price
    stop_loss: float                    # Initial spot stop loss price
    target: float                       # Target spot price
    atr: float                          # ATR at signal bar
    probability: float                  # Confidence score (0.0 to 1.0)
    indicator: str                      # e.g. "BSS Pro v5"
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "timestamp": self.timestamp.isoformat() if isinstance(self.timestamp, datetime) else str(self.timestamp),
            "symbol": self.symbol,
            "side": self.side,
            "price": self.price,
            "stop_loss": self.stop_loss,
            "target": self.target,
            "atr": self.atr,
            "probability": self.probability,
            "indicator": self.indicator,
            "metadata": self.metadata
        }


@dataclass
class OrderSlice:
    """Institutional sliced order leg compliant with exchange freeze limits."""
    slice_id: int
    lots: int
    units: int
    limit_price: float
    side: str
    trading_symbol: str = ""
    order_id: Optional[str] = None
    status: str = "PENDING"             # PENDING, FILLED, FAILED


@dataclass
class Position:
    """
    Active trade position tracked strictly in Option Premium terms.
    Shared identically by Backtest Engine and Autonomous Live Engine.
    """
    id: str
    underlying: str                     # e.g. "NIFTY"
    contract_symbol: str                # e.g. "NIFTY26OCT24200CE"
    action: OrderAction                 # BUY_CE or BUY_PE
    option_type: str                    # "CE" or "PE"
    strike: float                       # 24200.0
    lots: int
    units: int
    entry_time: datetime
    entry_premium: float                # Fill price of option contract
    entry_spot: float                   # Spot index price at entry
    initial_sl_premium: float           # Stop Loss in option premium
    current_sl_premium: float           # Current dynamic/trailing SL
    target_premium: float               # Target in option premium
    peak_premium: float                 # Highest premium reached while position active
    trailing_stage: int = 0
    strategy_name: str = "QuantCore"
    slices: List[OrderSlice] = field(default_factory=list)
    minutes_held: int = 0               # Duration in minutes (for Time Stop decay check)
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class StatutoryCharges:
    """Breakdown of statutory exchange, tax, and broker fees for Indian derivatives."""
    brokerage: float                    # Flat broker fee (e.g. ₹20 per executed leg)
    stt: float                          # STT (0.1% on option sell turnover)
    exchange_txn_fee: float             # NSE transaction charge (0.0505% on premium turnover)
    sebi_fee: float                     # SEBI turnover charge (₹10 / crore)
    stamp_duty: float                   # Stamp duty (0.003% on buy turnover)
    gst: float                          # 18% GST on (Brokerage + Txn Fee + SEBI)
    total: float                        # Total deductions

    def to_dict(self) -> Dict[str, float]:
        return {
            "brokerage": round(self.brokerage, 2),
            "stt": round(self.stt, 2),
            "exchange_txn_fee": round(self.exchange_txn_fee, 2),
            "sebi_fee": round(self.sebi_fee, 2),
            "stamp_duty": round(self.stamp_duty, 2),
            "gst": round(self.gst, 2),
            "total": round(self.total, 2)
        }


@dataclass
class TradeRecord:
    """Closed trade record matching ledger and performance reporting requirements."""
    id: str
    date: str                           # YYYY-MM-DD
    day: str                            # Monday, Tuesday...
    symbol: str                         # NIFTY, BANKNIFTY...
    contract_symbol: str                # e.g. NIFTY26OCT24200CE
    side: str                           # BULLISH / BEARISH
    action: str                         # BUY_CE / BUY_PE
    option_type: str                    # CE / PE
    strike: float                       # 24200.0
    indicator: str                      # Strategy name
    probability: str                    # e.g. "85%"
    entry_time: str                     # HH:MM:SS
    entry_rate: float                   # Premium entry price (e.g. ₹199.56)
    exit_time: str                      # HH:MM:SS
    exit_rate: float                    # Premium exit price
    exit_reason: str                    # Target, SL, Trailing SL, Time Stop...
    points: float                       # Premium points (+/-)
    gross_pnl: float                    # Pre-cost PnL = points * units
    charges: float                      # Statutory charges
    net_pnl: float                      # Realized Net PnL = gross - charges
    roi_pct: float                      # Return on invested capital %
    lots: int
    units: int
    charges_breakdown: Optional[StatutoryCharges] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "date": self.date,
            "day": self.day,
            "symbol": self.symbol,
            "contract": self.contract_symbol,
            "trend": self.side,
            "action": self.action,
            "optionType": self.option_type,
            "strike": self.strike,
            "indicator": self.indicator,
            "probability": self.probability,
            "entryTime": self.entry_time,
            "entryRate": round(self.entry_rate, 2),
            "exitTime": self.exit_time,
            "exitRate": round(self.exit_rate, 2),
            "reason": self.exit_reason,
            "points": round(self.points, 2),
            "gross": round(self.gross_pnl, 2),
            "charges": round(self.charges, 2),
            "net": round(self.net_pnl, 2),
            "roi": round(self.roi_pct, 2),
            "lots": self.lots,
            "units": self.units,
            "lotQty": f"{self.lots}({self.units})",
            # Quant Studio snake_case compatibility
            "net_pnl_inr": round(self.net_pnl, 2),
            "gross_pnl_inr": round(self.gross_pnl, 2),
            "total_charges_inr": round(self.charges, 2),
            "pnl_inr": round(self.net_pnl, 2),
            "entry_price": round(self.entry_rate, 2),
            "exit_price": round(self.exit_rate, 2),
            "pnl_points": round(self.points, 2),
            "entry_time": f"{self.date} {self.entry_time}",
            "exit_time": f"{self.date} {self.exit_time}",
            "entry_reason": self.indicator,
            "exit_reason": self.exit_reason,
            "charges_bifurcation": {
                "brokerage": self.charges_breakdown.brokerage if self.charges_breakdown else 40.0,
                "stt": self.charges_breakdown.stt if self.charges_breakdown else 0.0,
                "exchange_txn": self.charges_breakdown.exchange_txn_fee if self.charges_breakdown else 0.0,
                "gst": self.charges_breakdown.gst if self.charges_breakdown else 0.0,
                "stamp_duty": self.charges_breakdown.stamp_duty if self.charges_breakdown else 0.0,
                "sebi": self.charges_breakdown.sebi_fee if self.charges_breakdown else 0.0
            } if self.charges_breakdown else {},
            "chargesBreakdown": self.charges_breakdown.to_dict() if self.charges_breakdown else None,
            "candles": [],
            "metadata": {k: v for k, v in self.metadata.items() if k != "candles"} if self.metadata else {}
        }
