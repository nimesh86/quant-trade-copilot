"""
2-Factor Authentication (2FA) & Session Guard for Quant Trade Engine
===================================================================
Provides robust, hardware-aligned RFC 6238 TOTP authentication and
cryptographic session verification to protect the trading console,
API endpoints, and WebSocket channels from unauthorized access.
"""

import os
import time
import json
import secrets
import io
import pyotp
import qrcode
from qrcode.image.svg import SvgPathImage
from pathlib import Path
from typing import Dict, Any, Optional, Tuple
from fastapi import Request

from trading_engine.core.keyvault import get_secret, set_secret

# Session persistence path
DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)
SESSIONS_FILE = DATA_DIR / "sessions.json"

# Session configuration: 7 days validity
SESSION_TTL_SECONDS = 7 * 24 * 3600

# In-memory session cache
_ACTIVE_SESSIONS: Dict[str, Dict[str, Any]] = {}


def _load_sessions():
    global _ACTIVE_SESSIONS
    if SESSIONS_FILE.exists():
        try:
            with open(SESSIONS_FILE, "r") as f:
                data = json.load(f)
                now = time.time()
                # Clean up expired sessions
                _ACTIVE_SESSIONS = {
                    tok: info for tok, info in data.items()
                    if (now - info.get("created_at", 0)) < SESSION_TTL_SECONDS
                }
        except Exception:
            _ACTIVE_SESSIONS = {}


def _save_sessions():
    try:
        with open(SESSIONS_FILE, "w") as f:
            json.dump(_ACTIVE_SESSIONS, f, indent=2)
    except Exception:
        pass


# Initialize sessions on module import
_load_sessions()


def get_or_create_totp_secret() -> str:
    """Retrieves or creates a 32-character Base32 TOTP secret stored in KeyVault."""
    secret = get_secret("APP_TOTP_SECRET")
    if not secret or len(secret) < 16:
        secret = pyotp.random_base32()
        set_secret("APP_TOTP_SECRET", secret)
    return secret


def get_master_pin() -> str:
    """Returns the master security PIN (default: 'quant2026')."""
    pin = get_secret("APP_2FA_PIN")
    if not pin:
        pin = "quant2026"
        set_secret("APP_2FA_PIN", pin)
    return pin


def set_master_pin(new_pin: str) -> bool:
    """Updates the master PIN in KeyVault."""
    if not new_pin or len(str(new_pin).strip()) < 4:
        return False
    return set_secret("APP_2FA_PIN", str(new_pin).strip())


def get_totp_uri(secret: Optional[str] = None) -> str:
    """Generates the standard otpauth:// URI for authenticator apps."""
    sec = secret or get_or_create_totp_secret()
    return pyotp.TOTP(sec).provisioning_uri(
        name="admin",
        issuer_name="Quant Trade Engine"
    )


def generate_qr_svg(secret: Optional[str] = None) -> str:
    """Generates an embedded SVG QR code for Google Authenticator enrollment."""
    uri = get_totp_uri(secret)
    qr = qrcode.QRCode(
        version=1,
        box_size=8,
        border=2,
        image_factory=SvgPathImage
    )
    qr.add_data(uri)
    qr.make(fit=True)
    img = qr.make_image()
    buf = io.BytesIO()
    img.save(buf)
    return buf.getvalue().decode("utf-8")


def verify_credentials(pin: str, totp_code: str) -> Tuple[bool, str]:
    """
    Verifies Master PIN and 6-digit TOTP code.
    Allows a 30-second clock skew tolerance (valid_window=1).
    """
    expected_pin = get_master_pin()
    if str(pin).strip() != expected_pin:
        return False, "Incorrect Master PIN / Password"

    secret = get_or_create_totp_secret()
    totp = pyotp.TOTP(secret)
    code_clean = str(totp_code).strip().replace(" ", "").replace("-", "")

    if not code_clean.isdigit() or len(code_clean) != 6:
        return False, "2FA Code must be exactly 6 digits"

    if not totp.verify(code_clean, valid_window=1):
        return False, "Invalid or expired 2FA code. Please re-check your Authenticator app."

    return True, "Verification successful"


def create_session(user_agent: str = "", client_ip: str = "") -> str:
    """Generates and stores a new cryptographically secure session token."""
    token = secrets.token_urlsafe(32)
    _ACTIVE_SESSIONS[token] = {
        "created_at": time.time(),
        "user_agent": user_agent,
        "client_ip": client_ip
    }
    _save_sessions()
    return token


def validate_session(token: Optional[str]) -> bool:
    """Checks whether the given session token is valid and active."""
    if not token or not isinstance(token, str):
        return False
    session_info = _ACTIVE_SESSIONS.get(token)
    if not session_info:
        return False
    age = time.time() - session_info.get("created_at", 0)
    if age > SESSION_TTL_SECONDS:
        revoke_session(token)
        return False
    return True


def revoke_session(token: Optional[str]):
    """Invalidates the session token."""
    if token and token in _ACTIVE_SESSIONS:
        del _ACTIVE_SESSIONS[token]
        _save_sessions()


def extract_session_token(request: Request) -> Optional[str]:
    """Extracts session token from cookie, Authorization header, or X-Quant-Token."""
    # 1. Cookie
    tok = request.cookies.get("quant_session")
    if tok:
        return tok
    # 2. X-Quant-Token header
    tok = request.headers.get("X-Quant-Token")
    if tok:
        return tok
    # 3. Authorization: Bearer <token>
    auth_header = request.headers.get("Authorization")
    if auth_header and auth_header.startswith("Bearer "):
        return auth_header[7:].strip()
    return None

