"""
Zerodha Kite NIFTY 50 Historical 1-Minute Candle Ingestion
From 2023-01-01 to Present.
"""
import os
import re
import sys
import time
import threading
import webbrowser
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pandas as pd
from dotenv import load_dotenv
from kiteconnect import KiteConnect
from sqlalchemy import create_engine, text
from psycopg2.extras import execute_values

# Paths & Config
AI_TRADER_DIR = Path("/Users/admin/Documents/Nimesh/Projects/ai-trade/AI-trader")
ENV_PATH = AI_TRADER_DIR / ".env"
load_dotenv(ENV_PATH)

API_KEY = os.getenv("KITE_API_KEY", "").strip() or os.getenv("ZERODHA_API_KEY", "").strip()
API_SECRET = os.getenv("KITE_API_SECRET", "").strip() or os.getenv("ZERODHA_API_SECRET", "").strip()
ACCESS_TOKEN = os.getenv("KITE_ACCESS_TOKEN", "").strip() or os.getenv("ZERODHA_ACCESS_TOKEN", "").strip()

DB_URL = "postgresql+psycopg2://postgres:postgres@localhost:5432/trading"
NIFTY_TOKEN = 256265  # NSE:NIFTY 50 cash spot index
SYMBOL_NAME = "NIFTY 50"

captured_request_token = None
server_done = threading.Event()


class RedirectHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        global captured_request_token
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)
        if "request_token" in params:
            captured_request_token = params["request_token"][0]
            self.send_response(200)
            self.send_header("Content-type", "text/html")
            self.end_headers()
            self.wfile.write(b"""
            <html>
            <head><title>Zerodha Kite Auth Success</title></head>
            <body style="font-family: sans-serif; text-align: center; padding-top: 50px; background: #0f172a; color: #fff;">
                <h2 style="color: #22c55e;">Authentication Successful!</h2>
                <p>Request token captured. Downloading historical candles now...</p>
                <p style="color: #94a3b8;">You can close this tab.</p>
            </body>
            </html>
            """)
            server_done.set()
        else:
            self.send_response(400)
            self.end_headers()
            self.wfile.write(b"No request_token found.")

    def log_message(self, format, *args):
        pass


def save_access_token(token: str):
    if ENV_PATH.exists():
        content = ENV_PATH.read_text()
        if "KITE_ACCESS_TOKEN=" in content:
            content = re.sub(r"KITE_ACCESS_TOKEN=.*", f"KITE_ACCESS_TOKEN={token}", content)
        else:
            content += f"\nKITE_ACCESS_TOKEN={token}\n"
        if "ZERODHA_ACCESS_TOKEN=" in content:
            content = re.sub(r"ZERODHA_ACCESS_TOKEN=.*", f"ZERODHA_ACCESS_TOKEN={token}", content)
        else:
            content += f"\nZERODHA_ACCESS_TOKEN={token}\n"
        ENV_PATH.write_text(content)
        print(f"Saved new access token into {ENV_PATH}")


def authenticate_kite():
    global ACCESS_TOKEN
    kite = KiteConnect(api_key=API_KEY)
    
    # Check if current access token is still valid
    if ACCESS_TOKEN:
        try:
            kite.set_access_token(ACCESS_TOKEN)
            profile = kite.profile()
            print(f"Kite session already valid! User: {profile.get('user_name')} ({profile.get('user_id')})")
            return kite
        except Exception:
            print("Existing access token is expired or invalid. Starting login flow...")

    # Start local listener
    httpd = HTTPServer(("127.0.0.1", 8000), RedirectHandler)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()

    login_url = kite.login_url()
    print("\n" + "=" * 60)
    print("ZERODHA KITE AUTHENTICATION REQUIRED")
    print("=" * 60)
    print(f"Opening browser to login URL:\n{login_url}\n")
    print("Waiting for login on http://127.0.0.1:8000 (up to 3 minutes)...")
    try:
        webbrowser.open(login_url)
    except Exception as e:
        print(f"Could not open browser automatically: {e}")

    # Wait for callback or timeout
    success = server_done.wait(timeout=180)
    httpd.shutdown()

    if not success or not captured_request_token:
        print("Timed out waiting for login redirect.")
        sys.exit(1)

    print(f"\nCaptured request token: {captured_request_token[:6]}... Exchanging for access token...")
    session_data = kite.generate_session(captured_request_token, api_secret=API_SECRET)
    new_token = session_data["access_token"]
    save_access_token(new_token)
    kite.set_access_token(new_token)
    profile = kite.profile()
    print(f"Authenticated as: {profile.get('user_name')} ({profile.get('user_id')})\n")
    return kite


