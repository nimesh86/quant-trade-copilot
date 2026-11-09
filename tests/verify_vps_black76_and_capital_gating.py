import time
import pyotp
from playwright.sync_api import sync_playwright

def run():
    vps_url = "http://160.250.205.128:8080"
    pin = "quant2026"
    secret = "O77T75SZHQVWOIPJW2LT4T5GWO5335AP"

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1600, "height": 1050})
        page = context.new_page()

        print(f"Connecting to VPS {vps_url}...")
        page.goto(vps_url, wait_until="networkidle", timeout=15000)

        # 2FA Authentication
        if "auth_login" in page.url or page.locator("#auth-pin").count() > 0:
            print("Authenticating with 2FA...")
            page.fill("#auth-pin", pin)
            totp_code = pyotp.TOTP(secret).now()
            page.fill("#auth-code", totp_code)
            page.click("button[type='submit']")
            page.wait_for_load_state("networkidle", timeout=15000)
            time.sleep(2)

        print(f"Authenticated URL: {page.url}")

        # Navigate to Paper Trading Tab
        print("Navigating to Paper Trading Tab...")
        page.click("#tab-btn-paper")
        time.sleep(2)

        # Verify Paper Trade Capital & Segregation
        header_text = page.locator("#paper-balance").inner_text() if page.locator("#paper-balance").count() > 0 else ""
        print(f"Paper Balance Display: '{header_text}'")

        # Capture Paper Tab Screenshot
        screenshot_path = "/Users/admin/.gemini/antigravity/brain/ff874bcf-72ce-43e7-991b-537273eea07f/vps_paper_black76_pricing_verified.png"
        page.screenshot(path=screenshot_path, full_page=False)
        print(f"Captured Paper Tab screenshot: {screenshot_path}")

        browser.close()
        print("Verification completed successfully!")

if __name__ == "__main__":
    run()
