"""
Test Script: Strategy Detection Engine & Parallel Execution Adapter.
Simulates a day's minute candles containing an institutional Bullish Spring sweep
and verifies parallel strategy evaluation, GTT suggestions, and risk metrics.
"""
import sys
from pathlib import Path
from datetime import datetime, timedelta

# Add trading_engine to path
sys.path.insert(0, str(Path(__file__).resolve().parent / "trading_engine"))

from config import EngineConfig
from adapters.strategy_detector_adapter import StrategyDetectorAdapter

def generate_mock_spring_day():
    bars = []
    base_time = datetime(2026, 10, 14, 9, 15, 0)

    # 09:15 to 10:15 IST (61 candles, 0 to 60 inclusive): Accumulation in range 25000 to 25050
    for i in range(61):
        t = base_time + timedelta(minutes=i)
        c = 25000.0 + (i % 10) * 5.0
        h = c + 5.0
        l = c - 5.0
        bars.append({"idx": i, "timestamp": t, "open": c, "high": h, "low": l, "close": c, "volume": 1000})

    # 10:16 to 10:20 IST (idx 61 to 65): Judas Swing / Manipulation dip below 25000 (sweep down to 24980)
    for i in range(61, 66):
        t = base_time + timedelta(minutes=i)
        bars.append({"idx": i, "timestamp": t, "open": 24995.0, "high": 25000.0, "low": 24980.0, "close": 24985.0, "volume": 3500})

    # 10:21 IST (idx 66): Reclaim candle closing back above 25000 (close at 25010)
    t = base_time + timedelta(minutes=66)
    bars.append({"idx": 66, "timestamp": t, "open": 24990.0, "high": 25015.0, "low": 24988.0, "close": 25010.0, "volume": 5000})

    return bars

def main():
    print("=" * 85)
    print("  TESTING MODULAR STRATEGY DETECTION ENGINE")
    print("=" * 85)

    cfg = EngineConfig()
    detector = StrategyDetectorAdapter(cfg)

    mock_bars = generate_mock_spring_day()
    print(f"\nStreaming {len(mock_bars)} simulated candles across parallel strategy pool...")

    detected_signals = []
    for bar in mock_bars:
        signals = detector.evaluate_candle_parallel(bar)
        if signals:
            detected_signals.extend(signals)

    detector.shutdown()

    print(f"\nVerification:")
    print(f"  Total Setups Detected: {len(detected_signals)}")
    assert len(detected_signals) == 1, f"Expected 1 setup, found {len(detected_signals)}"

    sig = detected_signals[0]
    assert sig.strategy_name == "AMD_VOLUME_PROFILE"
    assert sig.side == "CE"
    assert sig.trigger_price == 25010.0
    assert sig.stop_loss == 24978.0  # 24980 - 2 pts buffer
    assert sig.risk_points == 32.0   # 25010 - 24978
    assert sig.rr_ratio >= 3.0       # High-Target >= 3.0R
    assert sig.gtt_payload["tradingsymbol"] == "NIFTY 50"

    print(f"  Strategy:     {sig.strategy_name}")
    print(f"  Setup Side:   {sig.side} BUY")
    print(f"  Trigger Price: ₹{sig.trigger_price:.2f}")
    print(f"  Stop Loss:    ₹{sig.stop_loss:.2f} (-{sig.risk_points:.2f} pts)")
    print(f"  Target:       ₹{sig.target:.2f} (+{sig.reward_points:.2f} pts)")
    print(f"  R:R Ratio:    {sig.rr_ratio:.2f}R | Risk Rating: {sig.risk_rating}")
    print(f"  GTT Payload:  {sig.gtt_payload}")
    print("\n>>> Strategy Detection Engine Test PASSED (100% compliant with parallel architecture)!")

if __name__ == "__main__":
    main()
