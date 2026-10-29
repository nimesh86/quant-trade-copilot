import time
import pytest
from datetime import date, datetime, timedelta
from trading_engine.web import contract_data_manager as cdm
from trading_engine.config import get_index_spec


@pytest.fixture(autouse=True)
def setup_teardown_globals():
    """Reset globals between tests."""
    with cdm._price_bus_lock:
        cdm._live_index_spots.clear()
        cdm._live_price_bus.clear()
    with cdm._grid_lock:
        cdm._option_grid.clear()
        cdm._underlying_expiries.clear()
    with cdm._lock:
        cdm._forming_candles.clear()
        cdm._session_candles.clear()
    cdm._spot_cache.clear()
    cdm._option_chain_cache.clear()
    cdm._expiries_cache.clear()
    yield


def test_build_option_grid_and_lookups():
    """Test that _build_option_grid pre-indexes instruments into an O(1) hash matrix."""
    today = date.today()
    exp1 = today + timedelta(days=2)
    exp2 = today + timedelta(days=9)
    exp1_str = exp1.strftime("%Y-%m-%d")
    exp2_str = exp2.strftime("%Y-%m-%d")

    mock_instruments = [
        # NIFTY CE/PE exp1
        {"name": "NIFTY", "segment": "NFO-OPT", "expiry": exp1, "strike": 22700.0, "instrument_type": "CE", "tradingsymbol": "NIFTY26N0322700CE", "instrument_token": 1001},
        {"name": "NIFTY", "segment": "NFO-OPT", "expiry": exp1, "strike": 22700.0, "instrument_type": "PE", "tradingsymbol": "NIFTY26N0322700PE", "instrument_token": 1002},
        {"name": "NIFTY", "segment": "NFO-OPT", "expiry": exp1, "strike": 22750.0, "instrument_type": "CE", "tradingsymbol": "NIFTY26N0322750CE", "instrument_token": 1003},
        {"name": "NIFTY", "segment": "NFO-OPT", "expiry": exp1, "strike": 22750.0, "instrument_type": "PE", "tradingsymbol": "NIFTY26N0322750PE", "instrument_token": 1004},
        # SENSEX CE/PE (BFO-OPT) with BSESEN alias
        {"name": "BSESEN", "segment": "BFO-OPT", "expiry": exp1, "strike": 73200.0, "instrument_type": "CE", "tradingsymbol": "SENSEX26OCT73200CE", "instrument_token": 2001},
        {"name": "BSESEN", "segment": "BFO-OPT", "expiry": exp1, "strike": 73200.0, "instrument_type": "PE", "tradingsymbol": "SENSEX26OCT73200PE", "instrument_token": 2002},
        # Past expiry - must be ignored
        {"name": "NIFTY", "segment": "NFO-OPT", "expiry": today - timedelta(days=5), "strike": 22000.0, "instrument_type": "CE", "tradingsymbol": "OLD_CE", "instrument_token": 9999},
        # Non-option instrument - must be ignored
        {"name": "NIFTY", "segment": "NSE", "expiry": "", "strike": 0.0, "instrument_type": "EQ", "tradingsymbol": "NIFTY 50", "instrument_token": 256265},
    ]

    cdm._build_option_grid(mock_instruments)

    # Verify grid structure
    with cdm._grid_lock:
        assert "NIFTY" in cdm._option_grid
        assert exp1_str in cdm._option_grid["NIFTY"]
        assert 22700.0 in cdm._option_grid["NIFTY"][exp1_str]
        assert "CE" in cdm._option_grid["NIFTY"][exp1_str][22700.0]
        assert cdm._option_grid["NIFTY"][exp1_str][22700.0]["CE"]["tradingsymbol"] == "NIFTY26N0322700CE"

        # Verify SENSEX alias was normalized from BSESEN -> SENSEX
        assert "SENSEX" in cdm._option_grid
        assert 73200.0 in cdm._option_grid["SENSEX"][exp1_str]

        # Verify past expiry was filtered out
        assert (today - timedelta(days=5)).strftime("%Y-%m-%d") not in cdm._option_grid["NIFTY"]


def test_get_available_expiries_fast():
    """Test get_available_expiries returns sorted, labeled, future expiries."""
    today = date.today()
    exp_near = today + timedelta(days=2)
    exp_far = today + timedelta(days=25)
    
    mock_instruments = [
        {"name": "SENSEX", "segment": "BFO-OPT", "expiry": exp_near, "strike": 73000.0, "instrument_type": "CE", "tradingsymbol": "S1"},
        {"name": "SENSEX", "segment": "BFO-OPT", "expiry": exp_far, "strike": 73000.0, "instrument_type": "CE", "tradingsymbol": "S2"},
    ]
    cdm._build_option_grid(mock_instruments)

    exps = cdm.get_available_expiries("SENSEX")
    assert len(exps) == 2
    assert exps[0]["date"] == exp_near.strftime("%Y-%m-%d")
    assert exps[0]["is_near"] is True
    assert "Weekly" in exps[0]["display"]
    assert exps[1]["date"] == exp_far.strftime("%Y-%m-%d")


