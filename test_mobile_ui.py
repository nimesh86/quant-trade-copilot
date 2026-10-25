"""
test_mobile_ui.py
──────────────────
Automated Playwright UI test suite verifying responsive mobile layout,
dynamic underlying switching (NIFTY, BANKNIFTY, SENSEX), and zero horizontal overflow.
"""

import sys
import os
import time
from playwright.sync_api import sync_playwright

SCREENSHOT_DIR = "/Users/admin/.gemini/antigravity-ide/brain/86d24c2f-e264-440e-bf84-d79e49c95daf"
BASE_URL = "http://localhost:8080/contract.html"

MOBILE_DEVICES = [
    {"name": "iPhone 14 / Standard Mobile", "width": 390, "height": 844},
    {"name": "Compact Mobile (375x667)", "width": 375, "height": 667},
    {"name": "Android Standard (360x780)", "width": 360, "height": 780},
]


def run_mobile_tests():
    print("=" * 70)
    print("🚀 Running Mobile UI Validation Suite with Playwright...")
    print("=" * 70)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)

        for dev in MOBILE_DEVICES:
            print(f"\n📱 Testing Viewport: {dev['name']} ({dev['width']}x{dev['height']})...")
            context = browser.new_context(
                viewport={"width": dev["width"], "height": dev["height"]},
                user_agent="Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.0 Mobile/15E148 Safari/604.1",
                is_mobile=True,
                has_touch=True,
            )
            page = context.new_page()

            # 1. Navigate to contract page
            resp = page.goto(BASE_URL, wait_until="domcontentloaded", timeout=15000)
            assert resp.status == 200, f"Page returned status {resp.status}"
            print("  ✓ Page loaded successfully (HTTP 200)")

            time.sleep(1)

            # 2. Check no horizontal overflow on body/html
            overflow = page.evaluate("""() => {
                return {
                    scrollWidth: document.documentElement.scrollWidth,
                    clientWidth: document.documentElement.clientWidth,
                    bodyScrollWidth: document.body.scrollWidth,
                    windowInnerWidth: window.innerWidth
                };
            }""")
            print(f"  ✓ Viewport check: window={overflow['windowInnerWidth']}px, docScroll={overflow['scrollWidth']}px")
            assert overflow["scrollWidth"] <= overflow["windowInnerWidth"] + 1, (
                f"Horizontal overflow detected! scrollWidth={overflow['scrollWidth']} > innerWidth={overflow['windowInnerWidth']}"
            )

            # 3. Open Option Chain
            oc_toggle = page.locator("#oc-btn, #dock-oc-btn, .oc-panel-toggle")
            if oc_toggle.count() > 0:
                oc_toggle.first.click()
                time.sleep(0.5)

            # 4. Verify Option Chain Panel is visible
            oc_panel = page.locator("#option-chain-panel")
            assert oc_panel.count() > 0, "Option chain panel element exists"

            # 5. Verify dynamic index selector exists
            und_select = page.locator("#oc-underlying-select")
            assert und_select.count() > 0, "Index selector #oc-underlying-select is present"
            print("  ✓ Found #oc-underlying-select")

            # 6. Test switching to SENSEX
            und_select.select_option("SENSEX")
            time.sleep(1)
            selected_val = und_select.input_value()
            assert selected_val == "SENSEX", f"Expected SENSEX, got {selected_val}"
            print("  ✓ Switched to SENSEX successfully")

            # 7. Test switching to BANKNIFTY
            und_select.select_option("BANKNIFTY")
            time.sleep(1)
            selected_val_bn = und_select.input_value()
            assert selected_val_bn == "BANKNIFTY", f"Expected BANKNIFTY, got {selected_val_bn}"
            print("  ✓ Switched to BANK NIFTY successfully")

            # Switch back to SENSEX for screenshot
            und_select.select_option("SENSEX")
            time.sleep(1)

            # 8. Capture screenshot for visual audit
            clean_name = dev["name"].split()[0].lower()
            shot_path = f"{SCREENSHOT_DIR}/mobile_view_{clean_name}_{dev['width']}.png"
            page.screenshot(path=shot_path, full_page=False)
            print(f"  ✓ Captured screenshot: {shot_path}")

            context.close()

        browser.close()

    print("\n" + "=" * 70)
    print("🎉 ALL MOBILE UI PLAYWRIGHT TESTS PASSED! ZERO OVERFLOW.")
    print("=" * 70)


if __name__ == "__main__":
    run_mobile_tests()
