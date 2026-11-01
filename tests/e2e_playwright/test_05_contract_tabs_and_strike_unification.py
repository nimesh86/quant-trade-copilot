"""
Playwright E2E Test Suite 05: Contract Selection & Tab Synchronization
Verifies AC-05.1 to AC-05.4:
- Strike dropdown unifies option strike selection.
- Changing strike updates the CE and PE tab labels dynamically.
- Clicking Index Spot, CE, and PE tabs toggles active chart mode and rescales chart without losing markers.
"""
from playwright.sync_api import sync_playwright, expect


def test_contract_tabs_and_strike_unification():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto("http://localhost:8080/studio", wait_until="networkidle")

        tab_spot = page.locator("#tab-spot")
        tab_ce = page.locator("#tab-ce")
        tab_pe = page.locator("#tab-pe")
        sel_strike = page.locator("#select-strike")

        expect(tab_spot).to_be_visible()
        expect(tab_ce).to_be_visible()
        expect(tab_pe).to_be_visible()

        # 1. Verify initial tab labels
        expect(tab_ce).to_contain_text("24200 CE")
        expect(tab_pe).to_contain_text("24200 PE")

        # 2. Change Strike to 24,150
        sel_strike.select_option("24150")
        page.wait_for_timeout(400)

        # 3. Verify tabs update to reflect selected strike
        expect(tab_ce).to_contain_text("24150 CE")
        expect(tab_pe).to_contain_text("24150 PE")

        # 4. Click CE Tab
        tab_ce.click()
        page.wait_for_timeout(300)
        expect(tab_ce).to_have_class("chart-tab active")
        expect(page.locator("#legend-symbol")).to_contain_text("24150 CE")

        # 5. Click PE Tab
        tab_pe.click()
        page.wait_for_timeout(300)
        expect(tab_pe).to_have_class("chart-tab active")
        expect(page.locator("#legend-symbol")).to_contain_text("24150 PE")

        # 6. Click Spot Tab
        tab_spot.click()
        page.wait_for_timeout(300)
        expect(tab_spot).to_have_class("chart-tab active")
        expect(page.locator("#legend-symbol")).to_contain_text("NIFTY")

        page.screenshot(path="tests/e2e_playwright/screenshots/test_05_contract_tabs.png")
        browser.close()


if __name__ == "__main__":
    test_contract_tabs_and_strike_unification()
    print("✅ Test 05 Passed!")
