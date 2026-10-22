#!/usr/bin/env python3
"""
Test script for Kronos Foundation Model on NIFTY Candles.
Fetches real 1-minute market candles from TimescaleDB and forecasts next 10 candles.
"""
import sys
import os
import time
import pandas as pd
from sqlalchemy import create_engine

# Ensure trading_engine is in sys.path
BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from trading_engine.ai.kronos_service import KronosEngine

DB_URL = "postgresql+psycopg2://postgres:postgres@localhost:5432/trading"

def main():
    print("=" * 75)
    print("⚡ TESTING KRONOS FOUNDATION MODEL ON NIFTY CANDLES")
    print("=" * 75)

    # 1. Fetch real NIFTY 1-minute candles from DB
    engine = create_engine(DB_URL)
    query = """
        SELECT timestamp, open, high, low, close, COALESCE(volume, 1) as volume
        FROM minute_candles
        WHERE symbol = 'NIFTY 50'
        ORDER BY timestamp DESC
        LIMIT 60;
    """
    df = pd.read_sql(query, engine)
    if df.empty or len(df) < 15:
        print("Warning: Insufficient NIFTY 50 data, trying option_minute_candles...")
        query_alt = """
            SELECT timestamp, open, high, low, close, 100 as volume
            FROM option_minute_candles
            ORDER BY timestamp DESC
            LIMIT 60;
        """
        df = pd.read_sql(query_alt, engine)

    # Sort chronologically
    df = df.sort_values("timestamp").reset_index(drop=True)
    print(f"✅ Loaded {len(df)} historical candles.")
    print(f"Time Range: {df['timestamp'].iloc[0]} -> {df['timestamp'].iloc[-1]}")
    print(f"Latest Close: ₹{df['close'].iloc[-1]:.2f}")
    print("-" * 75)

    # Convert to candle dicts
    candles = []
    for _, row in df.iterrows():
        candles.append({
            "time": row["timestamp"],
            "open": float(row["open"]),
            "high": float(row["high"]),
            "low": float(row["low"]),
            "close": float(row["close"]),
            "volume": float(row["volume"]),
        })

    # 2. Initialize Kronos Engine
    kronos = KronosEngine.get_instance()
    t_load = time.time()
    print("Loading Kronos Foundation Model (NeoQuasar/Kronos-mini)...")
    success = kronos.load_model()
    if not success:
        print("❌ Failed to load Kronos model.")
        return

    print(f"✅ Model loaded in {time.time() - t_load:.2f}s on device: {kronos.predictor.device}")
    print("-" * 75)

    # 3. Run Trajectory Prediction
    t_pred = time.time()
    print("Running autoregressive prediction for next 10 candles (10 minutes forward)...")
    res = kronos.predict_trajectory(candles, pred_len=10)
    latency_ms = (time.time() - t_pred) * 1000

    if not res.get("success"):
        print(f"❌ Prediction error: {res.get('error')}")
        return

    print(f"⚡ Inference finished in {latency_ms:.1f}ms!")
    print("-" * 75)
    print("📈 KRONOS FORECAST RESULTS:")
    print(f"• Predicted Trend Direction: {res['direction']}")
    print(f"• Expected Return:          {res['expected_return_pct']:+.2f}%")
    print(f"• Expected Volatility:      {res['expected_volatility_pct']:.2f}%")
    print(f"• Forecasted High Range:    ₹{res['max_high']:.2f}")
    print(f"• Forecasted Low Range:     ₹{res['min_low']:.2f}")
    print("\nNext 10 Predicted Candles:")
    for i, c in enumerate(res["forecast_candles"], 1):
        dt = pd.to_datetime(c['time'], unit='s')
        print(f"  [{i:02d}] {dt.strftime('%H:%M')} -> Open: ₹{c['open']:.2f} | High: ₹{c['high']:.2f} | Low: ₹{c['low']:.2f} | Close: ₹{c['close']:.2f}")

    print("-" * 75)
    # 4. Evaluate SMF BUY Signal Confirmation
    print("Evaluating SMF BUY Signal Confirmation Gate:")
    eval_res = kronos.evaluate_smf_confirmation(candles, signal_type="BUY", pred_len=8)
    print(f"• Trade Confirmed: {eval_res['confirmed']}")
    print(f"• AI Confidence:   {eval_res['confidence'] * 100:.0f}%")
    print(f"• Decision Rationale: {eval_res['reason']}")
    print("=" * 75)

if __name__ == "__main__":
    main()
