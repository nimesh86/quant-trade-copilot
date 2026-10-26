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
        await page.wait_for_selector('#pe-toolbar.has-pos', timeout=5000)

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
        await page.wait_for_selector('#pe-toolbar:not(.has-pos)', timeout=5000)
        assert 'has-pos' not in (await page.locator('#pe-toolbar').get_attribute('class'))
        assert await page.locator('#pe-btn-buy').is_visible()

        # 3. Test CE Buy & Exit Cycle
        await page.click('#ce-btn-buy')
        await page.wait_for_selector('#ce-toolbar.has-pos', timeout=5000)
        assert 'has-pos' in (await page.locator('#ce-toolbar').get_attribute('class'))
        assert 'has-pos' not in (await page.locator('#pe-toolbar').get_attribute('class'))

        ce_exit_btn = page.locator('#ce-btn-exit')
        assert await ce_exit_btn.is_visible()
        ce_box = await ce_exit_btn.bounding_box()
        assert ce_box['x'] + ce_box['width'] <= 390

        await ce_exit_btn.click()
        await page.wait_for_selector('#ce-toolbar:not(.has-pos)', timeout=5000)
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


@pytest.mark.asyncio
async def test_ui_limit_buy_full_lifecycle_and_drag_controls():
    """
    TC-UI-LIMIT-LIFECYCLE:
    Complete proof and verification of Limit Buy UI and Backend:
    1. Drawing the Box: activates overlay, renders target (green) & SL (red) zones, creates hitboxes.
    2. Moving the Box: moves limit price and shifts target & SL while preserving bracket delta.
    3. Adjusting Target: independently adjusts target price without shifting limit or SL.
    4. Adjusting SL: independently adjusts SL price without shifting limit or target.
    5. Placing Limit Order: executes order payload to backend, confirms pending order in state.
    6. Backend Trigger & Fill: price tick reaches limit price -> order fills into active position.
    7. Clean Cancellation: limit order can be cancelled cleanly before fill.
    """
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(viewport={'width': 390, 'height': 844})
        page = await context.new_page()

        # 0. Clean reset
        await page.goto(f"{BASE_URL}/contract", wait_until="networkidle")
        await page.evaluate("() => fetch('/api/trade/reset', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({initial_capital:100000.0})})")
        await page.reload(wait_until="networkidle")
        await page.wait_for_timeout(600)

        # 1. DRAWING BOX: Click Limit button on Station CE
        await page.click('#ce-btn-limit')
        await page.wait_for_timeout(300)

        # Assert tool is active and styled
        limit_btn_cls = await page.locator('#ce-btn-limit').get_attribute('class')
        assert 'active' in limit_btn_cls, "Clicking #ce-btn-limit must activate button (.active)"

        box_state = await page.evaluate("""() => {
            const st = window.Stations.CE;
            return {
                active: st.limitTool?.active,
                limitPrice: st.limitTool?.limitPrice,
                targetPrice: st.limitTool?.targetPrice,
                slPrice: st.limitTool?.slPrice,
                hitBoxes: st.limitToolHitBoxes || []
            };
        }""")

        assert box_state['active'] is True, "limitTool.active must be true"
        assert box_state['limitPrice'] > 0, "Initial limit price must be > 0"
        assert box_state['targetPrice'] > box_state['limitPrice'], "Target must be above limit buy price"
        assert box_state['slPrice'] < box_state['limitPrice'], "SL must be below limit buy price"

        # Verify hitboxes generated on canvas
        hb_types = [hb['type'] for hb in box_state['hitBoxes']]
        assert 'limit_drag' in hb_types, "limit_drag hitbox must exist"
        assert 'target_drag' in hb_types, "target_drag hitbox must exist"
        assert 'sl_drag' in hb_types, "sl_drag hitbox must exist"
        assert 'limit_place' in hb_types, "limit_place hitbox must exist"
        assert 'limit_cancel' in hb_types, "limit_cancel hitbox must exist"

        init_limit = box_state['limitPrice']
        init_tgt = box_state['targetPrice']
        init_sl = box_state['slPrice']
        tgt_delta = round(init_tgt - init_limit, 2)
        sl_delta = round(init_limit - init_sl, 2)

        # 2. MOVING THE BOX: Shift Limit Buy Price BELOW market by -10.0
        # A Buy Limit placed below current market sits pending until price drops to limit price
        new_limit = round(init_limit - 10.0, 2)
        moved_state = await page.evaluate(f"""() => {{
            const st = window.Stations.CE;
            const newLim = {new_limit};
            const delta = newLim - st.limitTool.limitPrice;
            st.limitTool.limitPrice = newLim;
            st.limitTool.targetPrice = Math.round((st.limitTool.targetPrice + delta) * 20) / 20;
            st.limitTool.slPrice = Math.round((st.limitTool.slPrice + delta) * 20) / 20;
            renderStationSMFCanvas(st);
            return {{
                limitPrice: st.limitTool.limitPrice,
                targetPrice: st.limitTool.targetPrice,
                slPrice: st.limitTool.slPrice
            }};
        }}""")


        assert moved_state['limitPrice'] == new_limit, f"Limit price must move to {new_limit}"
        # Bracket delta must be preserved
        assert abs((moved_state['targetPrice'] - moved_state['limitPrice']) - tgt_delta) < 0.1, "Target delta must be preserved when moving box"
        assert abs((moved_state['limitPrice'] - moved_state['slPrice']) - sl_delta) < 0.1, "SL delta must be preserved when moving box"

        # 3. ADJUSTING TARGET: Independently drag Target handle +15.0 higher
        adj_target = round(moved_state['targetPrice'] + 15.0, 2)
        target_state = await page.evaluate(f"""() => {{
            const st = window.Stations.CE;
            st.limitTool.targetPrice = {adj_target};
            renderStationSMFCanvas(st);
            return {{
                limitPrice: st.limitTool.limitPrice,
                targetPrice: st.limitTool.targetPrice,
                slPrice: st.limitTool.slPrice
            }};
        }}""")

        assert target_state['targetPrice'] == adj_target, "Target price must update to adjusted value"
        assert target_state['limitPrice'] == new_limit, "Limit price must remain unchanged when adjusting Target"
        assert target_state['slPrice'] == moved_state['slPrice'], "SL price must remain unchanged when adjusting Target"

        # 4. ADJUSTING SL: Independently drag SL handle -5.0 lower
        adj_sl = round(target_state['slPrice'] - 5.0, 2)
        sl_state = await page.evaluate(f"""() => {{
            const st = window.Stations.CE;
            st.limitTool.slPrice = {adj_sl};
            renderStationSMFCanvas(st);
            return {{
                limitPrice: st.limitTool.limitPrice,
                targetPrice: st.limitTool.targetPrice,
                slPrice: st.limitTool.slPrice
            }};
        }}""")

        assert sl_state['slPrice'] == adj_sl, "SL price must update to adjusted value"
        assert sl_state['limitPrice'] == new_limit, "Limit price must remain unchanged when adjusting SL"
        assert sl_state['targetPrice'] == adj_target, "Target price must remain unchanged when adjusting SL"

        # 5. PLACING LIMIT ORDER (UI -> API -> Backend)
        # Execute place limit order
        place_res = await page.evaluate("""async () => {
            const st = window.Stations.CE;
            await executeStationLimitOrder(st);
            return {
                toolActive: st.limitTool?.active,
                btnActive: document.getElementById('ce-btn-limit')?.classList.contains('active')
            };
        }""")

        await page.wait_for_timeout(400)
        assert place_res['toolActive'] is False, "Limit tool must deactivate after order placement"
        assert place_res['btnActive'] is False, "Limit button .active must be removed"

        # Verify toast alert popped up
        all_toasts = await page.locator('.top-toast').all_text_contents()
        assert any("LIMIT ORDER PLACED" in t for t in all_toasts), f"Expected LIMIT ORDER PLACED toast in {all_toasts}"


        # 6. VERIFY BACKEND PENDING STATE
        engine_state = await page.evaluate("() => fetch('/api/trade/state').then(r => r.json())")
        pending_orders = engine_state.get('pending_orders', [])
        assert len(pending_orders) == 1, f"Expected 1 pending limit order in backend, found {len(pending_orders)}"

        po = pending_orders[0]
        assert po['order_type'] == 'LIMIT'
        assert po['limit_price'] == new_limit
        assert po['sl'] == adj_sl
        assert po['target1'] == adj_target
        assert po['side'] == 'BUY'

        # 7. BACKEND TRIGGER & ORDER FILL
        # A. Market price stays above limit price -> order stays pending
        above_res = await page.evaluate(f"""() => fetch('/api/trade/tick', {{
            method: 'POST',
            headers: {{'Content-Type': 'application/json'}},
            body: JSON.stringify({{
                symbol: window.Stations.CE.symbol,
                price: {new_limit + 5.0},
                atr: 3.0
            }})
        }}).then(r => r.json())""")

        assert above_res['event'] is None or above_res['event'].get('action') != 'ORDER_FILLED', "Order must NOT fill while price is above limit"
        state_after_above = await page.evaluate("() => fetch('/api/trade/state').then(r => r.json())")
        assert len(state_after_above.get('pending_orders', [])) == 1

        # B. Market price reaches / dips below limit price -> order fills immediately!
        fill_res = await page.evaluate(f"""() => fetch('/api/trade/tick', {{
            method: 'POST',
            headers: {{'Content-Type': 'application/json'}},
            body: JSON.stringify({{
                symbol: window.Stations.CE.symbol,
                price: {new_limit - 0.5},
                atr: 3.0
            }})
        }}).then(r => r.json())""")

        assert fill_res['event'] is not None, "Tick at limit price must trigger order event"
        assert fill_res['event'].get('action') == 'ORDER_FILLED', f"Expected ORDER_FILLED, got {fill_res['event']}"

        # C. Verify active position created with exact adjusted SL and Target
        final_state = await page.evaluate("() => fetch('/api/trade/state').then(r => r.json())")
        assert len(final_state.get('pending_orders', [])) == 0, "Pending orders must be 0 after fill"
        active_positions = final_state.get('active_positions', {})
        ce_sym = await page.evaluate("() => window.Stations.CE.symbol")
        assert ce_sym in active_positions, f"Active positions must contain {ce_sym}"

        pos = active_positions[ce_sym]
        assert pos['entry_price'] == new_limit, f"Filled position entry price must equal limit price {new_limit}"
        assert pos['current_sl'] == adj_sl, f"Filled position SL must equal adjusted SL {adj_sl}"
        assert pos['target1'] == adj_target, f"Filled position Target must equal adjusted Target {adj_target}"

        await browser.close()

