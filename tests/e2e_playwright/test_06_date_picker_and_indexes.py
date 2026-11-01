"""
Playwright E2E Test Suite 06: Date Picker & Underlying Index Switching
Verifies AC-06.1 to AC-07.2:
- Date picker defaults to latest available date.
- Changing date re-initializes replay session.
- Switching underlying index (e.g. BANK NIFTY) updates spot base and strike steps.
"""
from playwright.sync_api import sync_playwright, expect


def test_date_picker_and_indexes():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto("http://localhost:8080/studio", wait_until="networkidle")

        sel_date = page.locator("#select-replay-date")
        expect(sel_date).to_be_visible()

        # 1. Verify default date is latest
        assert "2026-10-30" in sel_date.input_value(), "Default date should be 2026-10-30"

        # 2. Change date to 2026-10-29
        sel_date.select_option("2026-10-29")
        page.wait_for_timeout(600)
        expect(page.locator("#console-logs")).to_contain_text("2026-10-29")

        # 3. Switch underlying index to BANK NIFTY
        sel_und = page.locator("#select-underlying")
        sel_und.select_option("BANKNIFTY")
        page.wait_for_timeout(600)

        # 4. Verify strikes update to Bank Nifty levels (~52,000)
        sel_strike = page.locator("#select-strike")
        expect(sel_strike).to_contain_text("52,000")
        expect(page.locator("#tab-ce")).to_contain_text("52000 CE")

        # 5. Switch to SENSEX
        sel_und.select_option("SENSEX")
        page.wait_for_timeout(600)
        expect(sel_strike).to_contain_text("79,400")

        page.screenshot(path="tests/e2e_playwright/screenshots/test_06_dates_indexes.png")
        browser.close()


if __name__ == "__main__":
    test_date_picker_and_indexes()
    print("✅ Test 06 Passed!")
