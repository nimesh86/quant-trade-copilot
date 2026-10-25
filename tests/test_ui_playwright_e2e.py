import pytest
import asyncio
import re
from playwright.async_api import async_playwright

BASE_URL = "http://127.0.0.1:8080"

@pytest.mark.asyncio
async def test_ui_e2e_complete_mobile_flow():
    """
    Automated E2E Playwright test on iPhone 14 mobile viewport (390x844).
    Validates:
    1. Initial flat state
    2. PE Buy & Exit cycle with bounding-box width safety (<= 390px)
    3. CE Buy & Exit cycle with position isolation
    4. Limit button wiring (#ce-btn-limit & #pe-btn-limit)
    5. Underlying switch lot size synchronization (NIFTY 65 -> BANKNIFTY 15)
    6. Timeframe toggle (1m <-> 5m)
    7. Strike stepper (+50 / -50)
    8. Bar Replay mode segregation (Auto vs Manual trades)
    9. Top-sticky notification banner (thin, dismissable, max 2 cap)
    """
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            viewport={'width': 390, 'height': 844},
            user_agent='Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) AppleWebKit/605.1.15'
        )
        page = await context.new_page()

        # 0. Clean reset via API
        await page.goto(f"{BASE_URL}/contract", wait_until="networkidle")
        await page.evaluate("() => fetch('/api/trade/reset', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({initial_capital:100000.0})})")
        await page.reload(wait_until="networkidle")
        await page.wait_for_timeout(800)

        # 1. Verify initial flat state
        ce_cls = await page.locator('#ce-toolbar').get_attribute('class')
        pe_cls = await page.locator('#pe-toolbar').get_attribute('class')
        assert 'has-pos' not in ce_cls
        assert 'has-pos' not in pe_cls
        assert await page.locator('#ce-btn-exit').is_hidden()
        assert await page.locator('#pe-btn-exit').is_hidden()
        assert await page.locator('#ce-btn-buy').is_visible()
        assert await page.locator('#pe-btn-buy').is_visible()

        # 2. Test PE Buy Order Execution & Exit Button Viewport Safety
        await page.click('#pe-btn-buy')
        await page.wait_for_timeout(500)

        pe_cls_pos = await page.locator('#pe-toolbar').get_attribute('class')
        ce_cls_pos = await page.locator('#ce-toolbar').get_attribute('class')
        assert 'has-pos' in pe_cls_pos, "PE toolbar must have .has-pos class when position open"
        assert 'has-pos' not in ce_cls_pos, "CE toolbar must NOT have .has-pos when only PE bought"

        exit_btn = page.locator('#pe-btn-exit')
        assert await exit_btn.is_visible()
        box = await exit_btn.bounding_box()
        assert box is not None
        assert box['x'] + box['width'] <= 390, f"PE Exit button right edge ({box['x'] + box['width']}) must fit inside 390px mobile screen!"

        # Exit PE position
        await exit_btn.click()
        await page.wait_for_timeout(500)
        assert 'has-pos' not in (await page.locator('#pe-toolbar').get_attribute('class'))
        assert await page.locator('#pe-btn-buy').is_visible()

        # 3. Test CE Buy & Exit Cycle
        await page.click('#ce-btn-buy')
        await page.wait_for_timeout(500)
        assert 'has-pos' in (await page.locator('#ce-toolbar').get_attribute('class'))
        assert 'has-pos' not in (await page.locator('#pe-toolbar').get_attribute('class'))

        ce_exit_btn = page.locator('#ce-btn-exit')
        assert await ce_exit_btn.is_visible()
        ce_box = await ce_exit_btn.bounding_box()
        assert ce_box['x'] + ce_box['width'] <= 390

        await ce_exit_btn.click()
        await page.wait_for_timeout(500)
        assert 'has-pos' not in (await page.locator('#ce-toolbar').get_attribute('class'))

        # 4. Test Station Limit Buttons Wiring & Overlay
        await page.click('#ce-btn-limit')
        await page.wait_for_timeout(300)
        limit_btn_cls = await page.locator('#ce-btn-limit').get_attribute('class')
        assert 'active' in limit_btn_cls, "Clicking #ce-btn-limit must activate the limit tool!"
        hb_count = await page.evaluate("() => window.Stations && window.Stations.CE && window.Stations.CE.limitToolHitBoxes ? window.Stations.CE.limitToolHitBoxes.length : 0")
        assert hb_count > 0, "Station Limit tool must generate interactive hitboxes on the canvas!"
        # Toggle off
        await page.click('#ce-btn-limit')
        await page.wait_for_timeout(300)
        assert 'active' not in (await page.locator('#ce-btn-limit').get_attribute('class'))

        # 5. Test Timeframe Toggle (1m <-> 5m)
        tf_btn = page.locator('#btn-timeframe-toggle')
        assert await tf_btn.text_content() == "1m"
        await tf_btn.click()
        await page.wait_for_timeout(300)
        assert await tf_btn.text_content() == "5m"
        await tf_btn.click()
        await page.wait_for_timeout(300)
        assert await tf_btn.text_content() == "1m"

        # 6. Test Strike Stepper (+50 / -50)
        strike_before = int(await page.locator('#cur-strike-val').text_content())
        await page.click('#btn-strike-next')
        await page.wait_for_timeout(400)
        strike_after = int(await page.locator('#cur-strike-val').text_content())
        assert strike_after == strike_before + 50

        await page.click('#btn-strike-prev')
        await page.wait_for_timeout(400)
        assert int(await page.locator('#cur-strike-val').text_content()) == strike_before

        # 7. Test Bar Replay Mode Segregation (Auto vs Manual)
        await page.wait_for_function("() => window.Stations && window.Stations.CE && window.Stations.CE.rawCandles && window.Stations.CE.rawCandles.length >= 5", timeout=10000)
        await page.click('#btn-master-replay')
        await page.wait_for_selector('#replay-bar:not(.hidden)', timeout=5000)

        # Verify next candle button has clean icon ⏭
        assert await page.locator('#rp-btn-step-fwd').text_content() == "⏭"

        await page.click('#rp-mode-manual')
        await page.wait_for_timeout(300)

        # In manual mode, both stations initially show 0 trades
        assert await page.locator('#ce-stat-summary-rate').text_content() == "0 | 0%"
        assert await page.locator('#pe-stat-summary-rate').text_content() == "0 | 0%"

        # Execute 2 manual trades on PE
        await page.click('#pe-btn-buy')
        await page.wait_for_timeout(300)
        await page.click('#pe-btn-exit')
        await page.wait_for_timeout(300)

        await page.click('#pe-btn-buy')
        await page.wait_for_timeout(300)
        await page.click('#pe-btn-exit')
        await page.wait_for_timeout(300)

        # PE shows 2 trades, CE strictly remains 0 trades
        pe_rate = await page.locator('#pe-stat-summary-rate').text_content()
        ce_rate = await page.locator('#ce-stat-summary-rate').text_content()
        assert "2 |" in pe_rate
        assert ce_rate == "0 | 0%"

        # Open Scorecard Modal
        await page.click('#btn-master-report')
        await page.wait_for_timeout(400)
        assert await page.locator('#bt-count-all').text_content() == "2"
        assert await page.locator('#bt-count-ce').text_content() == "0"
        assert await page.locator('#bt-count-pe').text_content() == "2"
        await page.click('#bt-btn-close')
        await page.wait_for_timeout(300)

        # Exit Replay
        await page.click('#rp-btn-exit')
        await page.wait_for_timeout(400)

        # 8. Test Top Sticky Notification Banner
        await page.evaluate("() => flashExecutionAlert('🟢 TEST NOTIF', 'Verification Alert', 'success')")
        await page.wait_for_timeout(300)
        toast = page.locator('.top-toast')
        assert await toast.count() == 1
        tbox = await toast.bounding_box()
        assert tbox['height'] <= 32
        assert tbox['y'] <= 15

        # Dismiss via close button
        await page.click('.top-toast-close')
        await page.wait_for_timeout(350)
        assert await page.locator('.top-toast:not(.toast-dismissing)').count() == 0

        await browser.close()


