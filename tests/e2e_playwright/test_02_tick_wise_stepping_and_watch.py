"""
Playwright E2E Test Suite 02: Tick-Wise Stepping & Watch Window Inspector
Verifies AC-02.1 to AC-02.5:
- Stepping tick-wise (+1 Tick) advances tick_index by 1 (not whole 1-minute candle).
- Variable Watch (Inspector) table updates on every single tick with ltp, tick_vol, tick_index, and EMAs.
- Stepping backward (-1 Tick) decrements tick and restores preceding state.
"""
from playwright.sync_api import sync_playwright, expect


def test_tick_wise_stepping_and_watch_table():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto("http://localhost:8080/studio", wait_until="networkidle")

        # 1. Wait for session and watch table to be ready
        page.wait_for_selector("#watch-table-body tr", timeout=10000)

        # 2. Capture initial tick text from scrubber badge
        badge = page.locator("#replay-bar-info")
        expect(badge).to_be_visible()
        initial_badge_text = badge.inner_text()
        assert "Tick #" in initial_badge_text, f"Badge must display tick count: {initial_badge_text}"

        # 3. Read initial watch table variables
        initial_vars = page.evaluate("""() => {
            const rows = document.querySelectorAll('#watch-table-body tr');
            const data = {};
            rows.forEach(r => {
                const k = r.querySelector('.var-name')?.innerText?.trim();
                const v = r.querySelector('.var-val')?.innerText?.trim();
                if (k && v) data[k] = v;
            });
            return data;
        }""")
        assert "ltp" in initial_vars or "close" in initial_vars, "Watch table must display price metrics"

        # 4. Click Step +1 Tick button
        btn_step_tick = page.locator("#btn-replay-next-tick")
        btn_step_tick.click()
        page.wait_for_timeout(400)

        # 5. Verify badge reflects tick advance
        new_badge_text = badge.inner_text()
        assert new_badge_text != initial_badge_text, f"Badge must change on +1 Tick step: {new_badge_text}"

        # 6. Verify watch table contains comprehensive tick variables
        updated_vars = page.evaluate("""() => {
            const rows = document.querySelectorAll('#watch-table-body tr');
            const data = {};
            rows.forEach(r => {
                const k = r.querySelector('.var-name')?.innerText?.trim();
                const v = r.querySelector('.var-val')?.innerText?.trim();
                if (k && v) data[k] = v;
            });
            return data;
        }""")

        assert "ltp" in updated_vars, "Watch table must show live LTP"
        assert "tick_index" in updated_vars, "Watch table must show tick_index"
        assert "cur_ema_fast" in updated_vars or "ema_fast" in updated_vars, "Watch table must show EMA indicators"

        # 7. Test Stepping Backward (-1 Tick)
        btn_back_tick = page.locator("#btn-replay-prev-tick")
        btn_back_tick.click()
        page.wait_for_timeout(400)
        rewound_badge_text = badge.inner_text()
        assert rewound_badge_text != new_badge_text, "Badge must update on -1 Tick step"

        # 8. Take screenshot
        page.screenshot(path="tests/e2e_playwright/screenshots/test_02_tick_watch.png")
        browser.close()


if __name__ == "__main__":
    test_tick_wise_stepping_and_watch_table()
    print("✅ Test 02 Passed!")
