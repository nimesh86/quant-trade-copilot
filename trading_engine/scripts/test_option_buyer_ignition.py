#!/usr/bin/env python3
"""
Option Buyer Ignition Engine: 6-Month Backtest (April 2026 - October 2026)
──────────────────────────────────────────────────────────────────────────
Designed specifically around Option Buyer Physics:
1. Ignition Trigger:
   - Contraction: ATR(5) was compressed, price was consolidated inside 10-bar Donchian channel.
   - Ignition Candle: Candle breaks 10-bar high with strong body (> 60% of candle range).
   - Momentum: RSI crosses above 52 with positive slope.
2. Trade Management:
   - Stop Loss: Low of the ignition candle (or Entry - 1.0x ATR, capped ₹3 to ₹7).
   - Target 1 (Lock 50% or full): +1.5x Risk (typically +7 to +12 pts).
   - Breakeven Trigger: Once +0.8x Risk is touched, SL moves to Entry.
   - Stagnation Exit: If 6 bars pass without hitting Target 1 and candle closes below 9 EMA -> EXIT immediately.
   - EOD Square-Off: 15:15 IST.
3. Max 5 trades per day.
4. ₹25/trade brokerage deduction.
"""

import sys
import os
import time
import pandas as pd
import numpy as np
from sqlalchemy import create_engine

DB_URL = "postgresql+psycopg2://postgres:postgres@localhost:5432/trading"

def calc_atr(highs, lows, closes, length=14):
    n = len(highs)
    tr = np.zeros(n)
    tr[0] = highs[0] - lows[0]
    for i in range(1, n):
        tr[i] = max(highs[i] - lows[i], abs(highs[i] - closes[i-1]), abs(lows[i] - closes[i-1]))
    return pd.Series(tr).ewm(alpha=1.0/length, adjust=False).mean().values

def calc_rsi(closes, period=14):
    delta = pd.Series(closes).diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_g = gain.ewm(alpha=1.0/period, adjust=False).mean().values
    avg_l = loss.ewm(alpha=1.0/period, adjust=False).mean().values
    rs = np.where(avg_l == 0, 100.0, avg_g / np.maximum(avg_l, 1e-6))
    return 100.0 - (100.0 / (1.0 + rs))