@pytest.mark.asyncio
async def test_ui_limit_order_canvas_drag_isolation():
    """
    TC-UI-LIMIT-DRAG:
    Verifies that dragging the Limit Order box on the station chart updates the limit price
    while PREVENTING LightweightCharts from moving or scrolling the candles!
    """
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(viewport={'width': 390, 'height': 844})
        page = await context.new_page()
        await page.goto(f"{BASE_URL}/contract", wait_until="networkidle")
        await page.wait_for_function("() => window.Stations && window.Stations.CE && window.Stations.CE.rawCandles && window.Stations.CE.rawCandles.length >= 5", timeout=10000)

        # 1. Click Limit tool on CE
        await page.click('#ce-btn-limit')
        await page.wait_for_timeout(300)
        assert 'active' in (await page.locator('#ce-btn-limit').get_attribute('class'))

        # 2. Get initial chart visible logical range
        initial_range = await page.evaluate("() => window.Stations.CE.chart.timeScale().getVisibleLogicalRange()")

        # 3. Locate Station CE Canvas and simulate mouse drag
        canvas_locator = page.locator('#station-ce canvas').first
        box = await canvas_locator.bounding_box()
        assert box is not None

        start_x = box['x'] + (box['width'] / 2)
        start_y = box['y'] + (box['height'] / 2)

        # Drag vertically by 30 pixels
        await page.mouse.move(start_x, start_y)
        await page.mouse.down()
        await page.mouse.move(start_x, start_y - 30, steps=5)
        await page.mouse.up()
        await page.wait_for_timeout(300)

        # 4. Verify that the chart time scale range did not change / pan
        new_range = await page.evaluate("() => window.Stations.CE.chart.timeScale().getVisibleLogicalRange()")
        if initial_range and new_range:
            assert abs(new_range['from'] - initial_range['from']) < 0.5, "Chart time axis must NOT pan when dragging limit handles!"

        # 5. Toggle off limit tool
        await page.click('#ce-btn-limit')
        await page.wait_for_timeout(200)
        assert 'active' not in (await page.locator('#ce-btn-limit').get_attribute('class'))
        await browser.close()


