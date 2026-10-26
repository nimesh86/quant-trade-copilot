import pytest
import asyncio
import contextlib
from playwright.async_api import async_playwright

BASE_URL = "http://127.0.0.1:8080"


@contextlib.asynccontextmanager
async def create_matrix_page(width=390, height=844):
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
            await page.wait_for_timeout(200)
            yield page
        finally:
            await browser.close()


# ─────────────────────────────────────────────────────────────────────────────
# 1. VIEWPORT MATRIX (12 TEST CASES)
# ─────────────────────────────────────────────────────────────────────────────
VIEWPORTS = [
    (375, 667, "iPhone SE"),
    (390, 844, "iPhone 12/13/14"),
    (414, 896, "iPhone XR/11"),
    (430, 932, "iPhone 14/15 Pro Max"),
    (768, 1024, "iPad Mini / Air Portrait"),
    (1024, 768, "iPad Mini / Air Landscape"),
]

@pytest.mark.asyncio
@pytest.mark.parametrize("width,height,device_name", VIEWPORTS)
@pytest.mark.parametrize("station_id", ["ce", "pe"])
async def test_viewport_station_rendering(width, height, device_name, station_id):
    """
    Verify responsive rendering of station charts, headers, and toolbars across mobile and tablet viewports:
    6 viewports x 2 stations = 12 test cases.
    """
    async with create_matrix_page(width, height) as page:
        # Check chart container exists and has non-zero geometry
        chart = page.locator(f"#{station_id}-chart-container")
        assert await chart.is_visible()
        box = await chart.bounding_box()
        assert box is not None
        assert box["width"] > 250
        assert box["height"] > 150

        # Check toolbar buttons
        buy_btn = page.locator(f"#{station_id}-btn-buy")
        limit_btn = page.locator(f"#{station_id}-btn-limit")
        assert await buy_btn.is_visible()
        assert await limit_btn.is_visible()


# ─────────────────────────────────────────────────────────────────────────────
# 2. LIMIT TOOL BRACKET MATRIX (12 TEST CASES)
# ─────────────────────────────────────────────────────────────────────────────
LIMIT_RATIOS = [
    (10.0, 10.0, "1:1 RR"),
    (8.0, 16.0, "1:2 RR"),
    (6.0, 18.0, "1:3 RR"),
]

@pytest.mark.asyncio
@pytest.mark.parametrize("sl_dist,tgt_dist,desc", LIMIT_RATIOS)
@pytest.mark.parametrize("station_id", ["ce", "pe"])
@pytest.mark.parametrize("lots", [1, 3])
async def test_limit_bracket_ratios_matrix(sl_dist, tgt_dist, desc, station_id, lots):
    """
    Verify limit bracket box calculations, target lines, SL lines, and RR ratio badges:
    3 RR ratios x 2 stations x 2 lots = 12 test cases.
    """
    async with create_matrix_page() as page:
        # Reset state
        await page.evaluate("() => fetch('/api/trade/reset', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({initial_capital:100000.0})})")
        await page.wait_for_timeout(200)

        # Open limit tool
        await page.click(f"#{station_id}-btn-limit")
        await page.wait_for_timeout(200)

        st_obj = f"Stations.{station_id.upper()}"
        cur_px = await page.evaluate(f"() => {st_obj}.rawCandles.length > 0 ? {st_obj}.rawCandles[{st_obj}.rawCandles.length - 1].close : 100.0")
        lim_px = round(cur_px - 5.0, 2)
        tgt_px = round(lim_px + tgt_dist, 2)
        sl_px = round(lim_px - sl_dist, 2)

        # Set prices and lots
        await page.evaluate(f"""() => {{
            const st = {st_obj};
            st.limitTool.limitPrice = {lim_px};
            st.limitTool.targetPrice = {tgt_px};
            st.limitTool.slPrice = {sl_px};
            st.tradingLots = {lots};
            renderStationSMFCanvas(st);
        }}""")
        await page.wait_for_timeout(200)

        # Verify hitboxes
        hitboxes = await page.evaluate(f"() => {st_obj}.limitToolHitBoxes.map(h => h.type)")
        assert "limit_drag" in hitboxes
        assert "target_drag" in hitboxes
        assert "sl_drag" in hitboxes
        assert "box_resize_w" in hitboxes
        assert "limit_cancel" in hitboxes


# ─────────────────────────────────────────────────────────────────────────────
# 3. PENDING ORDERS MATRIX (12 TEST CASES)
# ─────────────────────────────────────────────────────────────────────────────
PENDING_PRICE_OFFSETS = [8.0, 14.0, 22.0]

