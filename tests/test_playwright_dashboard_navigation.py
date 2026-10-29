import pytest
from playwright.async_api import async_playwright

BASE_URL = "http://127.0.0.1:8080"


@pytest.mark.asyncio
async def test_dashboard_header_keyvault_and_dual_option_chart_navigation(live_server):
    """
    Playwright test verifying:
    1. Only ONE Key Vault button exists on the dashboard header.
    2. Clicking Key Vault button opens the Key Vault settings modal.
    3. The mode bar contains the 'Dual Option Chart' button.
    4. Clicking 'Dual Option Chart' cleanly redirects to /contract.
    5. No duplicate or mislabeled 'Key Vault' navigation buttons exist.
    """
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(viewport={'width': 1366, 'height': 850})
        page = await context.new_page()

        # 1. Open dashboard index page
        res = await page.goto(f"{BASE_URL}/", wait_until="domcontentloaded")
        assert res.status == 200, f"Dashboard load failed with status {res.status}"
        await page.wait_for_timeout(1000)

        # 2. Check for Key Vault buttons: must find EXACTLY ONE button whose text includes 'Key Vault'
        key_vault_buttons = page.locator("button:has-text('Key Vault'), a:has-text('Key Vault')")
        count = await key_vault_buttons.count()
        assert count == 1, f"Expected exactly 1 Key Vault button on dashboard, found {count}"

        vault_btn = page.locator("#btn-open-vault")
        assert await vault_btn.is_visible(), "Key Vault button (#btn-open-vault) must be visible"

        # 3. Click Key Vault button and ensure modal opens
        await vault_btn.click()
        await page.wait_for_selector("#vault-modal-backdrop", state="visible", timeout=3000)
        modal_visible = await page.locator("#vault-modal-backdrop").is_visible()
        assert modal_visible, "Key Vault modal overlay must be displayed upon click"

        # Close the modal
        close_btn = page.locator("#vault-modal-backdrop .modal-close-btn, #btn-close-vault").first
        if await close_btn.is_visible():
            await close_btn.click()
            await page.wait_for_timeout(300)

        # 4. Check Dual Option Chart button in the mode bar
        chart_btn = page.locator("#mode-btn-contract")
        assert await chart_btn.is_visible(), "Dual Option Chart button (#mode-btn-contract) must be visible"
        chart_text = await chart_btn.text_content()
        assert "Dual Option Chart" in chart_text, f"Expected 'Dual Option Chart' text, got: {chart_text}"
        assert (await chart_btn.get_attribute("href")) == "/contract", "Must link to /contract"

        # 5. Click Dual Option Chart button and verify navigation to contract screen
        await chart_btn.click()
        await page.wait_for_url("**/contract*", timeout=5000)
        assert "/contract" in page.url, f"Expected URL to be /contract, got: {page.url}"

        await browser.close()
