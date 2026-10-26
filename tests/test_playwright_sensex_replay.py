import pytest
import os
import re
from playwright.async_api import async_playwright

BASE_URL = os.environ.get("BASE_URL", "http://127.0.0.1:8080")


@pytest.mark.asyncio
async def test_1_verify_sensex_contract_loaded_via_option_chain():
    """
    Test 1: Verify Contract Loaded or not
      - Open the option chain modal
      - Select SENSEX as index
      - Select Current Expiry
      - Select ATM PUT/CE
      - Verify on the contract screen that it is loaded successfully
    """
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            viewport={"width": 1440, "height": 900},
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
        page = await context.new_page()

        print(f"\n[Test 1] Navigating to {BASE_URL}/contract...")
        await page.goto(f"{BASE_URL}/contract", wait_until="domcontentloaded")
        await page.wait_for_selector("#stations-stream", timeout=10000)

        # 1. Open the Option Chain modal
        print("[Test 1] Step 1: Opening Option Chain modal...")
        oc_panel = page.locator("#option-chain-panel")
        is_collapsed = "collapsed" in (await oc_panel.get_attribute("class") or "")
        if is_collapsed:
            toggle_btn = page.locator("#btn-master-chain")
            await toggle_btn.click()
            await page.wait_for_selector("#option-chain-panel:not(.collapsed)", timeout=5000)

        assert not ("collapsed" in (await oc_panel.get_attribute("class") or "")), (
            "Option Chain panel must be opened and not have 'collapsed' class"
        )

        # 2. Select SENSEX as index
        print("[Test 1] Step 2: Selecting SENSEX as underlying index...")
        await page.select_option("#oc-underlying-select", "SENSEX")
        await page.dispatch_event("#oc-underlying-select", "change")

        # Wait for SENSEX data to be fetched and optionChainData.underlying to become SENSEX
        await page.wait_for_function("""() => {
            const data = window.MasterState && window.MasterState.optionChainData;
            return data && data.underlying === 'SENSEX' && (data.spot > 60000 || data.spot_price > 60000);
        }""", timeout=20000)

        spot_text = await page.locator("#oc-spot-val").text_content()
        clean_spot = float(re.sub(r"[^\d.]", "", spot_text or "0"))
        print(f"[Test 1] SENSEX spot price resolved: ₹{clean_spot:,.2f}")
        assert clean_spot > 60000.0, f"Expected SENSEX spot > 60,000, got ₹{clean_spot}"

        # 3. Select Current Expiry
        print("[Test 1] Step 3: Selecting current expiry...")
        await page.wait_for_selector("#oc-expiry-select option", timeout=5000)
        first_expiry = await page.locator("#oc-expiry-select option").first.get_attribute("value")
        assert first_expiry, "Available expiries list must not be empty"
        await page.select_option("#oc-expiry-select", first_expiry)
        print(f"[Test 1] Current expiry selected: {first_expiry}")

        # Wait for option chain table strikes to render
        await page.wait_for_selector("#oc-tbody tr.oc-row", timeout=8000)
        row_count = await page.locator("#oc-tbody tr.oc-row").count()
        assert row_count > 0, f"Option Chain table must have strike rows, got {row_count}"
        print(f"[Test 1] Option Chain rendered with {row_count} strikes.")

        # 4. Select ATM PUT/CE
        print("[Test 1] Step 4: Selecting ATM Strike...")
        atm_rows = page.locator("#oc-tbody tr.oc-row.atm-row")
        if await atm_rows.count() > 0:
            target_row = atm_rows.first
        else:
            # Fallback to middle row if exact ATM class is animating
            target_row = page.locator("#oc-tbody tr.oc-row").nth(row_count // 2)

        strike_cell_txt = await target_row.locator(".col-strike").text_content()
        atm_strike_val = int(re.search(r"\d+", strike_cell_txt).group())
        print(f"[Test 1] Selected ATM Strike: {atm_strike_val}")

        # Click the ATM strike row to load both Call and Put stations simultaneously
        call_action_btn = target_row.locator(".call-action-btn")
        if await call_action_btn.is_visible():
            await call_action_btn.click()
        else:
            await target_row.click()

        # 5. Verify on the contract screen that it is loaded successfully
        print("[Test 1] Step 5: Verifying contract screen loaded successfully...")
        # Close option chain if still open
        if not ("collapsed" in (await oc_panel.get_attribute("class") or "")):
            close_btn = page.locator("#oc-close-btn")
            if await close_btn.is_visible():
                await close_btn.click()
            await page.wait_for_selector("#option-chain-panel.collapsed", timeout=5000)

        # Verify strike on master header
        cur_strike_el = page.locator("#cur-strike-val")
        await page.wait_for_function(
            "(val) => document.getElementById('cur-strike-val') && document.getElementById('cur-strike-val').textContent.includes(String(val))",
            arg=atm_strike_val,
            timeout=8000
        )
        loaded_strike_txt = await cur_strike_el.text_content()
        assert str(atm_strike_val) in (loaded_strike_txt or ""), (
            f"Master header must reflect ATM strike {atm_strike_val}, got {loaded_strike_txt}"
        )

        # Verify CE Station contract symbol
        ce_sym_el = page.locator("#ce-symbol-text")
        await page.wait_for_function(
            "() => document.getElementById('ce-symbol-text') && document.getElementById('ce-symbol-text').textContent.includes('SENSEX')",
            timeout=8000
        )
        ce_sym = await ce_sym_el.text_content()
        assert "SENSEX" in ce_sym and "CE" in ce_sym, f"Expected SENSEX CE symbol, got {ce_sym}"

        # Verify PE Station contract symbol
        pe_sym_el = page.locator("#pe-symbol-text")
        await page.wait_for_function(
            "() => document.getElementById('pe-symbol-text') && document.getElementById('pe-symbol-text').textContent.includes('SENSEX')",
            timeout=8000
        )
        pe_sym = await pe_sym_el.text_content()
        assert "SENSEX" in pe_sym and "PE" in pe_sym, f"Expected SENSEX PE symbol, got {pe_sym}"

        # Verify both parallel stations are visible
        assert await page.locator("#station-ce").is_visible(), "Station CE must be visible"
        assert await page.locator("#station-pe").is_visible(), "Station PE must be visible"

        print(f"[Test 1] SUCCESS: SENSEX Strike {atm_strike_val} (CE: {ce_sym} | PE: {pe_sym}) loaded successfully!\n")
        await browser.close()


@pytest.mark.asyncio
async def test_2_verify_sensex_replay_mode_30min_and_premium():
    """
    Test 2: After running Test 1 and verifying it, Run the Replay Mode
      - Make sure candles are loaded
      - Run the replay mode
      - Keep automatic Trading
      - Start Run
      - Verify the first 30 minutes of candles are correct
      - Verify the premium is getting matched with ATM
    """
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            viewport={"width": 1440, "height": 900},
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
        page = await context.new_page()

        print(f"\n[Test 2] Navigating to {BASE_URL}/contract and preparing SENSEX ATM contract...")
        await page.goto(f"{BASE_URL}/contract", wait_until="domcontentloaded")
        await page.wait_for_selector("#stations-stream", timeout=10000)

        # 1. Load SENSEX ATM via Option Chain or direct loadStrikePair
        await page.click("#btn-master-chain")
        await page.wait_for_selector("#option-chain-panel:not(.collapsed)", timeout=5000)
        await page.select_option("#oc-underlying-select", "SENSEX")
        await page.dispatch_event("#oc-underlying-select", "change")
        await page.wait_for_function("""() => {
            const data = window.MasterState && window.MasterState.optionChainData;
            return data && data.underlying === 'SENSEX' && data.chain && data.chain.length > 0;
        }""", timeout=20000)
        await page.wait_for_selector("#oc-tbody tr.oc-row", timeout=8000)

        atm_rows = page.locator("#oc-tbody tr.oc-row.atm-row")
        target_row = atm_rows.first if (await atm_rows.count() > 0) else page.locator("#oc-tbody tr.oc-row").first
        await target_row.locator(".call-action-btn").click()

        # Close Option Chain modal
        if not ("collapsed" in (await page.locator("#option-chain-panel").get_attribute("class") or "")):
            await page.locator("#oc-close-btn").click()
            await page.wait_for_selector("#option-chain-panel.collapsed", timeout=5000)

        # 2. Make sure candles are loaded
        print("[Test 2] Step 1: Verifying candles are loaded in stations...")
        await page.wait_for_function("""() => {
            const ce = window.Stations && window.Stations.CE && window.Stations.CE.rawCandles;
            const pe = window.Stations && window.Stations.PE && window.Stations.PE.rawCandles;
            return (ce && ce.length >= 5) || (pe && pe.length >= 5);
        }""", timeout=15000)

        initial_candle_count = await page.evaluate("""() => {
            const ce = window.Stations.CE.rawCandles ? window.Stations.CE.rawCandles.length : 0;
            const pe = window.Stations.PE.rawCandles ? window.Stations.PE.rawCandles.length : 0;
            return Math.max(ce, pe);
        }""")
        print(f"[Test 2] Available candles confirmed: {initial_candle_count} bars.")
        assert initial_candle_count >= 5, "At least 5 candles must be loaded before launching replay"

        # 3. Run the Replay Mode
        print("[Test 2] Step 2: Entering Bar Replay mode...")
        await page.click("#btn-master-replay")
        await page.wait_for_selector("#replay-bar:not(.hidden)", timeout=5000)

        body_classes = await page.evaluate("() => document.body.className")
        assert "replay-mode-active" in body_classes, "Body must have 'replay-mode-active' class"

        # 4. Keep automatic Trading mode
        print("[Test 2] Step 3: Verifying Replay is in Automatic Trading mode...")
        auto_btn = page.locator("#rp-mode-auto")
        auto_btn_classes = await auto_btn.get_attribute("class") or ""
        if "active" not in auto_btn_classes:
            await auto_btn.click()
            await page.wait_for_timeout(200)

        mode_val = await page.evaluate("() => window.Replay ? window.Replay.mode : ''")
        assert mode_val == "auto", f"Expected Replay mode to be 'auto', got '{mode_val}'"
        assert "active" in (await auto_btn.get_attribute("class") or ""), "Auto mode button must have 'active' class"
        print("[Test 2] Confirmed: Automatic Trading mode is active.")

        # 5. Start Run from beginning of session
        print("[Test 2] Step 4: Starting Replay run from bar 0 (09:15 AM)...")
        # Set speed to 10x (40ms per bar) for efficient automated testing
        await page.select_option("#rp-speed-select", "40")

        # Reset scrubber to bar 0
        await page.evaluate("""() => {
            if (window.Replay) {
                window.Replay.currentIndex = 0;
                window.renderReplayStep();
            }
        }""")

        start_time_str = await page.locator("#rp-cur-time").text_content()
        print(f"[Test 2] Replay initial timestamp: {start_time_str}")

        # Start Run by clicking Play button
        play_btn = page.locator("#rp-btn-play")
        await play_btn.click()
        await page.wait_for_timeout(200)
        assert "playing" in (await play_btn.get_attribute("class") or ""), "Play button must enter playing state (⏸)"

        # Let the replay run through the first 30 minutes (progress through at least 30 1-minute bars)
        print("[Test 2] Step 5: Waiting for replay to progress through the first 30 minutes of session...")
        await page.wait_for_function("""() => {
            return window.Replay && window.Replay.currentIndex >= 30;
        }""", timeout=25000)

        # Pause replay once 30 minutes have played
        await play_btn.click()
        await page.wait_for_timeout(200)
        print("[Test 2] Replay paused at or after 30 minutes.")

        # 6. Verify the first 30 minutes of candles are correct
        print("[Test 2] Step 6: Verifying the first 30 minutes of candles are structurally correct...")
        candles_validation = await page.evaluate("""() => {
            const list = (window.Replay.fullCandles || (window.Stations.CE && window.Stations.CE.rawCandles) || []).slice(0, 30);
            return {
                totalCount: list.length,
                currentIndex: window.Replay.currentIndex,
                curTimeText: document.getElementById('rp-cur-time') ? document.getElementById('rp-cur-time').textContent.trim() : '',
                candles: list.map(c => ({
                    time: c.time,
                    open: c.open,
                    high: c.high,
                    low: c.low,
                    close: c.close,
                    volume: c.volume
                }))
            };
        }""")

        assert candles_validation["totalCount"] >= 30, (
            f"Expected at least 30 candles in dataset, got {candles_validation['totalCount']}"
        )
        assert candles_validation["currentIndex"] >= 30, (
            f"Replay index ({candles_validation['currentIndex']}) must be >= 30 bars"
        )

        for i, c in enumerate(candles_validation["candles"]):
            assert c["open"] > 0, f"Candle #{i} open price must be > 0 (got {c['open']})"
            assert c["high"] >= c["low"], f"Candle #{i} high ({c['high']}) must be >= low ({c['low']})"
            assert c["high"] >= min(c["open"], c["close"]), f"Candle #{i} high must be >= open/close"
            assert c["low"] <= max(c["open"], c["close"]), f"Candle #{i} low must be <= open/close"
            assert c["close"] > 0, f"Candle #{i} close price must be > 0 (got {c['close']})"
            if i > 0:
                prev_time = candles_validation["candles"][i - 1]["time"]
                assert c["time"] > prev_time, f"Candle #{i} timestamp must be strictly sequential (> {prev_time})"

        print(f"[Test 2] Verified: All 30 candles are valid, positive, and sequentially ordered.")
        print(f"[Test 2] Current replay time reached: {candles_validation['curTimeText']}")

        # 7. Verify the premium is getting matched with ATM
        print("[Test 2] Step 7: Verifying option premium matches ATM strike candle prices...")
        premium_data = await page.evaluate("""() => {
            const ce = window.Stations.CE.rawCandles;
            const pe = window.Stations.PE.rawCandles;
            const ceLast = (ce && ce.length > 0) ? ce[ce.length - 1].close : 0;
            const peLast = (pe && pe.length > 0) ? pe[pe.length - 1].close : 0;
            const ceLtpEl = document.getElementById('ce-ltp');
            const peLtpEl = document.getElementById('pe-ltp');
            return {
                ceClose: ceLast,
                peClose: peLast,
                ceLtpStr: ceLtpEl ? ceLtpEl.textContent.trim() : '',
                peLtpStr: peLtpEl ? peLtpEl.textContent.trim() : '',
                strike: window.MasterState ? window.MasterState.currentStrike : 0
            };
        }""")

        ce_displayed_ltp = float(re.sub(r"[^\d.]", "", premium_data["ceLtpStr"] or "0"))
        pe_displayed_ltp = float(re.sub(r"[^\d.]", "", premium_data["peLtpStr"] or "0"))

        print(f"[Test 2] ATM Strike: {premium_data['strike']}")
        print(f"[Test 2] CE Displayed LTP: ₹{ce_displayed_ltp:.2f} | Candle Close: ₹{premium_data['ceClose']:.2f}")
        print(f"[Test 2] PE Displayed LTP: ₹{pe_displayed_ltp:.2f} | Candle Close: ₹{premium_data['peClose']:.2f}")

        # Assert displayed LTP matches current replay candle close with zero lag (tolerance < 0.1)
        assert abs(ce_displayed_ltp - premium_data["ceClose"]) < 0.1, (
            f"CE displayed premium (₹{ce_displayed_ltp}) does not match ATM candle close (₹{premium_data['ceClose']})!"
        )
        assert abs(pe_displayed_ltp - premium_data["peClose"]) < 0.1, (
            f"PE displayed premium (₹{pe_displayed_ltp}) does not match ATM candle close (₹{premium_data['peClose']})!"
        )

        # Assert premiums are positive realistic option values
        assert ce_displayed_ltp > 0 and pe_displayed_ltp > 0, "ATM Option premiums must be strictly positive"
        assert premium_data["strike"] >= 60000, "Current strike must be a valid SENSEX ATM strike (>= 60,000)"

        print("[Test 2] SUCCESS: Option premiums match ATM candle prices seamlessly!\n")
        await browser.close()
