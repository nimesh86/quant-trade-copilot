"""
Trading Engine Core Utilities
"""

def get_secret(key: str, default=None):
    from trading_engine.core.keyvault import get_secret as _gs
    return _gs(key, default)

def set_secret(key: str, value: str):
    from trading_engine.core.keyvault import set_secret as _ss
    return _ss(key, value)

__all__ = ["get_secret", "set_secret"]
