import pytest
from trading_engine.adapters.symbol_translator import (
    parse_canonical_symbol,
    to_breeze_params,
    to_canonical_symbol,
    _get_exchange_for_underlying
)
from trading_engine.core.keyvault import LocalKeyVault

def test_tc_sym_01_translate_nifty_breeze():
    """TC-SYM-01: Canonical symbol translation to Breeze params."""
    params = to_breeze_params("NIFTY26OCT22700CE")
    assert params is not None
    assert params["stock_code"] == "NIFTY"
    assert params["strike_price"] == "22700"
    assert params["right"] == "Call"
    assert params["exchange_code"] == "NFO"

def test_tc_sym_02_translate_banknifty_breeze():
    """TC-SYM-02: BANKNIFTY translation uses Breeze stock code CNXBAN."""
    params = to_breeze_params("BANKNIFTY26OCT51200PE")
    assert params is not None
    assert params["stock_code"] == "CNXBAN"
    assert params["strike_price"] == "51200"
    assert params["right"] == "Put"
    assert params["exchange_code"] == "NFO"

def test_tc_sym_03_sensex_exchange_resolution():
    """TC-SYM-03: SENSEX routes to BFO (BSE Options) exchange."""
    exch = _get_exchange_for_underlying("SENSEX", product_type="options")
    assert exch == "BFO"

def test_tc_sym_04_to_canonical_symbol():
    """TC-SYM-04: Constructs standard NSE symbol from Breeze API response."""
    sym = to_canonical_symbol("NIFTY", 22700, "Call", "2026-10-29")
    assert "NIFTY" in sym
    assert "22700" in sym
    assert "CE" in sym

def test_tc_sec_01_keyvault_encryption_at_rest(tmp_path):
    """TC-SEC-01: Keyvault encrypts secrets on disk using Fernet authenticated ciphertext."""
    kv = LocalKeyVault(vault_dir=tmp_path, use_keychain=False)
    kv.set_secret("kite_api_secret", "super_secret_token_12345")

    # Read raw ciphertext from vault.enc
    vault_file = tmp_path / "vault.enc"
    assert vault_file.exists()
    raw_content = vault_file.read_bytes()
    # The plain text must NOT appear anywhere in the raw file
    assert b"super_secret_token_12345" not in raw_content

    # Decrypted retrieval matches original
    decrypted = kv.get_secret("kite_api_secret")
    assert decrypted == "super_secret_token_12345"

def test_tc_sec_02_token_masking_in_api(client):
    """TC-SEC-02: Sensitive broker secrets are masked in API status responses."""
    res = client.get("/api/vault/broker-status")
    assert res.status_code == 200
    text = res.text
    # Raw credentials must never be leaked
    assert "super_secret" not in text
    assert "session_key" not in text or "configured" in text or "******" in text

def test_tc_sec_03_injection_resilience(client):
    """TC-SEC-03: Malicious inputs in query parameters return safe responses without crashing."""
    malicious_inputs = [
        "NIFTY; DROP TABLE trades;--",
        "../../etc/passwd",
        "<script>alert(1)</script>",
        "NIFTY' OR '1'='1"
    ]
    for m in malicious_inputs:
        res = client.get(f"/api/contracts/expiries?underlying={m}")
        # Must return valid HTTP response, never 500 unhandled server crash
        assert res.status_code in [200, 400, 422]


def test_tc_breeze_config_namespace_immunity():
    """TC-BRZ-01: Verifies trading_engine.config provides Breeze SDK constants and avoids module collision."""
    import trading_engine.config as cfg
    assert hasattr(cfg, "SECURITY_MASTER_URL")
    assert cfg.SECURITY_MASTER_URL == "https://directlink.icicidirect.com/MotherAppMaster/SecurityMaster.zip"
    assert hasattr(cfg, "APIEndPoint")
    assert hasattr(cfg, "API_URL")

    # BreezeConnect import must succeed without AttributeError: module 'config' has no attribute 'SECURITY_MASTER_URL'
    from breeze_connect import BreezeConnect
    client = BreezeConnect(api_key="mock_test_key")
    assert client is not None
    assert client.api_key == "mock_test_key"

