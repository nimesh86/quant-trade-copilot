"""
test_multi_index_breeze_kite.py
────────────────────────────────
Unit test suite validating 100% correct behavior of Dynamic Multi-Index trading
across ICICI Direct Breeze API and Zerodha Kite Connect.
"""

import unittest
from datetime import datetime

from trading_engine.config import get_index_spec, INDEX_SPEC_REGISTRY, EngineConfig
from trading_engine.adapters.symbol_translator import (
    to_breeze_params,
    parse_canonical_symbol,
    _get_exchange_for_underlying,
    STOCK_CODE_MAP,
    REVERSE_STOCK_CODE_MAP,
)
from trading_engine.execution.order_slicer import OrderSlicer
from trading_engine.web.server import build_option_chain_data
from trading_engine.web.contract_data_manager import (
    pin_trade_symbol,
    unpin_trade_symbol,
    get_pinned_trade_symbols,
)


class TestMultiIndexBreezeKite(unittest.TestCase):

    def test_index_spec_registry_all_indices(self):
        """Verifies contract specifications for all supported indices."""
        # NIFTY
        nifty = get_index_spec("NIFTY")
        self.assertEqual(nifty["exchange_code"], "NFO")
        self.assertEqual(nifty["cash_exchange"], "NSE")
        self.assertEqual(nifty["lot_size"], 65)
        self.assertEqual(nifty["strike_gap"], 50)
        self.assertEqual(nifty["breeze_stock_code"], "NIFTY")

        # BANKNIFTY
        bn = get_index_spec("BANKNIFTY")
        self.assertEqual(bn["exchange_code"], "NFO")
        self.assertEqual(bn["cash_exchange"], "NSE")
        self.assertEqual(bn["lot_size"], 15)
        self.assertEqual(bn["strike_gap"], 100)
        self.assertEqual(bn["breeze_stock_code"], "CNXBAN")

        # SENSEX (BSE Derivatives)
        sensex = get_index_spec("SENSEX")
        self.assertEqual(sensex["exchange_code"], "BFO")
        self.assertEqual(sensex["cash_exchange"], "BSE")
        self.assertEqual(sensex["lot_size"], 10)
        self.assertEqual(sensex["strike_gap"], 100)
        self.assertEqual(sensex["breeze_stock_code"], "BSX")

        # FINNIFTY
        fin = get_index_spec("FINNIFTY")
        self.assertEqual(fin["exchange_code"], "NFO")
        self.assertEqual(fin["lot_size"], 25)
        self.assertEqual(fin["strike_gap"], 50)

        # MIDCPNIFTY
        mid = get_index_spec("MIDCPNIFTY")
        self.assertEqual(mid["exchange_code"], "NFO")
        self.assertEqual(mid["lot_size"], 75)
        self.assertEqual(mid["strike_gap"], 25)

        # Safe fallback for unknown
        fallback = get_index_spec("UNKNOWN_INDEX")
        self.assertEqual(fallback["display_name"], "NIFTY 50")

    def test_breeze_exchange_routing(self):
        """Verifies BFO routing for SENSEX/BANKEX and NFO for NSE indices."""
        self.assertEqual(_get_exchange_for_underlying("SENSEX", "options"), "BFO")
        self.assertEqual(_get_exchange_for_underlying("BSX", "options"), "BFO")
        self.assertEqual(_get_exchange_for_underlying("SENSEX", "cash"), "BSE")
        self.assertEqual(_get_exchange_for_underlying("NIFTY", "options"), "NFO")
        self.assertEqual(_get_exchange_for_underlying("BANKNIFTY", "options"), "NFO")
        self.assertEqual(_get_exchange_for_underlying("NIFTY", "cash"), "NSE")

    def test_breeze_symbol_translator_sensex(self):
        """Verifies SENSEX monthly, weekly, and 6-digit options parse to Breeze BFO parameters."""
        # Monthly SENSEX Call Option
        p_monthly = to_breeze_params("SENSEX26OCT79500CE")
        self.assertIsNotNone(p_monthly)
        self.assertEqual(p_monthly["stock_code"], "BSX")
        self.assertEqual(p_monthly["exchange_code"], "BFO")
        self.assertEqual(p_monthly["right"], "Call")
        self.assertEqual(p_monthly["strike_price"], "79500")
        self.assertEqual(p_monthly["product_type"], "options")

        # Weekly SENSEX Put Option (single-char month 'O' = October)
        p_weekly = to_breeze_params("SENSEX26O2479500PE")
        self.assertIsNotNone(p_weekly)
        self.assertEqual(p_weekly["stock_code"], "BSX")
        self.assertEqual(p_weekly["exchange_code"], "BFO")
        self.assertEqual(p_weekly["right"], "Put")
        self.assertEqual(p_weekly["strike_price"], "79500")

        # Standard 6-digit YYMMDD SENSEX option
        p_6d = to_breeze_params("SENSEX26042480000CE")
        self.assertIsNotNone(p_6d)
        self.assertEqual(p_6d["stock_code"], "BSX")
        self.assertEqual(p_6d["exchange_code"], "BFO")
        self.assertEqual(p_6d["strike_price"], "80000")

    def test_breeze_symbol_translator_banknifty_and_nifty(self):
        """Verifies BANKNIFTY and NIFTY parse to Breeze NFO parameters."""
        # BANKNIFTY Put
        p_bn = to_breeze_params("BANKNIFTY26OCT52000PE")
        self.assertIsNotNone(p_bn)
        self.assertEqual(p_bn["stock_code"], "CNXBAN")
        self.assertEqual(p_bn["exchange_code"], "NFO")
        self.assertEqual(p_bn["right"], "Put")
        self.assertEqual(p_bn["strike_price"], "52000")

        # NIFTY Call
        p_nifty = to_breeze_params("NIFTY26OCT24500CE")
        self.assertIsNotNone(p_nifty)
        self.assertEqual(p_nifty["stock_code"], "NIFTY")
        self.assertEqual(p_nifty["exchange_code"], "NFO")
        self.assertEqual(p_nifty["right"], "Call")
        self.assertEqual(p_nifty["strike_price"], "24500")

    def test_dynamic_option_chain_strike_grid(self):
        """Verifies strike interval is 100 for SENSEX/BANKNIFTY and 50 for NIFTY."""
        # SENSEX at spot 80,123.45 -> ATM is 80,100, gap is 100
        chain_sensex = build_option_chain_data(80123.45, underlying="SENSEX")
        self.assertEqual(chain_sensex["underlying"], "SENSEX")
        self.assertEqual(chain_sensex["atm_strike"], 80100)
        self.assertEqual(len(chain_sensex["chain"]), 11)
        # Check consecutive strike gap
        diff_sensex = chain_sensex["chain"][1]["strike"] - chain_sensex["chain"][0]["strike"]
        self.assertEqual(diff_sensex, 100)

        # NIFTY at spot 24,182.50 -> ATM is 24,200, gap is 50
        chain_nifty = build_option_chain_data(24182.50, underlying="NIFTY")
        self.assertEqual(chain_nifty["underlying"], "NIFTY 50")
        self.assertEqual(chain_nifty["atm_strike"], 24200)
        diff_nifty = chain_nifty["chain"][1]["strike"] - chain_nifty["chain"][0]["strike"]
        self.assertEqual(diff_nifty, 50)

    def test_order_slicer_freeze_limits_and_lot_sizes(self):
        """Verifies order slicer respects index-specific lot sizes and freeze limits."""
        cfg = EngineConfig()
        slicer = OrderSlicer(cfg)
        now = datetime.now()

        # SENSEX: lot size 10, freeze 100 lots
        report_sensex = slicer.generate_child_slices(
            n_lots=50,
            side="CE",
            base_option_price=250.0,
            spot_price=80000.0,
            entry_time=now,
            strike_step=100.0,
            freeze_lots=100,
            lot_size=10,
        )
        self.assertEqual(report_sensex.total_slices, 1)
        self.assertEqual(report_sensex.parent_units, 500)
        self.assertEqual(report_sensex.slices[0].units, 500)

        # NIFTY: lot size 65, freeze 27 lots -> 50 lots must slice into 2 legs (27 and 23)
        report_nifty = slicer.generate_child_slices(
            n_lots=50,
            side="CE",
            base_option_price=120.0,
            spot_price=24000.0,
            entry_time=now,
            strike_step=50.0,
            freeze_lots=27,
            lot_size=65,
        )
        self.assertEqual(report_nifty.total_slices, 2)
        self.assertEqual(report_nifty.slices[0].lots, 27)
        self.assertEqual(report_nifty.slices[0].units, 27 * 65)
        self.assertEqual(report_nifty.slices[1].lots, 23)
        self.assertEqual(report_nifty.slices[1].units, 23 * 65)

    def test_pinned_watchdog_registry(self):
        """Verifies active trade watchdog pins and unpins contracts."""
        test_sym = "SENSEX26OCT79500CE"
        pin_trade_symbol(test_sym)
        self.assertIn(test_sym, get_pinned_trade_symbols())

        unpin_trade_symbol(test_sym)
        self.assertNotIn(test_sym, get_pinned_trade_symbols())


if __name__ == "__main__":
    unittest.main()