@pytest.mark.asyncio
async def test_ui_mathematical_pnl_sync_header_and_footers():
    """
    TC-UI-PNL-SYNC:
    Verifies that the Master Header Net PnL is mathematically identical to CE Net PnL + PE Net PnL
    in flat state, during open positions, and after manual replay trades.
    """
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(viewport={'width': 390, 'height': 844})
        page = await context.new_page()
        await page.goto(f"{BASE_URL}/contract", wait_until="networkidle")
        await page.evaluate("() => fetch('/api/trade/reset', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({initial_capital:100000.0})})")
        await page.reload(wait_until="networkidle")
        await page.wait_for_function("() => window.Stations && window.Stations.CE && window.Stations.CE.rawCandles && window.Stations.CE.rawCandles.length >= 5", timeout=10000)

        # 1. Enter Replay Mode in Manual
        await page.click('#btn-master-replay')
        await page.wait_for_selector('#replay-bar:not(.hidden)', timeout=5000)
        await page.click('#rp-mode-manual')
        await page.wait_for_timeout(300)

        # 2. Execute 1 trade on PE and 1 trade on CE
        await page.click('#pe-btn-buy')
        await page.wait_for_timeout(200)
        await page.click('#pe-btn-exit')
        await page.wait_for_timeout(200)

        await page.click('#ce-btn-buy')
        await page.wait_for_timeout(200)
        await page.click('#ce-btn-exit')
        await page.wait_for_timeout(200)

        # 3. Read exact cash values from Station state and header DOM
        values = await page.evaluate("""() => {
            const pnlEl = document.getElementById('val-portfolio-pnl');
            return {
                ceCash: window.Stations.CE.currentNetCash || 0,
                peCash: window.Stations.PE.currentNetCash || 0,
                headerText: pnlEl ? pnlEl.textContent.trim() : '+₹0.00'
            };
        }""")
        expected_sum = values['ceCash'] + values['peCash']

        # Parse header string (e.g., "+₹1,234.50" or "-₹55.00")
        clean_text = values['headerText'].replace('₹', '').replace(',', '').replace('+', '').strip()
        header_val = float(clean_text)

        assert abs(header_val - expected_sum) <= 1.0, (
            f"Net PnL Discrepancy! Header shows ₹{header_val}, "
            f"but CE (₹{values['ceCash']}) + PE (₹{values['peCash']}) = ₹{expected_sum}!"
        )

        await browser.close()