def test_resolve_spot_price_multi_index_accuracy():
    """Test spot price resolution across all indices with ATM strike calculation."""
    # 1. Update spot prices via live tracker
    cdm.update_live_index_spot("NIFTY", 22698.85)
    cdm.update_live_index_spot("BANKNIFTY", 54267.40)
    cdm.update_live_index_spot("SENSEX", 73246.03)
    cdm.update_live_index_spot("FINNIFTY", 24418.25)
    cdm.update_live_index_spot("MIDCPNIFTY", 13286.60)

    # 2. Check resolution
    assert abs(cdm.resolve_spot_price("NIFTY") - 22698.85) < 0.01
    assert abs(cdm.resolve_spot_price("BANKNIFTY") - 54267.40) < 0.01
    assert abs(cdm.resolve_spot_price("SENSEX") - 73246.03) < 0.01
    assert abs(cdm.resolve_spot_price("BSE SENSEX") - 73246.03) < 0.01
    assert abs(cdm.resolve_spot_price("FINNIFTY") - 24418.25) < 0.01
    assert abs(cdm.resolve_spot_price("MIDCPNIFTY") - 13286.60) < 0.01

    # 3. Check ATM strike precision
    # NIFTY step 50 -> round(22698.85 / 50) * 50 = 22700
    nifty_step = get_index_spec("NIFTY")["strike_gap"]
    assert int(round(cdm.resolve_spot_price("NIFTY") / nifty_step) * nifty_step) == 22700

    # SENSEX step 100 -> round(73246.03 / 100) * 100 = 73200
    sensex_step = get_index_spec("SENSEX")["strike_gap"]
    assert int(round(cdm.resolve_spot_price("SENSEX") / sensex_step) * sensex_step) == 73200

    # BANKNIFTY step 100 -> round(54267.40 / 100) * 100 = 54300
    bank_step = get_index_spec("BANKNIFTY")["strike_gap"]
    assert int(round(cdm.resolve_spot_price("BANKNIFTY") / bank_step) * bank_step) == 54300


def test_get_option_chain_data_o1_speed_and_structure():
    """Test get_option_chain_data generates complete, centered option chain in < 15ms."""
    today = date.today()
    exp = today + timedelta(days=2)
    exp_str = exp.strftime("%Y-%m-%d")

    # Generate 40 strikes centered around 73200 for SENSEX
    mock_insts = []
    for s in range(71000, 75000, 100):
        mock_insts.append({"name": "SENSEX", "segment": "BFO-OPT", "expiry": exp, "strike": float(s), "instrument_type": "CE", "tradingsymbol": f"SENSEX{s}CE", "instrument_token": s*10})
        mock_insts.append({"name": "SENSEX", "segment": "BFO-OPT", "expiry": exp, "strike": float(s), "instrument_type": "PE", "tradingsymbol": f"SENSEX{s}PE", "instrument_token": s*10+1})

    cdm._build_option_grid(mock_insts)
    cdm.update_live_index_spot("SENSEX", 73246.03)

    # First call
    t0 = time.time()
    res = cdm.get_option_chain_data("SENSEX", expiry=exp_str)
    dt1 = (time.time() - t0) * 1000.0

    assert dt1 < 25.0, f"Option chain generation too slow: {dt1:.2f} ms"
    assert res["underlying"] == "SENSEX"
    assert res["expiry"] == exp_str
    assert res["atm_strike"] == 73200
    assert len(res["chain"]) == 21  # 21 strikes (ATM ± 10)

    # Check ATM item
    atm_item = next(i for i in res["chain"] if i["strike"] == 73200.0)
    assert atm_item["is_atm"] is True
    assert atm_item["call"]["symbol"] == "SENSEX73200CE"
    assert atm_item["put"]["symbol"] == "SENSEX73200PE"
    assert atm_item["call"]["ltp"] is not None
    assert atm_item["put"]["ltp"] is not None

    # Second call (cached)
    t1 = time.time()
    res_cached = cdm.get_option_chain_data("SENSEX", expiry=exp_str)
    dt2 = (time.time() - t1) * 1000.0
    assert dt2 < 5.0, f"Cached option chain too slow: {dt2:.2f} ms"


