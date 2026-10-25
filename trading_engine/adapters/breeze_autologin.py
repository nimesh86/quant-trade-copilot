"""
trading_engine/adapters/breeze_autologin.py
===========================================
100% Zero-Touch Automated Daily ICICI Direct Breeze API Login Engine with TOTP.

Uses Playwright and pyotp to:
1. Navigate to ICICI Direct Breeze OAuth login portal.
2. Fill User ID and Password.
3. Compute dynamic 6-digit TOTP code via pyotp from the base32 TOTP secret.
4. Submit 2FA verification.
5. Intercept redirect URL containing the daily `apisession` token.
6. Initialize BreezeConnect and generate session (`breeze.generate_session(api_secret, session_token)`).
7. Encrypt and save BREEZE_SESSION_TOKEN into KeyVault.
8. Hot-reload the live Breeze adapter in memory with zero server downtime.
"""

import os
import sys
import time
import logging
import urllib.parse
from pathlib import Path
from typing import Dict, Any, Optional

# Ensure workspace root is in sys.path
workspace_root = str(Path(__file__).resolve().parent.parent.parent)
if workspace_root not in sys.path:
    sys.path.insert(0, workspace_root)

import pyotp

from trading_engine.core.keyvault import get_secret, set_secret

logger = logging.getLogger("breeze_autologin")


