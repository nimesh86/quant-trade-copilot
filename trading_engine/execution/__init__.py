"""
Institutional Execution Module.
Provides algorithmic order slicing, statutory freeze limit compliance,
Level 2 depth slippage modeling, and strike dispersion.
"""
from .order_slicer import OrderSlicer, ChildSlice, SlicedExecutionReport

__all__ = ["OrderSlicer", "ChildSlice", "SlicedExecutionReport"]