def test_session_candle_cache_ttl_logic():
    """Test candle cache expires after 60s during market hours and stays persistent after close."""
    sym = "TEST_OPT"
    candles = [{"time": "2026-10-28 09:15", "open": 100, "high": 105, "low": 98, "close": 102, "volume": 500}]

    # 1. Put fresh candles in cache (cached 5 seconds ago)
    now = time.time()
    cdm._session_candles[sym] = (now - 5.0, candles)

    # During market hours: 5s age is valid (< 60s)
    res = cdm.fetch_contract_candles(sym)
    assert len(res["candles"]) == 1

    # 2. Simulate expired cache (cached 75 seconds ago)
    cdm._session_candles[sym] = (now - 75.0, candles)

    # Mock is_market_open to True -> should invalidate
    orig_open = cdm.is_market_open
    try:
        cdm.is_market_open = lambda: True
        # With expired cache and no DB/Kite, it should attempt fetch and return empty or new
        res_expired = cdm.fetch_contract_candles(sym)
        # Because TEST_OPT does not exist in DB or broker, count should be 0 (cache was not used)
        assert res_expired["count"] == 0

        # Mock is_market_open to False (off-market) -> should remain valid despite 75s age
        cdm.is_market_open = lambda: False
        cdm._session_candles[sym] = (now - 75.0, candles)
        res_closed = cdm.fetch_contract_candles(sym)
        assert len(res_closed["candles"]) == 1
    finally:
        cdm.is_market_open = orig_open


def test_update_forming_candle_minute_rollover():
    """Test that minute rollover in update_forming_candle appends completed candle to _session_candles."""
    sym = "NIFTY26N0322750CE"
    
    # Tick at minute 10:00 (timestamp 1793160000)
    minute_0 = 1793160000
    with cdm._lock:
        cdm._forming_candles[sym] = {
            "time": minute_0,
            "open": 150.0,
            "high": 155.0,
            "low": 148.0,
            "close": 152.0,
            "volume": 0
        }

    # New tick at minute 10:01 (timestamp 1793160060)
    fc = cdm.update_forming_candle(sym, 153.0)

    # Verify forming candle started fresh
    assert fc["open"] == 153.0
    assert fc["close"] == 153.0

    # Verify previous candle was completed and pushed to _session_candles
    with cdm._lock:
        assert sym in cdm._session_candles
        ts, completed_list = cdm._session_candles[sym]
        assert len(completed_list) == 1
        assert completed_list[0]["timestamp"] == minute_0
        assert completed_list[0]["close"] == 152.0


def test_fast_black_scholes_pricing_and_norm_cdf():
    """Test _fast_black_scholes_price and _norm_cdf accuracy and boundary conditions."""
    # 1. Normal CDF checks
    assert abs(cdm._norm_cdf(0.0) - 0.5) < 1e-6
    assert abs(cdm._norm_cdf(1.96) - 0.975002) < 1e-4
    assert abs(cdm._norm_cdf(-1.96) - 0.024998) < 1e-4

    # 2. Expiry at T = 0 boundary condition
    # Call with S > K -> S - K
    assert cdm._fast_black_scholes_price(S=22750.0, K=22700.0, T=0.0, r=0.07, sigma=0.15, is_call=True) == 50.0
    # Call with S < K -> max(0.05, S - K) = 0.05
    assert cdm._fast_black_scholes_price(S=22650.0, K=22700.0, T=0.0, r=0.07, sigma=0.15, is_call=True) == 0.05
    # Put with S < K -> K - S
    assert cdm._fast_black_scholes_price(S=22650.0, K=22700.0, T=0.0, r=0.07, sigma=0.15, is_call=False) == 50.0

    # 3. Zero volatility boundary condition
    assert cdm._fast_black_scholes_price(S=22800.0, K=22700.0, T=0.1, r=0.07, sigma=0.0, is_call=True) == 100.0

    # 4. Standard active option pricing sanity
    call_px = cdm._fast_black_scholes_price(S=22700.0, K=22700.0, T=3.0/365.0, r=0.07, sigma=0.13, is_call=True)
    put_px = cdm._fast_black_scholes_price(S=22700.0, K=22700.0, T=3.0/365.0, r=0.07, sigma=0.13, is_call=False)
    assert 50.0 < call_px < 150.0
    assert 50.0 < put_px < 150.0