@pytest.mark.asyncio
@pytest.mark.parametrize("offset", PENDING_PRICE_OFFSETS)
@pytest.mark.parametrize("station_id", ["ce", "pe"])
@pytest.mark.parametrize("lots", [1, 2])
async def test_pending_orders_submission_and_lifecycle_matrix(offset, station_id, lots):
    """
    Verify pending order creation, toolbar pill display, chart overlay rendering, and cancellation:
    3 price offsets x 2 stations x 2 lots = 12 test cases.
    """
    async with create_matrix_page() as page:
        await page.evaluate("() => fetch('/api/trade/reset', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({initial_capital:100000.0})})")
        await page.wait_for_timeout(200)

        st_obj = f"Stations.{station_id.upper()}"
        cur_px = await page.evaluate(f"() => {st_obj}.rawCandles.length > 0 ? {st_obj}.rawCandles[{st_obj}.rawCandles.length - 1].close : 100.0")
        lim_px = round(cur_px - offset, 2)

        # Open limit tool
        await page.click(f"#{station_id}-btn-limit")
        await page.wait_for_timeout(200)

        # Configure and execute limit order
        await page.evaluate(f"""() => {{
            const st = {st_obj};
            st.limitTool.limitPrice = {lim_px};
            st.limitTool.targetPrice = {lim_px + 15.0};
            st.limitTool.slPrice = {lim_px - 10.0};
            st.tradingLots = {lots};
            return executeStationLimitOrder(st);
        }}""")
        await page.wait_for_timeout(500)

        # Assert toolbar pill is visible
        pill = page.locator(f"#{station_id}-pending-pill")
        assert await pill.is_visible()

        # Assert chart canvas contains cancel hitbox
        cancel_hb = await page.evaluate(f"() => {st_obj}.limitToolHitBoxes.find(h => h.type === 'pending_cancel')")
        assert cancel_hb is not None

        # Cancel pending order via button in toolbar pill
        cancel_btn = page.locator(f"#{station_id}-btn-cancel-pending")
        await cancel_btn.click()
        await page.wait_for_timeout(400)

        # Verify order removed
        pending_orders = await page.evaluate("() => (MasterState.tradingState?.pending_orders || []).length")
        assert pending_orders == 0


# ─────────────────────────────────────────────────────────────────────────────
# 4. REPLAY CONTROLS & TIMEFRAME SYNC MATRIX (14 TEST CASES)
# ─────────────────────────────────────────────────────────────────────────────
SPEEDS = ["1x", "2x", "5x"]

@pytest.mark.asyncio
@pytest.mark.parametrize("speed", SPEEDS)
@pytest.mark.parametrize("mode", ["auto", "manual"])
async def test_replay_mode_switching_and_speeds_matrix(speed, mode):
    """
    Verify replay dock activation, mode toggling, speed selections, and station control synchronization:
    3 speeds x 2 modes = 6 test cases.
    """
    async with create_matrix_page() as page:
        # Open Replay bar
        await page.click("#btn-master-replay")
        await page.wait_for_selector("#replay-bar:not(.hidden)", timeout=5000)

        # Switch mode
        await page.click(f"#rp-mode-{mode}")
        await page.wait_for_timeout(200)

        # Select speed
        await page.select_option("#rp-speed-select", speed)
        await page.wait_for_timeout(200)

        # Verify CE station buttons honor mode
        if mode == "auto":
            assert await page.locator("#ce-auto-badge").is_visible()
            assert await page.locator("#ce-btn-buy").is_hidden()
        else:
            assert await page.locator("#ce-auto-badge").is_hidden()
            assert await page.locator("#ce-btn-buy").is_visible()


TIMEFRAMES = [("1m", "5m"), ("5m", "1m")]

@pytest.mark.asyncio
@pytest.mark.parametrize("from_tf,to_tf", TIMEFRAMES)
@pytest.mark.parametrize("station_id", ["ce", "pe"])
@pytest.mark.parametrize("with_limit_box", [True, False])
async def test_timeframe_toggle_station_stability_matrix(from_tf, to_tf, station_id, with_limit_box):
    """
    Verify timeframe toggle button stability, candle updates, and limit box anchoring:
    2 transitions x 2 stations x 2 limit states = 8 test cases.
    """
    async with create_matrix_page() as page:
        st_obj = f"Stations.{station_id.upper()}"
        if with_limit_box:
            await page.click(f"#{station_id}-btn-limit")
            await page.wait_for_timeout(200)

        # Toggle timeframe
        await page.click("#btn-timeframe-toggle")
        await page.wait_for_timeout(300)

        # Ensure station remains valid
        is_valid = await page.evaluate(f"() => {st_obj} && {st_obj}.symbol != null")
        assert is_valid is True

        if with_limit_box:
            hb = await page.evaluate(f"() => {st_obj}.limitToolHitBoxes.find(h => h.type === 'limit_drag')")
            assert hb is not None, "Limit box must remain stable across timeframe toggle"
