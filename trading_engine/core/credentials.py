"""
Unified Broker & System Credentials Model
─────────────────────────────────────────
Centralizes credential definition, validation, and serialization.
Eliminates scattered string literals and duplicate validation logic across
adapters, autologin flows, and server endpoints.
"""
from enum import Enum
from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional


class SecretKey(str, Enum):
    """
    Exhaustive single-source-of-truth enumeration of all KeyVault keys.
    String literals must NEVER be defined or repeated outside this Enum.
    """
    # Zerodha Kite Connect
    KITE_API_KEY = "KITE_API_KEY"
    KITE_ACCESS_TOKEN = "KITE_ACCESS_TOKEN"
    KITE_API_SECRET = "KITE_API_SECRET"

    # ICICI Direct Breeze
    BREEZE_API_KEY = "BREEZE_API_KEY"
    BREEZE_API_SECRET = "BREEZE_API_SECRET"
    BREEZE_USER_ID = "BREEZE_USER_ID"
    BREEZE_PASSWORD = "BREEZE_PASSWORD"
    BREEZE_TOTP_KEY = "BREEZE_TOTP_KEY"
    BREEZE_SESSION_TOKEN = "BREEZE_SESSION_TOKEN"

    # System & Alerts
    ACTIVE_BROKER = "ACTIVE_BROKER"
    TELEGRAM_BOT_TOKEN = "TELEGRAM_BOT_TOKEN"
    TELEGRAM_CHAT_ID = "TELEGRAM_CHAT_ID"
    TELEGRAM_ENABLED = "TELEGRAM_ENABLED"


@dataclass
class KiteCredentials:
    """Zerodha Kite Connect API Credentials."""
    api_key: str = ""
    access_token: str = ""
    api_secret: str = ""

    @property
    def is_configured(self) -> bool:
        """True if minimum trading credentials (key + token) are present."""
        return bool(
            self.api_key
            and self.access_token
            and len(self.api_key.strip()) > 3
            and len(self.access_token.strip()) > 10
        )

    @property
    def is_login_configured(self) -> bool:
        """True if key and secret are present for OAuth / request-token login."""
        return bool(
            self.api_key
            and self.api_secret
            and len(self.api_key.strip()) > 3
            and len(self.api_secret.strip()) > 3
        )

    def to_masked_dict(self) -> Dict[str, Any]:
        return {
            "configured": self.is_configured,
            "has_key": bool(self.api_key),
            "has_token": bool(self.access_token),
            "has_secret": bool(self.api_secret),
            "masked_key": f"{self.api_key[:4]}..." if len(self.api_key) > 6 else ("***" if self.api_key else "")
        }


@dataclass
class BreezeCredentials:
    """ICICI Direct Breeze API Credentials & Zero-Touch Login Parameters."""
    api_key: str = ""
    api_secret: str = ""
    user_id: str = ""
    password: str = ""
    totp_key: str = ""
    session_token: str = ""

    @property
    def is_configured(self) -> bool:
        """True if active session token and API keys exist for direct API calls."""
        return bool(
            self.api_key
            and self.api_secret
            and self.session_token
            and len(self.api_key.strip()) > 3
            and len(self.session_token.strip()) > 3
        )

    @property
    def clean_totp(self) -> str:
        """Normalized uppercase TOTP secret with whitespace removed."""
        return self.totp_key.replace(" ", "").upper()

    @property
    def is_autologin_ready(self) -> bool:
        """True if all 5 fields required for zero-touch browser autologin exist."""
        return len(self.missing_autologin_fields) == 0

    @property
    def missing_autologin_fields(self) -> List[str]:
        """Returns list of missing credential names required for autologin."""
        missing = []
        if not self.api_key.strip():
            missing.append(SecretKey.BREEZE_API_KEY.value)
        if not self.api_secret.strip():
            missing.append(SecretKey.BREEZE_API_SECRET.value)
        if not self.user_id.strip():
            missing.append(SecretKey.BREEZE_USER_ID.value)
        if not self.password.strip():
            missing.append(SecretKey.BREEZE_PASSWORD.value)
        if not self.clean_totp:
            missing.append(SecretKey.BREEZE_TOTP_KEY.value)
        return missing

    def to_masked_dict(self) -> Dict[str, Any]:
        return {
            "configured": self.is_configured,
            "autologin_ready": self.is_autologin_ready,
            "user_id": self.user_id,
            "has_key": bool(self.api_key),
            "has_secret": bool(self.api_secret),
            "has_password": bool(self.password),
            "has_totp": bool(self.totp_key),
            "has_session_token": bool(self.session_token),
            "masked_key": f"{self.api_key[:4]}..." if len(self.api_key) > 6 else ("***" if self.api_key else "")
        }


@dataclass
class TelegramCredentials:
    """Telegram Bot Alert Notification Credentials."""
    bot_token: str = ""
    chat_id: str = ""
    enabled: bool = False

    @property
    def is_configured(self) -> bool:
        return bool(self.bot_token.strip() and self.chat_id.strip())
