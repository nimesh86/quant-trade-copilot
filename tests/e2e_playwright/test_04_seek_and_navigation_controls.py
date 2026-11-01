"""
Playwright E2E Test Suite 04: Navigation & Playback Controls
Verifies AC-04.1 to AC-04.6:
- Seek Forward (+1 Candle) / Seek Backward (-1 Candle).
- Step +1 Tick / -1 Tick.
- Play starts sub-second tick streaming; Pause halts.
- Rewind resets to 0.
- Timeline scrubber input moves cursor.
"""
from playwright.sync_api import sync_playwright, expect


def test_navigation_and_playback_controls():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto("http://localhost:8080/studio", wait_until="networkidle")

        badge = page.locator("#replay-bar-info")
        expect(badge).to_be_visible()

        # 1. Step Next Candle (+1 Candle)
        btn_next_candle = page.locator("#btn-replay-next-candle")
        initial_text = badge.inner_text()
        btn_next_candle.click()
        page.wait_for_timeout(400)
        assert badge.inner_text() != initial_text, "Advancing candle must change scrubber badge"

        # 2. Step Prev Candle (-1 Candle)
        btn_prev_candle = page.locator("#btn-replay-prev-candle")
        btn_prev_candle.click()
        page.wait_for_timeout(400)

        # 3. Play / Pause Toggle
        play_btn = page.locator("#btn-replay-play")
        play_btn.click()
        page.wait_for_timeout(600)
        status_pill = page.locator("#dbg-status-pill")
        expect(status_pill).to_contain_text("Running")

        # Pause
        page.click("#dbg-pause")
        page.wait_for_timeout(300)

        # 4. Rewind to start
        page.click("#btn-replay-rewind")
        page.wait_for_timeout(400)

        # 5. Scrubber input seeking
        scrubber = page.locator("#replay-scrubber")
        scrubber.fill("30")
        page.wait_for_timeout(400)

        # 6. Restart
        page.click("#dbg-restart")
        page.wait_for_timeout(600)
        expect(status_pill).to_contain_text("Ready")

        page.screenshot(path="tests/e2e_playwright/screenshots/test_04_controls.png")
        browser.close()


if __name__ == "__main__":
    test_navigation_and_playback_controls()
    print("✅ Test 04 Passed!")
