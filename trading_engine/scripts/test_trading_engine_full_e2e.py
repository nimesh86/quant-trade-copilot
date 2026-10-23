#!/usr/bin/env python3
"""
Comprehensive End-to-End Test Suite for Contract Trading Engine (Backend + Frontend Asset Integration)
─────────────────────────────────────────────────────────────────────────────────────────────────────────
Verifies:
1. Account state, capital balance, and mode switcher
2. Single-click Market Buy execution & lot units sizing
3. Real-time tick ingestion & mathematical unrealized PnL calculation (pts * units)
4. Dynamic Trailing Stop Loss ratcheting & Break-Even lock at Target 1
5. Interactive level modification (SL and Target drag on chart)
6. 1-click Market Exit & statutory charge deduction (Brokerage + STT + GST)
7. Limit order placement, pending queue, and tick-triggered auto-fill
8. Emergency Kill Switch instant flatten, order cancellation, and session lockout
9. Frontend HTML, CSS, and JS contract interface integrity & endpoint bindings
"""

import sys
import os
import time
import requests
import unittest

BASE_URL = os.environ.get("TEST_BASE_URL", "http://localhost:8080")
TEST_SYMBOL = "NIFTY26OCT22800PE"

class TestTradingEngineE2E(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        print(f"\n{'='*75}")
        print(f"🚀 RUNNING TRADING ENGINE E2E SUITE against {BASE_URL}")
        print(f"{'='*75}")
        # Reset account to clean state
        res = requests.post(f"{BASE_URL}/api/trade/reset", json={"capital": 100000.0})
        assert res.status_code == 200, f"Reset failed: {res.text}"

    def setUp(self):
        # Ensure kill switch is unlocked before each test
        requests.post(f"{BASE_URL}/api/trade/kill_switch/unlock")

    def test_01_initial_account_state(self):
        """Verify initial capital, zero PnL, paper mode, and kill switch status."""
        res = requests.get(f"{BASE_URL}/api/trade/state")
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data["mode"], "paper")
        self.assertAlmostEqual(data["capital"], 100000.0, places=1)
        self.assertEqual(data["realized_day_pnl"], 0.0)
        self.assertEqual(data["unrealized_pnl"], 0.0)
        self.assertEqual(data["total_day_pnl"], 0.0)
        self.assertIsNone(data["active_position"])
        self.assertFalse(data["is_kill_switch_active"])
        print("  [✓] Test 01 Passed: Initial account state is clean and accurate.")

    def test_02_market_buy_execution_and_lot_sizing(self):
        """Place 1-click market buy order and verify lot size calculation (NIFTY = 65 units)."""
        payload = {
            "symbol": TEST_SYMBOL,
            "side": "BUY",
            "order_type": "MARKET",
            "lots": 2,
            "current_market_price": 140.0,
            "sl": 125.0,
            "target1": 155.0,
            "target2": 170.0,
            "trailing_mode": "be_lock"
        }
        res = requests.post(f"{BASE_URL}/api/trade/order", json=payload)
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data["status"], "success")
        self.assertEqual(data["action"], "ORDER_FILLED")
        
        pos = data["position"]
        self.assertEqual(pos["symbol"], TEST_SYMBOL)
        self.assertEqual(pos["lots"], 2)
        self.assertEqual(pos["units"], 130)  # 2 lots * 65 units = 130
        self.assertAlmostEqual(pos["entry_price"], 140.05, places=2)  # 140.00 + 0.05 NSE slippage
        self.assertEqual(pos["sl"], 125.0)
        self.assertEqual(pos["target1"], 155.0)
        self.assertFalse(pos["be_locked"])
        print("  [✓] Test 02 Passed: Market Buy executed with exact lot sizing & 0.05 tick slippage.")

    def test_03_live_tick_pnl_calculation(self):
        """Feed price ticks and verify mathematical unrealized PnL calculation."""
        # 1. Price moves up +5 points (140.05 -> 145.05)
        # 130 units * 5.0 pts = +650.00 gross PnL
        res = requests.post(f"{BASE_URL}/api/trade/tick", json={"symbol": TEST_SYMBOL, "price": 145.05})
        self.assertEqual(res.status_code, 200)
        
        state = requests.get(f"{BASE_URL}/api/trade/state").json()
        pos = state["active_position"]
        self.assertIsNotNone(pos)
        self.assertAlmostEqual(pos["current_price"], 145.05, places=2)
        self.assertAlmostEqual(pos["unrealized_pts"], 5.0, places=1)
        self.assertAlmostEqual(pos["unrealized_pnl"], 650.0, places=1)
        self.assertAlmostEqual(state["total_day_pnl"], 650.0, places=1)

        # 2. Price dips -2 points below entry (140.05 -> 138.05)
        # 130 units * -2.0 pts = -260.00 gross loss
        requests.post(f"{BASE_URL}/api/trade/tick", json={"symbol": TEST_SYMBOL, "price": 138.05})
        state = requests.get(f"{BASE_URL}/api/trade/state").json()
        pos = state["active_position"]
        self.assertAlmostEqual(pos["unrealized_pts"], -2.0, places=1)
        self.assertAlmostEqual(pos["unrealized_pnl"], -260.0, places=1)
        self.assertAlmostEqual(state["total_day_pnl"], -260.0, places=1)
        print("  [✓] Test 03 Passed: Live tick unrealized PnL computed with precision.")

    def test_04_trailing_stop_breakeven_lock(self):
        """Feed tick reaching Target 1 (155.0) and verify Break-Even Stop Loss lock."""
        # Current entry = 140.05, Target 1 = 155.00
        # Push tick to 156.00 (Target 1 breached)
        requests.post(f"{BASE_URL}/api/trade/tick", json={"symbol": TEST_SYMBOL, "price": 156.00})
        state = requests.get(f"{BASE_URL}/api/trade/state").json()
        pos = state["active_position"]
        self.assertTrue(pos["be_locked"], "Position should have Break-Even locked at T1!")
        self.assertGreaterEqual(pos["current_sl"], pos["entry_price"])
        print(f"  [✓] Test 04 Passed: Break-Even locked at T1 (SL ratcheted to ₹{pos['current_sl']}).")

    def test_05_position_level_modification_from_chart_drag(self):
        """Simulate dragging SL and Target lines on the chart and calling /api/trade/modify."""
        payload = {
            "symbol": TEST_SYMBOL,
            "sl": 142.50,
            "target1": 165.00
        }
        res = requests.post(f"{BASE_URL}/api/trade/modify", json=payload)
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data["status"], "success")
        
        pos = data["position"]
        self.assertEqual(pos["current_sl"], 142.50)
        self.assertEqual(pos["target1"], 165.00)
        print("  [✓] Test 05 Passed: On-chart drag level modifications updated dynamically.")

    def test_06_market_exit_and_statutory_charges(self):
        """Exit position at market and verify realized PnL accounting minus taxes."""
        state_before = requests.get(f"{BASE_URL}/api/trade/state").json()
        cap_before = state_before["capital"]
        
        res = requests.post(f"{BASE_URL}/api/trade/exit", json={
            "symbol": TEST_SYMBOL,
            "exit_price": 150.05,
            "reason": "TEST_PROFIT_EXIT"
        })
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data["status"], "success")
        
        rec = data["record"]
        # Gross profit = (150.05 - 140.05) * 130 = +1,300.00
        self.assertAlmostEqual(rec["gross_pnl"], 1300.00, places=1)
        # Charges = brokerage (40.00) + STT + GST ~ ₹63-65
        self.assertGreater(rec["charges"], 40.0)
        # Net profit = gross - charges
        expected_net = round(rec["gross_pnl"] - rec["charges"], 2)
        self.assertAlmostEqual(rec["net_pnl"], expected_net, places=2)
        
        # Verify account capital credited
        state_after = requests.get(f"{BASE_URL}/api/trade/state").json()
        self.assertIsNone(state_after["active_position"])
        self.assertAlmostEqual(state_after["capital"], cap_before + rec["net_pnl"], places=2)
        self.assertAlmostEqual(state_after["realized_day_pnl"], rec["net_pnl"], places=2)
        print(f"  [✓] Test 06 Passed: Position closed with Net Profit ₹{rec['net_pnl']:+,.2f} after statutory taxes.")

    def test_07_limit_order_placement_and_fill(self):
        """Place passive Limit order, verify pending queue, and test tick fill."""
        payload = {
            "symbol": TEST_SYMBOL,
            "side": "BUY",
            "order_type": "LIMIT",
            "lots": 1,
            "limit_price": 135.00,
            "current_market_price": 140.00,
            "sl": 125.00,
            "target1": 150.00
        }
        res = requests.post(f"{BASE_URL}/api/trade/order", json=payload)
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data["action"], "LIMIT_ORDER_PLACED")
        order_id = data["order"]["order_id"]
        
        # Verify in state pending_orders
        state = requests.get(f"{BASE_URL}/api/trade/state").json()
        self.assertEqual(len(state["pending_orders"]), 1)
        self.assertEqual(state["pending_orders"][0]["order_id"], order_id)
        
        # Tick above limit price (136.00) -> remains pending
        requests.post(f"{BASE_URL}/api/trade/tick", json={"symbol": TEST_SYMBOL, "price": 136.00})
        state = requests.get(f"{BASE_URL}/api/trade/state").json()
        self.assertEqual(len(state["pending_orders"]), 1)
        self.assertIsNone(state["active_position"])

        # Tick at limit price (135.00) -> fills order!
        res_fill = requests.post(f"{BASE_URL}/api/trade/tick", json={"symbol": TEST_SYMBOL, "price": 135.00}).json()
        self.assertEqual(res_fill["event"]["action"], "ORDER_FILLED")
        
        state = requests.get(f"{BASE_URL}/api/trade/state").json()
        self.assertEqual(len(state["pending_orders"]), 0)
        self.assertIsNotNone(state["active_position"])
        self.assertEqual(state["active_position"]["entry_price"], 135.00)
        
        # Clean up
        requests.post(f"{BASE_URL}/api/trade/exit", json={"symbol": TEST_SYMBOL, "exit_price": 135.00})
        print("  [✓] Test 07 Passed: Limit order queued and filled on pullback tick.")

    def test_08_kill_switch_lifecycle(self):
        """Test emergency kill switch: flattens position, cancels orders, locks engine, then unlocks."""
        # Open a position first
        requests.post(f"{BASE_URL}/api/trade/order", json={
            "symbol": TEST_SYMBOL, "side": "BUY", "order_type": "MARKET", "lots": 1, "current_market_price": 140.0
        })
        # Queue a pending limit order
        requests.post(f"{BASE_URL}/api/trade/order", json={
            "symbol": TEST_SYMBOL, "side": "BUY", "order_type": "LIMIT", "lots": 1, "limit_price": 120.0, "current_market_price": 140.0
        })
        
        state = requests.get(f"{BASE_URL}/api/trade/state").json()
        self.assertIsNotNone(state["active_position"])
        self.assertEqual(len(state["pending_orders"]), 1)
        
        # Trigger Kill Switch!
        res_ks = requests.post(f"{BASE_URL}/api/trade/kill_switch")
        self.assertEqual(res_ks.status_code, 200)
        
        state = requests.get(f"{BASE_URL}/api/trade/state").json()
        self.assertTrue(state["is_kill_switch_active"])
        self.assertIsNone(state["active_position"], "Position should be flattened!")
        self.assertEqual(len(state["pending_orders"]), 0, "Pending orders should be cancelled!")

        # Attempt order while locked -> should be rejected with 400/403
        res_blocked = requests.post(f"{BASE_URL}/api/trade/order", json={
            "symbol": TEST_SYMBOL, "side": "BUY", "order_type": "MARKET", "lots": 1, "current_market_price": 140.0
        })
        self.assertIn(res_blocked.status_code, (400, 403))
        self.assertIn("kill switch", res_blocked.json()["detail"].lower())

        # Unlock Kill Switch
        res_unlock = requests.post(f"{BASE_URL}/api/trade/kill_switch/unlock")
        self.assertEqual(res_unlock.status_code, 200)
        state = requests.get(f"{BASE_URL}/api/trade/state").json()
        self.assertFalse(state["is_kill_switch_active"])
        print("  [✓] Test 08 Passed: Kill Switch flattens positions, cancels orders, and enforces lockout.")

    def test_09_frontend_assets_integrity(self):
        """Verify contract.html, contract.css, contract.js exist and have valid trading bindings."""
        # 1. HTML check
        res_html = requests.get(f"{BASE_URL}/contract.html")
        self.assertEqual(res_html.status_code, 200)
        html = res_html.text
        self.assertIn("btn-quick-buy-mkt", html, "Market Buy button ID missing in HTML!")
        self.assertIn("btn-draw-limit", html, "Limit tool button ID missing in HTML!")
        self.assertIn("btn-quick-exit", html, "Market Exit button ID missing in HTML!")
        self.assertIn("val-day-pnl", html, "Day PnL badge missing in HTML!")
        self.assertIn("btn-kill-switch", html, "Kill switch button missing in HTML!")
        self.assertIn("active-pos-hud", html, "Active position HUD (POS, SL, T1, T2) missing in HTML!")
        self.assertNotIn("trade-dock", html, "Obsolete floating trade-dock should not be present in HTML!")

        # 2. JS check
        res_js = requests.get(f"{BASE_URL}/static/contract.js")
        self.assertEqual(res_js.status_code, 200)
        js = res_js.text
        self.assertIn("updateMarketBuyButton", js)
        self.assertIn("renderTradingCanvasOverlay", js)
        self.assertIn("executeTradingViewLimitOrder", js)
        self.assertIn("initTradingCanvasMouseHandlers", js)
        self.assertIn("POS T2", js, "POS T2 rendering missing in contract.js!")
        self.assertIn("evaluateReplayCandleTrading", js, "Replay live PnL and trade evaluator missing in contract.js!")
        self.assertIn("/api/trade/order", js)
        self.assertIn("/api/trade/exit", js)
        self.assertIn("/api/trade/modify", js)
        
        # 3. CSS check
        res_css = requests.get(f"{BASE_URL}/static/contract.css")
        self.assertEqual(res_css.status_code, 200)
        css = res_css.text
        self.assertIn(".btn-clean-buy", css)
        self.assertIn(".btn-clean-limit", css)
        self.assertIn(".btn-clean-exit", css)
        self.assertIn(".active-pos-hud", css)
        print("  [✓] Test 09 Passed: Frontend HTML, CSS, and JS assets verified clean and bound.")

if __name__ == "__main__":
    unittest.main()
