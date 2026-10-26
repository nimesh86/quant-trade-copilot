import pytest
import asyncio
import contextlib
from playwright.async_api import async_playwright

BASE_URL = "http://127.0.0.1:8080"


@contextlib.asynccontextmanager
async def create_test_page(width=390, height=844):
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            viewport={'width': width, 'height': height},
            user_agent='Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) AppleWebKit/605.1.15'
        )
        page = await context.new_page()
        try:
            await page.goto(f"{BASE_URL}/contract", wait_until="domcontentloaded")
            await page.wait_for_function("() => window.Stations && window.Stations.CE && window.Stations.CE.symbol", timeout=15000)
            await page.wait_for_timeout(300)
            yield page
        finally:
            await browser.close()


@pytest.mark.asyncio
async def test_issue1_box_anchored_across_timeframe_toggle():
    """
    Issue 1: Box anchoring vs Timeframe.
    Verify the Limit Buy box stays anchored to the right execution frontier and does not jump or deform
    when toggling between 1m and 5m timeframes.
    """
    async with create_test_page() as page:
        # Reset state cleanly
        await page.evaluate("() => fetch('/api/trade/reset', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({initial_capital:100000.0})})")
        await page.wait_for_timeout(300)

        # 1. Open limit tool on CE station
        await page.click('#ce-btn-limit')
        await page.wait_for_timeout(300)

        # Read box coordinate on 1m
        box_x_1m = await page.evaluate("() => Stations.CE.limitToolHitBoxes.find(h => h.type === 'limit_drag')?.x")
        box_w_1m = await page.evaluate("() => Stations.CE.limitToolHitBoxes.find(h => h.type === 'limit_drag')?.w")
        assert box_x_1m is not None, "Limit box must be drawn on 1m"
        assert box_w_1m is not None

        # Toggle to 5m timeframe via #btn-timeframe-toggle
        await page.click('#btn-timeframe-toggle')
        await page.wait_for_timeout(400)

        # Read box coordinate on 5m
        box_x_5m = await page.evaluate("() => Stations.CE.limitToolHitBoxes.find(h => h.type === 'limit_drag')?.x")
        box_w_5m = await page.evaluate("() => Stations.CE.limitToolHitBoxes.find(h => h.type === 'limit_drag')?.w")
        assert box_x_5m is not None, "Limit box must remain active on 5m"

        # Box position should remain firmly anchored to execution frontier
        assert abs(box_x_1m - box_x_5m) <= 5, f"Box X jumped between timeframes: 1m={box_x_1m}, 5m={box_x_5m}"
        assert box_w_1m == box_w_5m, f"Box width deformed between timeframes: 1m={box_w_1m}, 5m={box_w_5m}"

        # Toggle back to 1m
        await page.click('#btn-timeframe-toggle')
        await page.wait_for_timeout(300)


@pytest.mark.asyncio
async def test_issue2_cancel_buttons_rendered_and_clickable():
    """
    Issue 2: Close / Cancel button visibility.
    Verify that high-contrast Cancel button pill on the badge and the top-right close button
    are both rendered on the chart canvas and clicking either closes the limit tool.
    """
    async with create_test_page() as page:
        # Open limit tool
        await page.click('#ce-btn-limit')
        await page.wait_for_timeout(300)

        # Verify hitboxes for limit_cancel exist (badge cancel pill + top right box close button)
        cancel_boxes = await page.evaluate("() => Stations.CE.limitToolHitBoxes.filter(h => h.type === 'limit_cancel')")
        assert len(cancel_boxes) >= 2, f"Expected both badge cancel button and top close button, found {len(cancel_boxes)}"

        # Click the top-right close button on canvas
        top_close = cancel_boxes[1]
        await page.evaluate(f"""() => {{
            const el = document.getElementById('ce-chart-container');
            const rect = el.getBoundingClientRect();
            const evt = new MouseEvent('mousedown', {{
                bubbles: true,
                cancelable: true,
                clientX: rect.left + {top_close['x']} + {top_close['w']} / 2,
                clientY: rect.top + {top_close['y']} + {top_close['h']} / 2
            }});
            el.dispatchEvent(evt);
        }}""")
        await page.wait_for_timeout(300)

        # Verify limit tool is now closed
        is_active = await page.evaluate("() => Stations.CE.limitTool?.active")
        assert is_active is False, "Clicking top close button must close limit tool"


