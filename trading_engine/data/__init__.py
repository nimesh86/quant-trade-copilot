"""
Local Storage & VPS Data Gateway
Embedded DuckDB storage and VPS client for Spot + Option synchronized feeds.
"""
from trading_engine.data.duckdb_store import LocalDataStore
from trading_engine.data.vps_client import VPSGatewayClient
from trading_engine.data.sync_bus import TimestampAlignmentBus

__all__ = [
    "LocalDataStore",
    "VPSGatewayClient",
    "TimestampAlignmentBus",
]
