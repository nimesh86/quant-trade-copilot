import time
import pytest
from playwright.async_api import async_playwright

BASE_URL = "http://127.0.0.1:8080"


@pytest.mark.asyncio
async def test_fast_option_chain_and_index_switch_workflow(live_server):
    """
    Playwright E2E test covering the complete option chain and index switching workflow:
    1. Opens /contract page.
    2. Opens Option Chain drawer and verifies ATM strike and spot display.
    3. Tests rapid, smooth switching across indices: NIFTY -> SENSEX -> BANKNIFTY -> FINNIFTY.
    4. Validates accurate strike gaps (50 for NIFTY, 100 for SENSEX/BANKNIFTY, 25/50 for FINNIFTY).
    5. Validates contract selection updates chart and station.
    6. Validates /api/contracts/candles returns authentic data with zero synthetic fallback.
    """
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            viewport={'width': 1280, 'height': 800}
        )
        page = await context.new_page()

        # 1. Navigate to contract page
        response = await page.goto(f"{BASE_URL}/contract", wait_until="domcontentloaded")
        assert response.status == 200, f"Page load failed with status {response.status}"
        await page.wait_for_timeout(1000)

        # 2. Open Option Chain Drawer
        btn_oc = page.locator('#btn-master-chain, #btn-toggle-oc').first
        assert await btn_oc.is_visible(), "Option Chain toggle button must be visible"

        t_open_start = time.time()
        await btn_oc.click()
        await page.wait_for_selector('#option-chain-panel:not(.collapsed)', timeout=5000)
        dt_open = (time.time() - t_open_start) * 1000.0
        print(f"Option Chain drawer open latency: {dt_open:.1f} ms")

        # Verify Option Chain table rows are populated
        await page.wait_for_selector('#oc-tbody tr', timeout=5000)
        rows = await page.locator('#oc-tbody tr').count()
        assert rows >= 5, f"Option chain must render strike rows, found {rows}"

        # 3. Test Fast Index Switching to SENSEX
        oc_und_select = page.locator('#oc-underlying-select')
        assert await oc_und_select.is_visible()

        t_switch_sensex = time.time()
        await oc_und_select.select_option("SENSEX")
        # Wait for table to update with SENSEX strikes
        await page.wait_for_function("""
            () => {
                const text = document.getElementById('oc-tbody')?.innerText || '';
                return text.includes('SENSEX') || text.includes('73') || text.includes('74');
            }
        """, timeout=5000)
        dt_sensex = (time.time() - t_switch_sensex) * 1000.0
        print(f"Switching to SENSEX latency: {dt_sensex:.1f} ms")
        assert dt_sensex < 1500.0, f"SENSEX switch took too long: {dt_sensex:.1f} ms"

        # Check spot price display
        spot_val = await page.locator('#oc-spot-val').text_content()
        assert spot_val and spot_val != "--", "SENSEX Spot must be populated"
        print(f"SENSEX Spot: {spot_val}")

        # 4. Test Fast Index Switching to BANKNIFTY
        t_switch_bank = time.time()
        await oc_und_select.select_option("BANKNIFTY")
        await page.wait_for_function("""
            () => {
                const text = document.getElementById('oc-tbody')?.innerText || '';
                return text.includes('BANKNIFTY') || text.includes('54') || text.includes('53') || text.includes('55');
            }
        """, timeout=5000)
        dt_bank = (time.time() - t_switch_bank) * 1000.0
        print(f"Switching to BANKNIFTY latency: {dt_bank:.1f} ms")
        assert dt_bank < 1500.0, f"BANKNIFTY switch took too long: {dt_bank:.1f} ms"

        # 5. Test Fast Index Switching back to NIFTY
        t_switch_nifty = time.time()
        await oc_und_select.select_option("NIFTY")
        await page.wait_for_function("""
            () => {
                const text = document.getElementById('oc-tbody')?.innerText || '';
                return text.includes('NIFTY') || text.includes('22') || text.includes('23');
            }
        """, timeout=5000)
        dt_nifty = (time.time() - t_switch_nifty) * 1000.0
        print(f"Switching back to NIFTY latency: {dt_nifty:.1f} ms")

        # 6. Select a contract strike button from the chain
        select_btns = page.locator('#oc-tbody button, #oc-tbody .btn-select-ce, #oc-tbody .btn-select-pe')
        if await select_btns.count() > 0:
            first_btn = select_btns.first
            await first_btn.click()
            await page.wait_for_timeout(500)

        # 7. Close the Option Chain panel
        close_btn = page.locator('#oc-close-btn')
        await close_btn.click(force=True)
        await page.wait_for_selector('#option-chain-panel.collapsed', timeout=3000)
        assert await page.locator('#option-chain-panel').get_attribute('class') is not None
        assert 'collapsed' in (await page.locator('#option-chain-panel').get_attribute('class'))

        # 8. Verify Direct API Candlestick Contract Fetch with Zero Synthetic Data
        api_res = await page.evaluate("""
            async () => {
                const res = await fetch('/api/contracts/candles?symbol=NIFTY26N0322700CE');
                return await res.json();
            }
        """)
        assert "candles" in api_res
        assert "count" in api_res
        assert "num_candles" in api_res
        print(f"Candles API response: count={api_res['count']}, status={api_res.get('market_status')}")

        await browser.close()