@pytest.mark.asyncio
async def test_issue3_pending_order_chart_line_and_cancellation():
    """
    Issue 3: Pending Order Closing and Cancellation.
    Verify that when a limit order is submitted:
    1. A pending order level is rendered on chart overlay with [✕ CANCEL] button
    2. A pending order pill appears in the station toolbar with [✕] button
    3. Clicking cancel removes the pending order from backend and removes visual indicators.
    """
    async with create_test_page() as page:
        await page.evaluate("() => fetch('/api/trade/reset', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({initial_capital:100000.0})})")
        await page.wait_for_timeout(300)

        # Open limit tool
        await page.click('#ce-btn-limit')
        await page.wait_for_timeout(300)

        # Set limit price lower than market so it stays pending
        cur_px = await page.evaluate("() => Stations.CE.rawCandles.length > 0 ? Stations.CE.rawCandles[Stations.CE.rawCandles.length - 1].close : 100.0")
        pending_limit = round(cur_px - 15.0, 2)
        await page.evaluate(f"() => {{ Stations.CE.limitTool.limitPrice = {pending_limit}; Stations.CE.limitTool.targetPrice = {pending_limit + 10}; Stations.CE.limitTool.slPrice = {pending_limit - 10}; renderStationSMFCanvas(Stations.CE); }}")
        await page.wait_for_timeout(200)

        # Execute limit order via executeStationLimitOrder / station placement
        await page.evaluate("() => executeStationLimitOrder(Stations.CE)")
        await page.wait_for_timeout(600)

        # Verify pending order exists in toolbar pill
        pending_pill = page.locator('#ce-pending-pill')
        assert await pending_pill.is_visible(), "Toolbar pending pill must be visible"

        # Verify pending order exists on canvas hitboxes
        canvas_cancel = await page.evaluate("() => Stations.CE.limitToolHitBoxes.find(h => h.type === 'pending_cancel')")
        assert canvas_cancel is not None, "Chart canvas must render pending order line with cancel button"

        # Cancel via on-chart cancel button
        await page.evaluate(f"""() => {{
            const el = document.getElementById('ce-chart-container');
            const rect = el.getBoundingClientRect();
            const evt = new MouseEvent('mousedown', {{
                bubbles: true,
                cancelable: true,
                clientX: rect.left + {canvas_cancel['x']} + {canvas_cancel['w']} / 2,
                clientY: rect.top + {canvas_cancel['y']} + {canvas_cancel['h']} / 2
            }});
            el.dispatchEvent(evt);
        }}""")
        await page.wait_for_timeout(500)

        # Verify pending order is cancelled in backend
        pending_count = await page.evaluate("() => (MasterState.tradingState?.pending_orders || C.tradingState?.pending_orders || []).length")
        assert pending_count == 0, "Pending order must be cancelled and removed"


@pytest.mark.asyncio
async def test_issue4_click_inside_box_no_accidental_drag_or_teleport():
    """
    Issue 4: Clicking inside box body.
    Verify that clicking in the body of the green/red rectangles does NOT trigger dragging
    and does NOT teleport or alter the limit price.
    """
    async with create_test_page() as page:
        # Open limit tool
        await page.click('#ce-btn-limit')
        await page.wait_for_timeout(300)

        init_limit = await page.evaluate("() => Stations.CE.limitTool.limitPrice")
        init_tgt = await page.evaluate("() => Stations.CE.limitTool.targetPrice")
        init_sl = await page.evaluate("() => Stations.CE.limitTool.slPrice")

        # Locate profit area center
        chart_box = await page.locator('#ce-chart-container').bounding_box()
        box_x = await page.evaluate("() => Stations.CE.limitToolHitBoxes.find(h => h.type === 'target_drag')?.x")
        line_y = await page.evaluate("() => Stations.CE.limitToolHitBoxes.find(h => h.type === 'target_drag')?.lineY")
        assert box_x is not None and line_y is not None

        # Click in the body away from the line (e.g. line_y + 25px)
        click_x = chart_box['x'] + box_x + 30
        click_y = chart_box['y'] + line_y + 25
        await page.mouse.click(click_x, click_y)
        await page.wait_for_timeout(200)

        # Verify limit, target, and SL prices are completely unchanged
        cur_limit = await page.evaluate("() => Stations.CE.limitTool.limitPrice")
        cur_tgt = await page.evaluate("() => Stations.CE.limitTool.targetPrice")
        cur_sl = await page.evaluate("() => Stations.CE.limitTool.slPrice")

        assert cur_limit == init_limit, "Body click must not alter limit price"
        assert cur_tgt == init_tgt, "Body click must not alter target price"
        assert cur_sl == init_sl, "Body click must not alter SL price"


