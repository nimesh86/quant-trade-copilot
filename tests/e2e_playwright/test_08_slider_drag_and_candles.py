"""
Playwright E2E Test Suite 08: Multi-Directional Timeline Scrubber & TV Chart Alignment
Verifies:
1. Slider dimensions stay locked (width does not change when dragging).
2. Badge text updates smoothly with real-time tick/bar/time and never collapses/twitches.
3. Chart maintains static zoom level (barSpacing) and scrolls horizontally to the latest candle.
4. Scrubbing forward (to afternoon/end) does NOT squash all 375 candles into a single screen.
5. Scrubbing to beginning (tick 0) cleans all ghost lines and renders the initial candle clearly.
"""
from playwright.sync_api import sync_playwright, expect


def test_timeline_slider_drag_and_candle_continuity():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1400, "height": 800})
        page.goto("http://localhost:8080/studio", wait_until="networkidle")

        # 1. Wait for session and chart to initialize
        page.wait_for_selector("#watch-table-body tr", timeout=10000)
        badge = page.locator("#replay-bar-info")
        expect(badge).to_be_visible()

        scrubber = page.locator("#replay-scrubber")
        expect(scrubber).to_be_visible()

        max_ticks = int(page.evaluate("() => document.getElementById('replay-scrubber').max"))
        box_init = scrubber.bounding_box()
        assert box_init is not None, "Scrubber must have a visible bounding box"
        assert abs(box_init["width"] - 240) < 2, f"Scrubber width must be fixed at 240px (was {box_init['width']})"

        # ── 1. DRAGGING STABILITY: Verify slider width doesn't change on input ──
        page.evaluate("() => { const s = document.getElementById('replay-scrubber'); s.value = 1050; s.dispatchEvent(new Event('input')); }")
        badge_text_input = badge.inner_text()
        box_drag = scrubber.bounding_box()
        assert "Scrubbing" not in badge_text_input, "Badge must not say 'Scrubbing...'"
        assert "1050" in badge_text_input, f"Badge must show target tick (got: {badge_text_input})"
        assert abs(box_drag["width"] - box_init["width"]) < 1, "Slider width must remain constant while dragging"

        # ── 2. SCROLLING TO 11:00 AM: Verify horizontal scrolling & static zoom ─
        page.evaluate("() => { const s = document.getElementById('replay-scrubber'); s.dispatchEvent(new Event('change')); }")
        page.wait_for_timeout(600)

        state_scrolled = page.evaluate("""() => {
            const c = window.chartMgr;
            const plots = c.plotDataMap || {};
            const lastCandle = c.candlesData[c.candlesData.length - 1];
            let overshoots = 0;
            for (const [k, pts] of Object.entries(plots)) {
                if (pts.length > 0 && pts[pts.length - 1].time > lastCandle.time) {
                    overshoots++;
                }
            }
            return {
                barSpacing: c.chart.timeScale().options().barSpacing,
                candleCount: c.candlesData.length,
                lastCandleTime: lastCandle.time,
                overshoots: overshoots
            };
        }""")

        assert state_scrolled["candleCount"] > 50, "Candles must expand up to current cursor"
        assert state_scrolled["barSpacing"] >= 7, "Bar spacing must remain static (not squashed into threads)"
        assert state_scrolled["overshoots"] == 0, "No indicator lines may overshoot the latest candle"

        # ── 3. SCRUB TO END (15:29:54): Verify chart does not squash all bars into 1 frame ─
        page.evaluate(f"() => {{ const s = document.getElementById('replay-scrubber'); s.value = {max_ticks}; s.dispatchEvent(new Event('change')); }}")
        page.wait_for_timeout(600)

        state_end = page.evaluate("""() => {
            const c = window.chartMgr;
            return {
                barSpacing: c.chart.timeScale().options().barSpacing,
                visibleRange: c.chart.timeScale().getVisibleRange(),
                candleCount: c.candlesData.length
            };
        }""")

        assert state_end["candleCount"] >= 370, "All candles must be loaded up to end"
        assert state_end["barSpacing"] >= 7, "Bar spacing must remain static at full day end"
        # Visible range must NOT span the entire 6 hours (09:15 to 15:30) in one screen
        visible_duration_mins = (state_end["visibleRange"]["to"] - state_end["visibleRange"]["from"]) / 60
        assert visible_duration_mins < 300, (
            f"Chart must horizontally scroll rather than fitting all 375 minutes in one screen (visible mins: {visible_duration_mins})"
        )

        # ── 4. SCRUB TO ZERO (09:15:00): Verify zero ghost lines & healthy candle ─
        page.evaluate("() => { const s = document.getElementById('replay-scrubber'); s.value = 0; s.dispatchEvent(new Event('change')); }")
        page.wait_for_timeout(600)

        state_zero = page.evaluate("""() => {
            const c = window.chartMgr;
            const plots = {};
            for (const [k, v] of Object.entries(c.plotDataMap)) {
                plots[k] = v.length;
            }
            return {
                candleCount: c.candlesData.length,
                plots: plots,
                firstCandle: c.candlesData[0]
            };
        }""")

        assert state_zero["candleCount"] == 1, "Only 1 candle at tick 0"
        assert len(state_zero["plots"]) == 0, f"No ghost lines from afternoon may linger at tick 0 (got: {state_zero['plots']})"
        assert state_zero["firstCandle"]["open"] > 20000.0, "Spot candle price must be valid"

        page.screenshot(path="tests/e2e_playwright/screenshots/test_08_slider_drag.png")
        browser.close()


if __name__ == "__main__":
    test_timeline_slider_drag_and_candle_continuity()
    print("✅ Test 08 Passed!")
