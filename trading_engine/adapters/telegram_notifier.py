"""
Telegram Notifications Adapter for AI Trader Engine.
Sends real-time alerts, tunnel URL updates, and trade notifications to Telegram.
"""
import os
import logging
from datetime import datetime
from zoneinfo import ZoneInfo
from pathlib import Path
from typing import Optional, Dict, Any
import requests
from dotenv import load_dotenv

# Ensure .env is loaded from workspace root
ENV_PATH = Path(__file__).resolve().parent.parent.parent / ".env"
if ENV_PATH.exists():
    load_dotenv(dotenv_path=ENV_PATH)
else:
    load_dotenv()

logger = logging.getLogger("telegram_notifier")
logger.setLevel(logging.INFO)


class TelegramNotifier:
    """Manages Telegram Bot notifications and tunnel URL dispatching."""

    def __init__(self):
        self.reload_config()

    def reload_config(self):
        """Reloads configuration dynamically from .env or environment."""
        if ENV_PATH.exists():
            load_dotenv(dotenv_path=ENV_PATH, override=True)
        self.token: str = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
        self.chat_id: str = os.getenv("TELEGRAM_CHAT_ID", "811560107").strip()
        self.enabled: bool = os.getenv("TELEGRAM_ENABLED", "true").lower() in ("true", "1", "yes")

    def is_configured(self) -> bool:
        """Returns True if bot token and chat ID are valid and notifications are enabled."""
        self.reload_config()
        return bool(self.enabled and self.token and self.chat_id)

    def send_message(self, text: str, parse_mode: str = "HTML") -> bool:
        """
        Sends a message to the configured Telegram chat.
        Returns True if successful, False otherwise.
        """
        self.reload_config()
        if not self.enabled:
            logger.info("Telegram notifications are disabled (TELEGRAM_ENABLED=false).")
            return False

        if not self.token:
            logger.warning("Telegram Bot Token is not set in .env. Waiting for user configuration.")
            return False

        if not self.chat_id:
            logger.warning("Telegram Chat ID is not set.")
            return False

        url = f"https://api.telegram.org/bot{self.token}/sendMessage"
        payload = {
            "chat_id": self.chat_id,
            "text": text,
            "parse_mode": parse_mode,
            "disable_web_page_preview": False
        }

        try:
            resp = requests.post(url, json=payload, timeout=10)
            if resp.status_code == 200:
                logger.info("Telegram message delivered successfully.")
                return True
            else:
                logger.error(f"Telegram API error {resp.status_code}: {resp.text}")
                return False
        except Exception as e:
            logger.error(f"Failed to send Telegram message: {e}")
            return False

    def send_tunnel_url_alert(self, public_url: str, is_new: bool = True) -> bool:
        """
        Sends an formatted notification with the new Cloudflare public URL.
        """
        now_ist = datetime.now(ZoneInfo("Asia/Kolkata")).strftime("%Y-%m-%d %H:%M:%S IST")
        title_prefix = "🚀 <b>AI Trader Live Dashboard Online</b>" if is_new else "🔄 <b>Cloudflare Tunnel URL Updated</b>"

        message = (
            f"{title_prefix}\n\n"
            f"🔗 <b>Public Access URL:</b>\n"
            f"<a href=\"{public_url}\">{public_url}</a>\n\n"
            f"⚡ <b>Active Features:</b>\n"
            f"• 1-Min Live Candlestick Chart (IST Timezone)\n"
            f"• Real-Time AI Predictor Gauges\n"
            f"• Live NIFTY Option Chain Matrix\n"
            f"• Backdated Market Session Simulator\n"
            f"• Autonomous Paper Trading with Iceberg Slicing\n\n"
            f"⏰ <b>Generated:</b> <code>{now_ist}</code>\n"
            f"🛡 <b>Network:</b> Cloudflare Tunnel (SSL Encrypted)"
        )
        return self.send_message(message)

    def test_connection(self) -> Dict[str, Any]:
        """Tests the configured Telegram token against the getMe endpoint."""
        self.reload_config()
        if not self.token:
            return {"status": "unconfigured", "message": "TELEGRAM_BOT_TOKEN is empty in .env"}
        try:
            resp = requests.get(f"https://api.telegram.org/bot{self.token}/getMe", timeout=10)
            data = resp.json()
            if resp.status_code == 200 and data.get("ok"):
                bot_info = data.get("result", {})
                return {
                    "status": "connected",
                    "bot_name": bot_info.get("first_name"),
                    "username": bot_info.get("username"),
                    "chat_id": self.chat_id
                }
            return {"status": "error", "response": data}
        except Exception as e:
            return {"status": "error", "error": str(e)}


# Singleton instance
default_telegram_notifier = TelegramNotifier()