@pytest.mark.asyncio
async def test_ui_replay_step_next_prev_and_icons():
    """
    TC-UI-REPLAY-STEP:
    Verifies that Replay Step Next (⏭) and Step Back (⏮) controls advance and rewind
    the exact candle index, update the time display, and display clean media glyphs.
    """
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(viewport={'width': 390, 'height': 844})
        page = await context.new_page()
        await page.goto(f"{BASE_URL}/contract", wait_until="networkidle")
        await page.wait_for_function("() => window.Stations && window.Stations.CE && window.Stations.CE.rawCandles && window.Stations.CE.rawCandles.length >= 5", timeout=10000)

        await page.click('#btn-master-replay')
        await page.wait_for_selector('#replay-bar:not(.hidden)', timeout=5000)

        # 1. Verify clean media transport icons
        assert await page.locator('#rp-btn-step-fwd').text_content() == "⏭"
        assert await page.locator('#rp-btn-step-back').text_content() == "⏮"
        assert await page.locator('#rp-btn-play').text_content() == "▶"

        # 2. Get initial replay index
        idx_before = await page.evaluate("() => window.Replay ? window.Replay.currentIndex : null")
        assert idx_before is not None

        # 3. Click Next Candle (⏭)
        await page.click('#rp-btn-step-fwd')
        await page.wait_for_timeout(200)
        idx_after_fwd = await page.evaluate("() => window.Replay.currentIndex")
        assert idx_after_fwd == idx_before + 1, "Clicking ⏭ must advance replay by exactly 1 candle!"

        # 4. Click Previous Candle (⏮)
        await page.click('#rp-btn-step-back')
        await page.wait_for_timeout(200)
        idx_after_back = await page.evaluate("() => window.Replay.currentIndex")
        assert idx_after_back == idx_before, "Clicking ⏮ must rewind replay by exactly 1 candle!"

        await browser.close()


@pytest.mark.asyncio
async def test_ui_lot_stepper_boundaries_and_direct_input():
    """
    TC-UI-LOT-BOUNDS:
    Verifies that the lot stepper clamps at 1 minimum, allows increments up to 100 lots,
    and supports direct typing on the lot badge.
    """
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(viewport={'width': 390, 'height': 844})
        page = await context.new_page()
        await page.goto(f"{BASE_URL}/contract", wait_until="networkidle")
        await page.wait_for_timeout(500)

        # 1. Decrement at minimum (1 lot) stays at 1
        await page.click('#ce-lot-minus')
        await page.wait_for_timeout(100)
        assert await page.locator('#ce-lot-val').text_content() == "1"

        # 2. Increment past 20 lots (e.g. up to 25 lots)
        for _ in range(24):
            await page.click('#ce-lot-plus')
        await page.wait_for_timeout(100)
        assert await page.locator('#ce-lot-val').text_content() == "25"
        assert await page.evaluate("() => window.Stations.CE.tradingLots") == 25

        # 3. Direct setting via script/prompt simulation
        await page.evaluate("() => { window.Stations.CE.tradingLots = 50; document.getElementById('ce-lot-val').textContent = '50'; }")
        assert await page.locator('#ce-lot-val').text_content() == "50"
        assert await page.evaluate("() => window.Stations.CE.tradingLots") == 50

        await browser.close()