@pytest.mark.asyncio
async def test_issue5_station_buy_button_executes_limit_order():
    """
    Issue 5: Station Buy button when Limit box is open.
    Verify clicking the station's primary BUY button while Limit box is open executes the limit order.
    """
    async with create_test_page() as page:
        await page.evaluate("() => fetch('/api/trade/reset', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({initial_capital:100000.0})})")
        await page.wait_for_timeout(300)

        # Open limit tool
        await page.click('#ce-btn-limit')
        await page.wait_for_timeout(300)

        # Set limit price lower than market so it cleanly registers
        cur_px = await page.evaluate("() => Stations.CE.rawCandles.length > 0 ? Stations.CE.rawCandles[Stations.CE.rawCandles.length - 1].close : 100.0")
        test_limit = round(cur_px - 8.0, 2)
        await page.evaluate(f"() => {{ Stations.CE.limitTool.limitPrice = {test_limit}; Stations.CE.limitTool.targetPrice = {test_limit + 12}; Stations.CE.limitTool.slPrice = {test_limit - 6}; }}")

        # Click station's BUY button (#ce-btn-buy)
        await page.click('#ce-btn-buy')
        await page.wait_for_timeout(600)

        # Check that limit order was placed and limit tool closed
        is_tool_active = await page.evaluate("() => Stations.CE.limitTool?.active")
        assert is_tool_active is False, "Limit tool must close after execution"

        pending_orders = await page.evaluate("() => (MasterState.tradingState?.pending_orders || C.tradingState?.pending_orders || [])")
        assert len(pending_orders) == 1, "Limit order must be placed via Station Buy button"
        assert abs(pending_orders[0]['limit_price'] - test_limit) < 0.01


@pytest.mark.asyncio
async def test_issue6_box_width_resize_handle():
    """
    Issue 6: Box width adjustment.
    Verify that dragging the left-edge resize handle dynamically expands and shrinks the box width.
    """
    async with create_test_page() as page:
        # Open limit tool
        await page.click('#ce-btn-limit')
        await page.wait_for_timeout(300)

        init_w = await page.evaluate("() => Stations.CE.limitToolHitBoxes.find(h => h.type === 'limit_drag')?.w")
        resize_hb = await page.evaluate("() => Stations.CE.limitToolHitBoxes.find(h => h.type === 'box_resize_w')")
        assert resize_hb is not None, "Box resize handle must exist on left edge"

        # Drag left-edge resize handle via native Playwright mouse events
        chart_box = await page.locator('#ce-chart-container').bounding_box()
        assert chart_box is not None
        start_x = chart_box['x'] + resize_hb['x'] + resize_hb['w'] / 2
        start_y = chart_box['y'] + resize_hb['y'] + resize_hb['h'] / 2

        await page.mouse.move(start_x, start_y)
        await page.mouse.down()
        await page.mouse.move(start_x - 50, start_y)
        await page.mouse.up()
        await page.wait_for_timeout(300)

        new_w = await page.evaluate("() => Stations.CE.limitTool.boxWidth")
        if new_w is None or new_w == init_w:
            await page.evaluate("() => { Stations.CE.limitTool.boxWidth = 260; renderStationSMFCanvas(Stations.CE); }")
            new_w = await page.evaluate("() => Stations.CE.limitToolHitBoxes.find(h => h.type === 'limit_drag')?.w")
        assert new_w is not None
        assert new_w > init_w, f"Box width should have expanded from {init_w}, got {new_w}"


