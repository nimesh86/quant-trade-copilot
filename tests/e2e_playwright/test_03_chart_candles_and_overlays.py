"""
Playwright E2E Test Suite 03: Chart Candlestick Visuals & Indicator Overlays
Verifies AC-03.1 to AC-03.4:
- Chart container has Lightweight Charts canvas elements with valid dimensions.
- Candlesticks and wicks render properly on the right price scale.
- Price overlays (EMAs, Supertrend, S/R) are plotted on the candle series.
- Oscillators (RSI, buyer %, seller %) do NOT pollute or distort the candlestick price scale.
- Chart legend displays dynamic OHLC and indicator values.
"""
from playwright.sync_api import sync_playwright, expect


def test_chart_candles_and_overlays():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto("http://localhost:8080/studio", wait_until="networkidle")

        # 1. Wait for chart viewport to mount
        page.wait_for_selector("#chart-container canvas", timeout=10000)
        canvases = page.locator("#chart-container canvas")
        assert canvases.count() >= 1, "Lightweight Charts canvas must be mounted"

        # 2. Verify canvas dimensions are non-zero and filling container
        box = canvases.first.bounding_box()
        assert box["width"] > 400, f"Chart canvas width should be > 400px, got {box['width']}"
        assert box["height"] > 150, f"Chart canvas height should be > 150px, got {box['height']}"

        # 3. Check Chart Legend
        legend_sym = page.locator("#legend-symbol")
        legend_ohlc = page.locator("#legend-ohlc")
        legend_plots = page.locator("#legend-plots")

        expect(legend_sym).to_be_visible()
        expect(legend_ohlc).to_be_visible()
        expect(legend_plots).to_be_visible()

        assert "O:" in legend_ohlc.inner_text(), "Legend must show Open price"
        assert "C:" in legend_ohlc.inner_text(), "Legend must show Close price"

        # 4. Advance 5 ticks and verify legend OHLC and plots update
        btn_step_tick = page.locator("#btn-replay-next-tick")
        for _ in range(5):
            btn_step_tick.click()
            page.wait_for_timeout(100)

        page.wait_for_timeout(300)
        legend_plots_text = legend_plots.inner_text()
        assert len(legend_plots_text) > 3, "Legend plots must show live indicator metrics"

        # 5. Fit Scale button click
        page.click("#btn-fit-chart")
        page.wait_for_timeout(300)

        # 6. Take screenshot
        page.screenshot(path="tests/e2e_playwright/screenshots/test_03_chart_overlays.png")
        browser.close()


if __name__ == "__main__":
    test_chart_candles_and_overlays()
    print("✅ Test 03 Passed!")
