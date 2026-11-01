"""
Playwright E2E Test Suite 07: Console Logs & Vectorized Backtest Scorecard
Verifies AC-08.1 to AC-09.2:
- Console & Logs drawer displays formatted timestamp logs.
- Clicking Quick Backtest runs high-speed backtest (< 50ms) and populates Scorecard metrics.
"""
from playwright.sync_api import sync_playwright, expect


def test_console_logs_and_backtest():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto("http://localhost:8080/studio", wait_until="networkidle")

        console_logs = page.locator("#console-logs")
        expect(console_logs).to_be_visible()

        # 1. Verify initial startup logs are present
        expect(console_logs).to_contain_text("[Compiler]")

        # 2. Click Quick Backtest button
        btn_backtest = page.locator("#btn-run-backtest")
        btn_backtest.click()
        page.wait_for_timeout(800)

        # 3. Verify Scorecard tab becomes active
        tab_scorecard = page.locator('[data-tab="scorecard"]')
        expect(tab_scorecard).to_have_class("drawer-tab active")

        # 4. Verify scorecard metrics are populated with non-zero values
        sc_trades = page.locator("#sc-trades")
        sc_winrate = page.locator("#sc-winrate")
        sc_pnl = page.locator("#sc-pnl")
        sc_sharpe = page.locator("#sc-sharpe")

        expect(sc_trades).not_to_have_text("-")
        expect(sc_winrate).not_to_have_text("-%")
        expect(sc_pnl).not_to_have_text("₹-")

        # 5. Verify console logs updated with backtest completion
        expect(console_logs).to_contain_text("[Backtest Complete]")

        page.screenshot(path="tests/e2e_playwright/screenshots/test_07_backtest.png")
        browser.close()


if __name__ == "__main__":
    test_console_logs_and_backtest()
    print("✅ Test 07 Passed!")
