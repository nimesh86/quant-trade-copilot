import json
import time
import pytest
from playwright.async_api import async_playwright

BASE_URL = "http://127.0.0.1:8080"


@pytest.mark.asyncio
async def test_replay_manual_entry_reversion_and_clean_exit(live_server):
    """
    Playwright test for Replay Mode manual trade workflow:
    1. Intercepts /api/contracts/candles to supply deterministic 1-minute historical candles.
    2. Enters replay mode and selects 'Manual' practice mode.
    3. Takes a manual BUY position on Station CE.
    4. Verifies that the toolbar shows 'EXIT' button with live PnL, and toolbar has 'has-pos'.
    5. Time-travels backwards before the entry timestamp:
       - Verifies that the position is automatically unwound/removed.
       - Verifies toolbar removes 'has-pos' and restores BUY button.
       - Verifies 'EXIT' button is hidden.
    6. Slides scrubber forward past entry timestamp:
       - Verifies position is restored.
    7. Clicks EXIT button:
       - Verifies clean local exit in replay mode with NO "EXIT FAILED" error banner.
       - Verifies position closes, toolbar resets to BUY, and trade is recorded in scorecard.
    8. Slides backward past exit timestamp:
       - Verifies trade is restored to active open position.
    9. Slides backward before entry timestamp:
       - Verifies trade is completely unwound/cleared.
    """
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(viewport={'width': 1366, 'height': 850})
        page = await context.new_page()

        # Intercept candles route to supply 50 authentic 1-minute bars
        async def handle_candles(route):
            sym = "NIFTY26N0322700CE"
            if "PE" in route.request.url:
                sym = "NIFTY26N0322700PE"
            base_time = 1761623100  # 09:15 AM
            candles = []
            price = 150.0
            for i in range(50):
                candles.append({
                    "time": base_time + (i * 60),
                    "open": round(price, 2),
                    "high": round(price + 2.5, 2),
                    "low": round(price - 1.5, 2),
                    "close": round(price + 0.8, 2),
                    "volume": 2500 + i * 50
                })
                price += 0.8
            await route.fulfill(
                status=200,
                content_type="application/json",
                body=json.dumps({
                    "symbol": sym,
                    "date": "2026-10-28",
                    "is_last_day_data": False,
                    "market_status": "OPEN",
                    "num_candles": len(candles),
                    "candles": candles
                })
            )

        await page.route("**/api/contracts/candles*", handle_candles)

        response = await page.goto(f"{BASE_URL}/contract", wait_until="domcontentloaded")
        assert response.status == 200, f"Page load failed with status {response.status}"
        await page.wait_for_timeout(1500)

        # Wait for candles to load into Station CE
        await page.wait_for_function("""
            () => window.Stations && window.Stations.CE && window.Stations.CE.rawCandles && window.Stations.CE.rawCandles.length >= 5
        """, timeout=10000)

        # 1. Start Replay mode
        replay_toggle = page.locator('#btn-master-replay, #btn-replay-toggle').first
        assert await replay_toggle.is_visible(), "Replay toggle button must be visible"
        await replay_toggle.click()
        await page.wait_for_selector('#replay-bar:not(.hidden)', timeout=5000)

        # 2. Switch to Manual Practice mode on the replay dock
        btn_manual = page.locator('#rp-mode-manual')
        await btn_manual.click()
        await page.wait_for_function("() => window.Replay && window.Replay.mode === 'manual'", timeout=3000)

        # Advance scrubber to index 15
        await page.evaluate("""
            () => {
                const scrubber = document.getElementById('rp-scrubber');
                if (scrubber) {
                    scrubber.value = 15;
                    scrubber.dispatchEvent(new Event('input'));
                }
            }
        """)
        await page.wait_for_timeout(500)

        initial_candle_time = await page.evaluate("""
            () => {
                const ce = window.Stations.CE;
                return Number(ce.rawCandles[ce.rawCandles.length - 1].time);
            }
        """)

        # 3. Enter manual BUY position on CE
        btn_buy = page.locator('#ce-btn-buy')
        await btn_buy.click()
        await page.wait_for_timeout(500)

        # Check position state
        pos_info = await page.evaluate("""
            () => {
                const ce = window.Stations.CE;
                const pos = ce.activePosition;
                const toolbarHasPos = document.getElementById('ce-toolbar')?.classList.contains('has-pos');
                const exitBtnVisible = !document.getElementById('ce-btn-exit')?.classList.contains('hidden');
                return {
                    hasPos: !!pos,
                    entryTimestamp: pos ? pos.entryTimestamp : null,
                    toolbarHasPos: toolbarHasPos,
                    exitBtnVisible: exitBtnVisible
                };
            }
        """)
        assert pos_info["hasPos"], "Station CE should have activePosition after clicking BUY"
        assert pos_info["toolbarHasPos"], "Toolbar should have 'has-pos' class"
        assert pos_info["exitBtnVisible"], "EXIT button should be visible"
        assert pos_info["entryTimestamp"] == initial_candle_time, "Position entryTimestamp should match candle time"

        # 4. Advance scrubber forward to index 20
        await page.evaluate("""
            () => {
                const scrubber = document.getElementById('rp-scrubber');
                if (scrubber) {
                    scrubber.value = 20;
                    scrubber.dispatchEvent(new Event('input'));
                }
            }
        """)
        await page.wait_for_timeout(500)

        pos_at_20 = await page.evaluate("() => !!window.Stations.CE.activePosition")
        assert pos_at_20, "Position should still be active at future bar"

        # 5. REVERSION TEST: Slide scrubber back before entry position (index 10)
        await page.evaluate("""
            () => {
                const scrubber = document.getElementById('rp-scrubber');
                if (scrubber) {
                    scrubber.value = 10;
                    scrubber.dispatchEvent(new Event('input'));
                }
            }
        """)
        await page.wait_for_timeout(500)

        # Verify trade is completely reverted at index 10
        reverted_info = await page.evaluate("""
            () => {
                const ce = window.Stations.CE;
                const toolbarHasPos = document.getElementById('ce-toolbar')?.classList.contains('has-pos');
                const exitBtnVisible = !document.getElementById('ce-btn-exit')?.classList.contains('hidden');
                const buyBtnVisible = !document.getElementById('ce-btn-buy')?.classList.contains('hidden');
                return {
                    hasPos: !!ce.activePosition,
                    toolbarHasPos: toolbarHasPos,
                    exitBtnVisible: exitBtnVisible,
                    buyBtnVisible: buyBtnVisible,
                    completedTradesCount: (ce.manualTrades || []).length
                };
            }
        """)
        assert not reverted_info["hasPos"], "Active position MUST be null when scrubbed before entry time"
        assert not reverted_info["toolbarHasPos"], "Toolbar MUST NOT have 'has-pos' class when scrubbed before entry time"
        assert not reverted_info["exitBtnVisible"], "EXIT button MUST be hidden when scrubbed before entry time"
        assert reverted_info["buyBtnVisible"], "BUY button MUST be visible when scrubbed before entry time"
        assert reverted_info["completedTradesCount"] == 0, "No completed trades should exist before entry time"

        # 6. RESTORE TEST: Slide scrubber back forward to index 20
        await page.evaluate("""
            () => {
                const scrubber = document.getElementById('rp-scrubber');
                if (scrubber) {
                    scrubber.value = 20;
                    scrubber.dispatchEvent(new Event('input'));
                }
            }
        """)
        await page.wait_for_timeout(500)

        restored_info = await page.evaluate("""
            () => {
                const ce = window.Stations.CE;
                const toolbarHasPos = document.getElementById('ce-toolbar')?.classList.contains('has-pos');
                const exitBtnVisible = !document.getElementById('ce-btn-exit')?.classList.contains('hidden');
                return {
                    hasPos: !!ce.activePosition,
                    toolbarHasPos: toolbarHasPos,
                    exitBtnVisible: exitBtnVisible
                };
            }
        """)
        assert restored_info["hasPos"], "Active position MUST be restored when scrubbed forward past entry time"
        assert restored_info["toolbarHasPos"], "Toolbar MUST show 'has-pos' when scrubbed forward"
        assert restored_info["exitBtnVisible"], "EXIT button MUST be visible again"

        # 7. EXIT TEST: Click EXIT button at index 20
        btn_exit = page.locator('#ce-btn-exit')
        await btn_exit.click()
        await page.wait_for_timeout(500)

        # Verify NO 'EXIT FAILED - No active position to close.' alert exists
        alert_text = await page.evaluate("""
            () => {
                const alertEl = document.getElementById('execution-alert-box') || document.querySelector('.execution-alert');
                return alertEl ? alertEl.innerText : '';
            }
        """)
        assert "EXIT FAILED" not in alert_text, f"Unexpected error banner found: {alert_text}"
        assert "No active position to close" not in alert_text, f"Unexpected server rejection: {alert_text}"

        # Verify exit clean state
        after_exit_info = await page.evaluate("""
            () => {
                const ce = window.Stations.CE;
                const toolbarHasPos = document.getElementById('ce-toolbar')?.classList.contains('has-pos');
                const exitBtnVisible = !document.getElementById('ce-btn-exit')?.classList.contains('hidden');
                return {
                    hasPos: !!ce.activePosition,
                    toolbarHasPos: toolbarHasPos,
                    exitBtnVisible: exitBtnVisible,
                    archiveCount: (ce.allManualTradesArchive || []).length,
                    activeTradesCount: (ce.manualTrades || []).length
                };
            }
        """)
        assert not after_exit_info["hasPos"], "Position must be cleared after EXIT"
        assert not after_exit_info["toolbarHasPos"], "Toolbar must not have 'has-pos' after EXIT"
        assert not after_exit_info["exitBtnVisible"], "EXIT button must be hidden after EXIT"
        assert after_exit_info["activeTradesCount"] == 1, "Completed trade must be recorded"
        assert after_exit_info["archiveCount"] == 1, "Trade must be stored in archive"

        # 8. TIME-TRAVEL REWIND BEFORE EXIT (e.g. index 18)
        # Entry was at index 15, Exit was at index 20. At index 18, position should be RESTORED as active!
        await page.evaluate("""
            () => {
                const scrubber = document.getElementById('rp-scrubber');
                if (scrubber) {
                    scrubber.value = 18;
                    scrubber.dispatchEvent(new Event('input'));
                }
            }
        """)
        await page.wait_for_timeout(500)

        at_18_info = await page.evaluate("""
            () => {
                const ce = window.Stations.CE;
                return {
                    hasPos: !!ce.activePosition,
                    activeTradesCount: (ce.manualTrades || []).length,
                    toolbarHasPos: document.getElementById('ce-toolbar')?.classList.contains('has-pos'),
                    exitBtnVisible: !document.getElementById('ce-btn-exit')?.classList.contains('hidden')
                };
            }
        """)
        assert at_18_info["hasPos"], "Position must be restored as active when rewound before exit time"
        assert at_18_info["toolbarHasPos"], "Toolbar must show 'has-pos' when rewound before exit time"
        assert at_18_info["exitBtnVisible"], "EXIT button must be visible when rewound before exit time"
        assert at_18_info["activeTradesCount"] == 0, "Trade should not be marked closed yet at index 18"

        # 9. TIME-TRAVEL REWIND BEFORE ENTRY (e.g. index 10)
        await page.evaluate("""
            () => {
                const scrubber = document.getElementById('rp-scrubber');
                if (scrubber) {
                    scrubber.value = 10;
                    scrubber.dispatchEvent(new Event('input'));
                }
            }
        """)
        await page.wait_for_timeout(500)

        at_10_info = await page.evaluate("""
            () => {
                const ce = window.Stations.CE;
                return {
                    hasPos: !!ce.activePosition,
                    activeTradesCount: (ce.manualTrades || []).length,
                    toolbarHasPos: document.getElementById('ce-toolbar')?.classList.contains('has-pos')
                };
            }
        """)
        assert not at_10_info["hasPos"], "Position must be completely unwound before entry time"
        assert not at_10_info["toolbarHasPos"], "Toolbar must be clean before entry time"
        assert at_10_info["activeTradesCount"] == 0, "No trades should be active before entry time"

        await browser.close()
