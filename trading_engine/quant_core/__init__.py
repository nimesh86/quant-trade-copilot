"""
Layer A: Quant Core - Single Source of Truth
─────────────────────────────────────────────
Pure quantitative mathematical, pricing, signal, sizing, and lifecycle logic
shared identically between Backtest Engine (Layer B) and Autonomous Live Engine (Layer C).
"""
from trading_engine.quant_core.contracts import (
    Bar,
    Signal,
    OrderAction,
    OrderSide,
    ExitReason,
    OptionContract,
    VerificationResult,
    OrderSlice,
    Position,
    StatutoryCharges,
    TradeRecord
)
from trading_engine.quant_core.costs import CostCalculator
from trading_engine.quant_core.option_resolver import OptionResolver
from trading_engine.quant_core.option_verifier import OptionVerifier
from trading_engine.quant_core.position_sizer import PositionSizer
from trading_engine.quant_core.lifecycle import TradeLifecycleManager
from trading_engine.quant_core.option_detector import SmartOptionDetector, DEFAULT_ATM_INTERVAL_MINUTES
from trading_engine.quant_core.sr_zone_manager import SRZone, SRZoneManager

__all__ = [
    # Contracts & Enums
    "Bar",
    "Signal",
    "OrderAction",
    "OrderSide",
    "ExitReason",
    "OptionContract",
    "VerificationResult",
    "OrderSlice",
    "Position",
    "StatutoryCharges",
    "TradeRecord",
    # Core Mathematical Calculators
    "CostCalculator",
    "OptionResolver",
    "OptionVerifier",
    "PositionSizer",
    "TradeLifecycleManager",
    "SmartOptionDetector",
    "DEFAULT_ATM_INTERVAL_MINUTES",
    "SRZone",
    "SRZoneManager"
]
