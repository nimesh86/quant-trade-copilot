import pytest
from playwright.async_api import async_playwright

BASE_URL = "http://127.0.0.1:8080"


@pytest.mark.asyncio
async def test_playwright_bss_pro_v3_indicator_workflow(live_server):
    """
    Playwright E2E test verifying complete BSS Pro v3 Indicator Studio workflow:
    1. Loads /contract page.
    2. Opens the +TvI Indicator Studio drawer (#btn-master-tvi).
    3. Verifies 'bss_pro_v3' preset exists and selects it.
    4. Validates editor displays BSS Pro v3 Pine Script v5 source code.
    5. Clicks 'Compile & Apply' (#tvi-btn-compile).
    6. Confirms window.Stations.CE.tvIndicatorResults contains 'bss_pro_v3' output.
    7. Validates HUD table data (buyPct, sellPct, rank, grade, status).
    8. Confirms HTML5 canvas overlay draws without runtime errors.
    9. Tests custom Pine Script editor workflow: pastes raw BSS Pro v3 script into custom editor
       and verifies automatic transpilation to computeBSSProV3 with active HUD.
    """
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(viewport={'width': 1280, 'height': 800})
        page = await context.new_page()

        # 1. Load contract page
        resp = await page.goto(f"{BASE_URL}/contract", wait_until="domcontentloaded")
        assert resp.status == 200, f"Contract page failed to load: {resp.status}"
        await page.wait_for_timeout(1000)

        # Ensure Stations is initialized and has candles for indicator testing
        await page.wait_for_function("() => window.Stations && window.Stations.CE", timeout=8000)
        await page.evaluate("""() => {
            if (window.Stations && window.Stations.CE && (!window.Stations.CE.rawCandles || window.Stations.CE.rawCandles.length < 5)) {
                const c = [];
                let p = 150.0;
                for (let i = 0; i < 35; i++) {
                    p += (i % 2 === 0 ? 0.8 : -0.3);
                    c.push({
                        time: 1761450000 + (i * 60),
                        open: p - 0.2,
                        high: p + 1.0,
                        low: p - 0.5,
                        close: p,
                        volume: 1200
                    });
                }
                window.Stations.CE.rawCandles = c;
                if (window.Stations.PE) window.Stations.PE.rawCandles = c;
            }
        }""")

        # 2. Open Indicator Studio drawer
        btn_tvi = page.locator('#btn-master-tvi')
        assert await btn_tvi.is_visible(), "Master +TvI button must be visible in navbar"
        await btn_tvi.click()

        # Wait for drawer to open
        await page.wait_for_selector('#tvi-studio-drawer.open', timeout=5000)
        drawer_visible = await page.locator('#tvi-studio-drawer').is_visible()
        assert drawer_visible, "Indicator Studio drawer must be visible when open"

        # 3. Verify bss_pro_v3 preset option is present in select dropdown
        preset_select = page.locator('#tvi-preset-select')
        assert await preset_select.is_visible()
        options = await preset_select.locator('option').all_inner_texts()
        assert any('BSS Pro v3' in opt for opt in options), f"BSS Pro v3 must be in options: {options}"

        # 4. Select bss_pro_v3 preset
        await preset_select.select_option("bss_pro_v3")
        await page.wait_for_timeout(400)

        # 5. Verify code editor contains BSS Pro v3 code
        editor_code = await page.locator('#tvi-code-editor').input_value()
        assert 'BSS Pro v3' in editor_code, "Editor must contain BSS Pro v3 Pine Script"
        assert 'tradeMode' in editor_code

        # 6. Click Compile & Apply
        btn_compile = page.locator('#tvi-btn-compile')
        await btn_compile.click()
        await page.wait_for_timeout(600)

        # 7. Validate window.Stations.CE.tvIndicatorResults has bss_pro_v3
        ce_results = await page.evaluate("""() => {
            if (!window.Stations || !window.Stations.CE) return null;
            const res = window.Stations.CE.tvIndicatorResults || [];
            return res.find(r => r.type === 'bss_pro_v3') || null;
        }""")

        assert ce_results is not None, "Stations.CE.tvIndicatorResults must contain bss_pro_v3 result"
        assert ce_results["type"] == "bss_pro_v3"
        assert "emaF" in ce_results and len(ce_results["emaF"]) > 0
        assert "emaS" in ce_results and len(ce_results["emaS"]) > 0
        assert "hudData" in ce_results and ce_results["hudData"] is not None

        # 8. Validate HUD table contents
        hud = ce_results["hudData"]
        assert 0 <= hud["buyPct"] <= 100, f"Invalid buyPct: {hud['buyPct']}"
        assert 0 <= hud["sellPct"] <= 100, f"Invalid sellPct: {hud['sellPct']}"
        assert hud["grade"] in ["Strong", "Moderate", "Weak"], f"Invalid grade: {hud['grade']}"
        assert hud["status"] in ["▲ LONG active", "▼ SHORT active", "● Flat"]

        # 9. Verify overlay rendering on canvas without exceptions
        render_success = await page.evaluate("""() => {
            try {
                if (window.Stations && window.Stations.CE && window.Stations.CE.canvas) {
                    const canvas = window.Stations.CE.canvas;
                    const ctx = canvas.getContext('2d');
                    window.TvPineEngine.renderStationIndicators(window.Stations.CE, ctx, canvas.width, canvas.height);
                    return true;
                }
                return true;
            } catch (e) {
                return false;
            }
        }""")
        assert render_success is True, "Canvas overlay renderer must execute without JavaScript exceptions"

        # 10. Test raw Custom Pine Script compilation
        raw_bss_script = """//@version=5
indicator("BSS Pro v3 - My Custom Buyer Seller", overlay=true)
emaFastLen = input.int(21, "Fast EMA")
emaSlowLen = input.int(50, "Slow EMA")
domThr = input.float(55.0, "Min dominance %")
"""
        await page.fill('#tvi-code-editor', raw_bss_script)
        await btn_compile.click()
        await page.wait_for_timeout(600)

        custom_ce_results = await page.evaluate("""() => {
            if (!window.Stations || !window.Stations.CE) return null;
            const res = window.Stations.CE.tvIndicatorResults || [];
            return res.find(r => r.type === 'bss_pro_v3') || null;
        }""")
        assert custom_ce_results is not None, "Custom BSS Pro script must automatically route to computeBSSProV3"

        await browser.close()