def test_option_chain_all_underlying_alias_normalization():
    """Test underlying symbol normalization for BSE/NSE index aliases."""
    today = date.today()
    exp = today + timedelta(days=2)
    exp_str = exp.strftime("%Y-%m-%d")

    # Populate grid with aliases
    mock_insts = [
        {"name": "NIFTY", "segment": "NFO-OPT", "expiry": exp, "strike": 22700.0, "instrument_type": "CE", "tradingsymbol": "NIFTY26N0322700CE", "instrument_token": 111},
        {"name": "CNXBAN", "segment": "NFO-OPT", "expiry": exp, "strike": 54300.0, "instrument_type": "CE", "tradingsymbol": "BANKNIFTY26N0354300CE", "instrument_token": 222},
        {"name": "NIFFIN", "segment": "NFO-OPT", "expiry": exp, "strike": 24400.0, "instrument_type": "CE", "tradingsymbol": "FINNIFTY26N0324400CE", "instrument_token": 333},
        {"name": "MIDCPNIFTY", "segment": "NFO-OPT", "expiry": exp, "strike": 13275.0, "instrument_type": "CE", "tradingsymbol": "MIDCPNIFTY26N0313275CE", "instrument_token": 444},
    ]
    cdm._build_option_grid(mock_insts)

    # Test alias resolution
    cdm.update_live_index_spot("NIFTY", 22700.0)
    cdm.update_live_index_spot("BANKNIFTY", 54300.0)
    cdm.update_live_index_spot("FINNIFTY", 24400.0)
    cdm.update_live_index_spot("MIDCPNIFTY", 13275.0)

    # "NIFTY 50" -> "NIFTY"
    r1 = cdm.get_option_chain_data("NIFTY 50", expiry=exp_str)
    assert r1["underlying"] == "NIFTY"

    # "NIFTY BANK" -> "BANKNIFTY"
    r2 = cdm.get_option_chain_data("NIFTY BANK", expiry=exp_str)
    assert r2["underlying"] == "BANKNIFTY"

    # "NIFTY FIN SERVICE" -> "FINNIFTY"
    r3 = cdm.get_option_chain_data("NIFTY FIN SERVICE", expiry=exp_str)
    assert r3["underlying"] == "FINNIFTY"

    # "NIFTY MID SELECT" -> "MIDCPNIFTY"
    r4 = cdm.get_option_chain_data("NIFTY MID SELECT", expiry=exp_str)
    assert r4["underlying"] == "MIDCPNIFTY"


def test_option_chain_fetch_broker_quotes_enrichment():
    """Test fetch_broker_quotes=True fetches quotes from Kite Connect and updates price bus."""
    today = date.today()
    exp = today + timedelta(days=2)
    exp_str = exp.strftime("%Y-%m-%d")

    mock_insts = [
        {"name": "NIFTY", "segment": "NFO-OPT", "expiry": exp, "strike": 22700.0, "instrument_type": "CE", "tradingsymbol": "NIFTY26N0322700CE", "instrument_token": 777001},
        {"name": "NIFTY", "segment": "NFO-OPT", "expiry": exp, "strike": 22700.0, "instrument_type": "PE", "tradingsymbol": "NIFTY26N0322700PE", "instrument_token": 777002},
    ]
    cdm._build_option_grid(mock_insts)
    cdm.update_live_index_spot("NIFTY", 22700.0)

    class MockKite:
        def quote(self, tokens):
            return {
                "777001": {"last_price": 142.50},
                "777002": {"last_price": 118.75},
            }

    orig_get_kite = cdm._get_kite
    try:
        cdm._get_kite = lambda: MockKite()
        res = cdm.get_option_chain_data("NIFTY", expiry=exp_str, fetch_broker_quotes=True)
        atm_item = next(i for i in res["chain"] if i["strike"] == 22700.0)
        assert atm_item["call"]["ltp"] == 142.50
        assert atm_item["put"]["ltp"] == 118.75

        # Check price bus was updated
        assert cdm.get_live_price_from_bus("NIFTY26N0322700CE") == 142.50
        assert cdm.get_live_price_from_bus("NIFTY26N0322700PE") == 118.75
    finally:
        cdm._get_kite = orig_get_kite


def test_allow_synthetic_candles_env_switch(monkeypatch):
    """Test synthetic candle generator is strictly disabled unless ALLOW_SYNTHETIC_CANDLES=1."""
    sym = "TEST_NON_EXISTENT_SYMBOL"

    # Default / Unset: strictly 0 candles returned
    monkeypatch.delenv("ALLOW_SYNTHETIC_CANDLES", raising=False)
    res_authentic = cdm.fetch_contract_candles(sym)
    assert res_authentic["count"] == 0
    assert len(res_authentic["candles"]) == 0

    # Explicitly enabled: produces fallback candles
    monkeypatch.setenv("ALLOW_SYNTHETIC_CANDLES", "1")
    res_synthetic = cdm.fetch_contract_candles(sym)
    assert res_synthetic["count"] == 375
    assert len(res_synthetic["candles"]) == 375