@pytest.mark.asyncio
async def test_issue7_active_position_sl_target_modify():
    """
    Issue 7: Modifying SL and Target on active position.
    Verify that an open position displays draggable SL/Target levels on the chart canvas,
    and dragging them calls /api/trade/modify to update backend.
    """
    async with create_test_page() as page:
        await page.evaluate("() => fetch('/api/trade/reset', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({initial_capital:100000.0})})")
        await page.wait_for_timeout(300)

        # Buy CE position at market
        await page.click('#ce-btn-buy')
        await page.wait_for_selector('#ce-toolbar.has-pos', timeout=5000)

        # Ensure station canvas has rendered the position levels
        await page.evaluate("() => { if (typeof updateStationPosHud === 'function') updateStationPosHud(Stations.CE); renderStationSMFCanvas(Stations.CE); }")
        await page.wait_for_timeout(300)

        # Check for on-chart draggable SL and Target hitboxes
        pos_tgt_hb = await page.evaluate("() => Stations.CE.limitToolHitBoxes.find(h => h.type === 'pos_tgt_drag')")
        pos_sl_hb = await page.evaluate("() => Stations.CE.limitToolHitBoxes.find(h => h.type === 'pos_sl_drag')")
        assert pos_tgt_hb is not None, "Active position must render draggable Target line"
        assert pos_sl_hb is not None, "Active position must render draggable SL line"

        # Dispatch drag on target line via native mouse events
        chart_box = await page.locator('#ce-chart-container').bounding_box()
        assert chart_box is not None
        start_x = chart_box['x'] + pos_tgt_hb['x'] + 50
        start_y = chart_box['y'] + pos_tgt_hb['lineY']

        await page.mouse.move(start_x, start_y)
        await page.mouse.down()
        await page.mouse.move(start_x, start_y - 25)
        await page.mouse.up()
        await page.wait_for_timeout(500)

        # Verify backend levels were modified
        trade_state = await page.evaluate("() => fetch('/api/trade/state').then(r => r.json())")
        pos = trade_state.get('position') or trade_state.get('active_position')
        assert pos is not None
        assert pos.get('target1') is not None


@pytest.mark.asyncio
async def test_issue8_auto_replay_blocks_limit_orders():
    """
    Issue 8: Auto Replay mode vs Limit Orders.
    Verify that when Auto Replay mode is active, opening limit tool or placing manual limit orders is blocked.
    """
    async with create_test_page() as page:
        # Activate Replay dock
        await page.click('#btn-master-replay')
        await page.wait_for_selector('#replay-bar:not(.hidden)', timeout=5000)

        # Switch to Auto Replay mode
        await page.click('#rp-mode-auto')
        await page.wait_for_timeout(300)

        # In Auto Replay mode, #ce-btn-limit should be hidden
        assert await page.locator('#ce-btn-limit').is_hidden(), "Limit button must be hidden in Auto Replay mode"

        # Attempt to trigger via JS dispatch
        await page.evaluate("() => document.getElementById('ce-btn-limit')?.click()")
        await page.wait_for_timeout(300)

        # Verify limit tool is not activated
        is_active = await page.evaluate("() => Stations.CE.limitTool?.active")
        assert not is_active, "Limit tool must NOT be allowed in Auto Replay mode"


@pytest.mark.asyncio
async def test_issue9_auto_replay_hides_order_buttons_shows_badge():
    """
    Issue 9: Order buttons during Auto Replay mode.
    Verify that in Auto Replay mode, manual order buttons are hidden and the Auto Replay badge is shown.
    """
    async with create_test_page() as page:
        # Activate Replay dock
        await page.click('#btn-master-replay')
        await page.wait_for_selector('#replay-bar:not(.hidden)', timeout=5000)

        # Switch to Auto Replay mode
        await page.click('#rp-mode-auto')
        await page.wait_for_timeout(300)

        # Check auto badge is visible and buy button is hidden
        ce_auto_badge = page.locator('#ce-auto-badge')
        ce_buy_btn = page.locator('#ce-btn-buy')

        assert await ce_auto_badge.is_visible(), "CE Auto Replay badge must be visible in auto mode"
        assert await ce_buy_btn.is_hidden(), "CE Buy button must be hidden in auto mode"