def test_ignition_strategy():
    engine = create_engine(DB_URL)
    print("=" * 80)
    print("🚀 OPTION BUYER IGNITION ENGINE: 6-MONTH BACKTEST (128 DAYS)")
    print("=" * 80)
    
    t0 = time.time()
    query = """
    SELECT timestamp, symbol, open, high, low, close
    FROM option_minute_candles
    ORDER BY timestamp, symbol ASC;
    """
    df = pd.read_sql(query, engine)
    print(f"Loaded {len(df):,} candles in {time.time()-t0:.1f}s.")
    
    df['timestamp'] = pd.to_datetime(df['timestamp']).dt.tz_convert('Asia/Kolkata')
    df['date'] = df['timestamp'].dt.strftime('%Y-%m-%d')
    unique_dates = sorted(df['date'].unique())
    
    MAX_TRADES_PER_DAY = 5
    
    # Test different Risk-Reward targets and compression bars
    for tgt_mult in [1.3, 1.5, 1.8]:
        for lookback in [8, 12]:
            all_trades = []
            for day_str in unique_dates:
                day_df = df[df['date'] == day_str]
                syms_today = day_df['symbol'].unique()
                
                contract_data = {}
                for s in syms_today:
                    cdf = day_df[day_df['symbol'] == s].sort_values('timestamp').reset_index(drop=True)
                    if len(cdf) < 35: continue
                    avg_p = cdf['close'].mean()
                    if avg_p < 55.0 or avg_p > 260.0: continue
                    
                    closes = cdf['close'].values
                    highs = cdf['high'].values
                    lows = cdf['low'].values
                    opens = cdf['open'].values
                    times = cdf['timestamp'].values
                    
                    ema9 = pd.Series(closes).ewm(span=9, adjust=False).mean().values
                    ema21 = pd.Series(closes).ewm(span=21, adjust=False).mean().values
                    atr14 = calc_atr(highs, lows, closes, 14)
                    rsi14 = calc_rsi(closes, 14)
                    
                    # Donchian channel upper boundary
                    donch_h = pd.Series(highs).shift(1).rolling(lookback, min_periods=4).max().values
                    
                    contract_data[s] = {
                        "closes": closes, "highs": highs, "lows": lows, "opens": opens, "times": times,
                        "ema9": ema9, "ema21": ema21, "atr14": atr14, "rsi14": rsi14, "donch_h": donch_h
                    }
                    
                if not contract_data: continue
                
                trades_today = 0
                in_trade = False
                active_contract = None
                active_entry = 0.0
                active_sl = 0.0
                active_target = 0.0
                active_entry_bar = 0
                be_moved = False
                
                sample_s = list(contract_data.keys())[0]
                day_times = contract_data[sample_s]["times"]
                
                for b in range(lookback + 2, len(day_times)):
                    cur_time = pd.to_datetime(day_times[b])
                    thm = cur_time.strftime("%H:%M")
                    
                    if in_trade:
                        cdata = contract_data[active_contract]
                        if b >= len(cdata["highs"]):
                            in_trade = False; continue
                            
                        ch = cdata["highs"][b]
                        cl = cdata["lows"][b]
                        cc = cdata["closes"][b]
                        fav = ch - active_entry
                        
                        # Move to breakeven after +0.8x risk
                        risk_amt = (active_target - active_entry) / tgt_mult
                        if not be_moved and fav >= 0.8 * risk_amt:
                            be_moved = True
                            active_sl = max(active_sl, active_entry + 0.3)
                            
                        # Exit 1: Target Hit
                        if ch >= active_target:
                            pnl = active_target - active_entry
                            all_trades.append({"date": day_str, "pnl": pnl, "outcome": "TARGET"})
                            in_trade = False; continue
                            
                        # Exit 2: Stop Loss Hit
                        if cl <= active_sl:
                            pnl = active_sl - active_entry
                            all_trades.append({"date": day_str, "pnl": pnl, "outcome": "SL"})
                            in_trade = False; continue
                            
                        # Exit 3: Stagnation (> 6 bars and below 9 EMA)
                        if (b - active_entry_bar >= 6) and (cc < cdata["ema9"][b]):
                            pnl = cc - active_entry
                            all_trades.append({"date": day_str, "pnl": pnl, "outcome": "STAGNATION"})
                            in_trade = False; continue
                            
                        # Exit 4: EOD
                        if thm >= "15:15":
                            pnl = cc - active_entry
                            all_trades.append({"date": day_str, "pnl": pnl, "outcome": "EOD"})
                            in_trade = False; continue
                        continue
                        
                    if trades_today >= MAX_TRADES_PER_DAY: continue
                    if thm < "09:25" or thm > "14:45": continue
                    
                    candidates = []
                    for s, cdata in contract_data.items():
                        if b >= len(cdata["closes"]): continue
                        c = cdata["closes"][b]
                        o = cdata["opens"][b]
                        h = cdata["highs"][b]
                        l = cdata["lows"][b]
                        prev_c = cdata["closes"][b-1]
                        dh = cdata["donch_h"][b]
                        prev_dh = cdata["donch_h"][b-1]
                        
                        if np.isnan(dh) or np.isnan(prev_dh): continue
                        
                        # Ignition rule: fresh break of lookback high
                        is_break = (c > dh) and (prev_c <= prev_dh)
                        
                        # Strong solid body
                        rng = max(h - l, 0.1)
                        body = c - o
                        is_solid = (body / rng >= 0.60) and ((c - l) / rng >= 0.70)
                        
                        # Trend & Momentum
                        rsi = cdata["rsi14"][b]
                        prev_rsi = cdata["rsi14"][b-1]
                        is_mom = (rsi >= 52.0) and (rsi <= 75.0) and (rsi > prev_rsi)
                        is_trend = c > cdata["ema9"][b] and cdata["ema9"][b] >= cdata["ema21"][b]
                        
                        if is_break and is_solid and is_mom and is_trend:
                            candidates.append((s, c, l, cdata["atr14"][b], rsi))
                            
                    if candidates:
                        candidates.sort(key=lambda x: x[4], reverse=True)
                        bs, bc, bl, batr, _ = candidates[0]
                        in_trade = True
                        active_contract = bs
                        active_entry = bc
                        # Stop loss at breakout candle low or 1.0x ATR
                        risk = max(3.0, min(7.0, max(active_entry - bl, batr * 1.0)))
                        active_sl = active_entry - risk
                        active_target = active_entry + tgt_mult * risk
                        active_entry_bar = b
                        be_moved = False
                        trades_today += 1
                        
            tdf = pd.DataFrame(all_trades)
            if len(tdf) > 0:
                tdf['net_pnl'] = tdf['pnl'] * 25.0 - 25.0
                tot = len(tdf)
                wr = len(tdf[tdf['pnl'] > 0]) / tot * 100
                pts = tdf['pnl'].sum()
                net = tdf['net_pnl'].sum()
                tgt_hits = len(tdf[tdf['outcome'] == 'TARGET'])
                print(f"Lookback: {lookback:>2} | Tgt: {tgt_mult}x | Trades: {tot:>3} ({tot/128:.1f}/d) | WR: {wr:>5.1f}% | Pts: {pts:>+8.1f} | Net ₹: {net:>+10,.1f} | Tgt Hits: {tgt_hits} ({tgt_hits/tot*100:.1f}%)")

if __name__ == "__main__":
    test_ignition_strategy()