def fetch_and_upsert_candles(kite):
    eng = create_engine(DB_URL)
    IST = timezone(timedelta(hours=5, minutes=30))
    start_dt = datetime(2023, 1, 1, 9, 15, tzinfo=IST)
    end_dt = datetime.now(IST)

    print(f"Starting download of {SYMBOL_NAME} (Token {NIFTY_TOKEN}) 1-min candles")
    print(f"Range: {start_dt.strftime('%Y-%m-%d')} to {end_dt.strftime('%Y-%m-%d')}")
    print("-" * 60)

    cur_start = start_dt
    total_fetched = 0
    total_inserted = 0

    while cur_start < end_dt:
        cur_end = min(cur_start + timedelta(days=50, hours=23, minutes=59), end_dt)
        s_str = cur_start.strftime("%Y-%m-%d %H:%M:%S")
        e_str = cur_end.strftime("%Y-%m-%d %H:%M:%S")
        print(f"Fetching chunk {cur_start.strftime('%Y-%m-%d')} -> {cur_end.strftime('%Y-%m-%d')}...", end=" ", flush=True)

        try:
            records = kite.historical_data(
                instrument_token=NIFTY_TOKEN,
                from_date=s_str,
                to_date=e_str,
                interval="minute",
                continuous=False,
                oi=False
            )
        except Exception as e:
            print(f"Error fetching chunk: {e}")
            time.sleep(2)
            cur_start = cur_end + timedelta(minutes=1)
            continue

        if not records:
            print("0 records.")
            cur_start = cur_end + timedelta(minutes=1)
            continue

        print(f"{len(records):,} records.", end=" ", flush=True)
        total_fetched += len(records)

        df = pd.DataFrame(records)
        df["timestamp"] = pd.to_datetime(df["date"], utc=True)
        df["symbol"] = SYMBOL_NAME
        df["vwap"] = (df["high"] + df["low"] + df["close"]) / 3.0
        df["oi"] = 0
        if "volume" not in df.columns:
            df["volume"] = 0

        cols = ["timestamp", "symbol", "open", "high", "low", "close", "volume", "vwap", "oi"]
        df = df[cols].drop_duplicates(subset=["timestamp", "symbol"])

        # Upsert into minute_candles
        tuples = [
            (row.timestamp, row.symbol, float(row.open), float(row.high),
             float(row.low), float(row.close), float(row.volume), float(row.vwap), int(row.oi))
            for row in df.itertuples(index=False)
        ]

        upsert_sql = """
            INSERT INTO minute_candles (timestamp, symbol, open, high, low, close, volume, vwap, oi)
            VALUES %s
            ON CONFLICT (timestamp, symbol) DO UPDATE SET
                open = EXCLUDED.open,
                high = EXCLUDED.high,
                low = EXCLUDED.low,
                close = EXCLUDED.close,
                volume = EXCLUDED.volume,
                vwap = EXCLUDED.vwap,
                oi = EXCLUDED.oi;
        """

        conn = eng.raw_connection()
        try:
            with conn.cursor() as cur:
                execute_values(cur, upsert_sql, tuples)
            conn.commit()
            total_inserted += len(tuples)
            print(f"Upserted. (Total: {total_inserted:,})")
        finally:
            conn.close()

        # Rate limiting: Kite permits max 3 req/sec on historical
        time.sleep(0.4)
        cur_start = cur_end + timedelta(minutes=1)

    print("\n" + "=" * 60)
    print(f"Download and ingestion complete!")
    print(f"Total candles fetched: {total_fetched:,}")
    print(f"Total candles saved to minute_candles: {total_inserted:,}")
    print("=" * 60)


if __name__ == "__main__":
    kite = authenticate_kite()
    fetch_and_upsert_candles(kite)