class BreezeAutoLogin:
    """
    Automates the full 2FA ICICI Direct Breeze login sequence.
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        api_secret: Optional[str] = None,
        user_id: Optional[str] = None,
        password: Optional[str] = None,
        totp_key: Optional[str] = None,
    ):
        self.api_key = (api_key or get_secret("BREEZE_API_KEY") or "").strip()
        self.api_secret = (api_secret or get_secret("BREEZE_API_SECRET") or "").strip()
        self.user_id = (user_id or get_secret("BREEZE_USER_ID") or "").strip()
        self.password = (password or get_secret("BREEZE_PASSWORD") or "").strip()
        
        raw_totp = (totp_key or get_secret("BREEZE_TOTP_KEY") or "").strip()
        self.totp_key = raw_totp.replace(" ", "").upper()

    def generate_current_totp(self) -> str:
        """Generates dynamic 6-digit TOTP code from secret key."""
        if not self.totp_key:
            raise ValueError("BREEZE_TOTP_KEY is missing or empty in KeyVault.")
        totp = pyotp.TOTP(self.totp_key)
        return totp.now()

    def check_prerequisites(self) -> Dict[str, Any]:
        """Validates that all required credentials exist."""
        missing = []
        if not self.api_key:
            missing.append("BREEZE_API_KEY")
        if not self.api_secret:
            missing.append("BREEZE_API_SECRET")
        if not self.user_id:
            missing.append("BREEZE_USER_ID")
        if not self.password:
            missing.append("BREEZE_PASSWORD")
        if not self.totp_key:
            missing.append("BREEZE_TOTP_KEY")

        if missing:
            return {
                "success": False,
                "error": f"Missing required credentials in KeyVault: {', '.join(missing)}",
                "missing": missing
            }
        return {"success": True}

    def execute_login(self, timeout_sec: int = 30) -> Dict[str, Any]:
        """
        Executes the zero-touch Playwright login flow.
        Returns a dictionary with status, apisession, and Breeze session confirmation.
        """
        prereq = self.check_prerequisites()
        if not prereq["success"]:
            return prereq

        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            return {
                "success": False,
                "error": "playwright package is not installed. Run: pip install playwright && playwright install chromium"
            }

        encoded_api_key = urllib.parse.quote_plus(self.api_key)
        login_url = f"https://api.icicidirect.com/apiuser/login?api_key={encoded_api_key}"
        logger.info(f"Initiating zero-touch Breeze login for user: {self.user_id}...")

        captured_session_token: Optional[str] = None
        debug_dir = Path(os.path.expanduser("~/.trading_assistant/debug"))
        debug_dir.mkdir(parents=True, mode=0o700, exist_ok=True)
        screenshot_path = debug_dir / "last_screen.png"

        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=True,
                args=[
                    "--no-sandbox",
                    "--disable-setuid-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-gpu",
                ]
            )
            context = browser.new_context(
                user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
                viewport={"width": 1280, "height": 800}
            )
            page = context.new_page()

            def extract_token_from_url(url: str):
                nonlocal captured_session_token
                if "apisession=" in url or "API_Session=" in url:
                    parsed = urllib.parse.urlparse(url)
                    qs = urllib.parse.parse_qs(parsed.query)
                    token = qs.get("apisession") or qs.get("API_Session")
                    if token and token[0]:
                        captured_session_token = token[0].strip()
                        logger.info(f"Captured apisession token from URL: {captured_session_token[:4]}****")

            page.on("framenavigated", lambda frame: extract_token_from_url(frame.url))
            page.on("request", lambda req: extract_token_from_url(req.url))

            try:
                logger.info(f"Navigating to Breeze login portal: {login_url}")
                page.goto(login_url, wait_until="networkidle", timeout=15000)
                page.wait_for_timeout(1000)

                # Check if already redirected (e.g. active SSO session)
                extract_token_from_url(page.url)

                if not captured_session_token:
                    # 1. Fill User ID
                    if page.locator("#txtuid").count() > 0 and page.locator("#txtuid").is_visible():
                        page.fill("#txtuid", self.user_id)
                    else:
                        page.fill("input[name*='user' i], input[type='text']", self.user_id)

                    # 2. Fill Password
                    if page.locator("#txtPass").count() > 0 and page.locator("#txtPass").is_visible():
                        page.fill("#txtPass", self.password)
                    else:
                        page.fill("input[type='password']", self.password)

                    # 3. Check Terms and Conditions
                    if page.locator("#chkssTnc").count() > 0:
                        page.check("#chkssTnc")

                    # 4. Click Login Button (#btnSubmit)
                    if page.locator("#btnSubmit").count() > 0 and page.locator("#btnSubmit").is_visible():
                        page.click("#btnSubmit")
                    else:
                        page.click("input[type='button'], button[type='submit']")

                    page.wait_for_timeout(2500)
                    extract_token_from_url(page.url)

                # 5. Handle OTP / TOTP Verification Step
                if not captured_session_token:
                    totp_found = False
                    current_code = self.generate_current_totp()

                    # Wait up to 6s for OTP box(es)
                    for _ in range(6):
                        # Case A: 6 individual single-digit input boxes (ICICI Direct standard)
                        otp_boxes = page.locator("#pnlOTP input[tg-nm='otp']").all()
                        if not otp_boxes:
                            otp_boxes = page.locator("#dvgetotp input[type='text']").all()

                        if len(otp_boxes) >= 6:
                            totp_found = True
                            logger.info(f"Injecting dynamic 6-digit TOTP into 6 digit boxes...")
                            for idx in range(6):
                                otp_boxes[idx].fill(current_code[idx])
                            # Click Submit (#Button1)
                            if page.locator("#Button1").count() > 0:
                                page.click("#Button1")
                            elif page.locator("input[value='Submit'], button:has-text('Submit')").count() > 0:
                                page.click("input[value='Submit'], button:has-text('Submit')")
                            break

                        # Case B: Single 6-digit input box
                        single_box_sels = ["input[name*='totp' i]", "input[name*='otp' i]", "input[id*='otp' i]"]
                        for s_sel in single_box_sels:
                            if page.locator(s_sel).count() > 0 and page.locator(s_sel).first.is_visible():
                                totp_found = True
                                logger.info(f"Injecting dynamic TOTP into single box...")
                                page.fill(s_sel, current_code)
                                if page.locator("button[type='submit'], input[type='button']").count() > 0:
                                    page.click("button[type='submit'], input[type='button']")
                                break

                        if totp_found:
                            break
                        page.wait_for_timeout(1000)

                    # Wait for redirect to callback URL
                    start_time = time.time()
                    while time.time() - start_time < timeout_sec:
                        extract_token_from_url(page.url)
                        if captured_session_token:
                            break
                        page.wait_for_timeout(500)

                if not captured_session_token:
                    page.screenshot(path=str(screenshot_path))
                    return {
                        "success": False,
                        "error": f"Failed to capture apisession within {timeout_sec}s. Screenshot saved to {screenshot_path}",
                        "current_url": page.url
                    }

            except Exception as e:
                logger.error(f"Playwright automation error during Breeze login: {e}")
                try:
                    page.screenshot(path=str(screenshot_path))
                except Exception:
                    pass
                return {"success": False, "error": str(e)}
            finally:
                browser.close()

        # Step 5: Save token and generate session with Breeze SDK
        logger.info(f"Persisting BREEZE_SESSION_TOKEN into KeyVault...")
        set_secret("BREEZE_SESSION_TOKEN", captured_session_token)
        set_secret("ACTIVE_BROKER", "breeze")

        # Step 6: Initialize BreezeConnect SDK and generate daily session
        try:
            from breeze_connect import BreezeConnect
            breeze = BreezeConnect(api_key=self.api_key)
            session_res = breeze.generate_session(
                api_secret=self.api_secret,
                session_token=captured_session_token
            )
            logger.info(f"Breeze SDK session generated: {session_res}")

            # Hot reload adapter
            from trading_engine.adapters.breeze_adapter import default_breeze_adapter
            default_breeze_adapter.reset()
            default_breeze_adapter.connect()

            # Telegram notification
            try:
                from trading_engine.adapters.telegram_notifier import get_telegram_notifier
                tg = get_telegram_notifier()
                tg.send_system_alert(
                    "⚡ <b>Breeze Auto-Login Success</b>\n"
                    "100% Zero-Touch 2FA TOTP authentication completed.\n"
                    f"Session active for user <code>{self.user_id}</code>."
                )
            except Exception:
                pass

            return {
                "success": True,
                "apisession": captured_session_token,
                "breeze_session": session_res,
                "message": "100% Zero-Touch Breeze Auto-Login successful! Session active."
            }
        except Exception as e:
            logger.error(f"Failed to generate Breeze session with captured token: {e}")
            return {
                "success": False,
                "apisession": captured_session_token,
                "error": f"Captured apisession but failed generating Breeze session: {e}"
            }


def run_daily_auto_login() -> Dict[str, Any]:
    """Helper entrypoint for daily cron/timer execution."""
    engine = BreezeAutoLogin()
    return engine.execute_login()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    res = run_daily_auto_login()
    print("\nResult:", res)
    sys.exit(0 if res.get("success") else 1)
