import unittest
import numpy as np
from trading_engine.scripts.smf_dynamic_exit_engine import is_srr_setup

class TestSupportReversalReclaim(unittest.TestCase):
    def test_srr_detection_on_support_reversal(self):
        # 30 bars mock
        n = 35
        # Morning base low at bar 10 with low = 111.0
        lows = np.full(n, 135.0)
        lows[10] = 111.0
        
        highs = np.full(n, 145.0)
        highs[15] = 165.0 # Prior resistance ceiling at 165.0
        
        opens = np.full(n, 133.0)
        closes = np.full(n, 137.0)
        
        # At bar 27: dip in last 4 bars touched 122.0 (support zone <= 111 + 1.2 * 10 = 123.0)
        lows[25] = 122.0
        lows[26] = 122.0
        highs[25] = 132.0
        highs[26] = 132.5
        
        # Bar 27 reversal candle:
        opens[27] = 131.0
        closes[27] = 137.0
        highs[27] = 137.5
        lows[27] = 130.5
        
        cdata = {
            "lows": lows,
            "highs": highs,
            "opens": opens,
            "closes": closes,
            "ind": {
                "atr": np.full(n, 10.0),
                "rsi": np.full(n, 48.0),
                "rvol": np.full(n, 1.2)
            }
        }
        
        is_srr, dip_val = is_srr_setup(
            b=27,
            c_open=opens[27],
            c_high=highs[27],
            c_low=lows[27],
            c_close=closes[27],
            c_time="10:07",
            cdata=cdata
        )
        self.assertTrue(is_srr, "SRR should trigger when price reverses from base support with wide runway")
        self.assertAlmostEqual(dip_val, 122.0, delta=0.1)

    def test_srr_rejected_without_runway(self):
        # Resistance ceiling is too close (only 10 pts away, runway < 22 pts)
        n = 35
        lows = np.full(n, 135.0)
        lows[10] = 111.0
        lows[25] = 122.0
        
        highs = np.full(n, 140.0) # Low ceiling -> runway 140 - 137 = 3 pts
        opens = np.full(n, 133.0)
        closes = np.full(n, 137.0)
        
        cdata = {
            "lows": lows,
            "highs": highs,
            "opens": opens,
            "closes": closes,
            "ind": {
                "atr": np.full(n, 10.0),
                "rsi": np.full(n, 48.0),
                "rvol": np.full(n, 1.2)
            }
        }
        
        is_srr, _ = is_srr_setup(
            b=27,
            c_open=131.0,
            c_high=137.5,
            c_low=130.5,
            c_close=137.0,
            c_time="10:07",
            cdata=cdata
        )
        self.assertFalse(is_srr, "SRR must reject entry if runway to overhead resistance is insufficient")

if __name__ == '__main__':
    unittest.main()
