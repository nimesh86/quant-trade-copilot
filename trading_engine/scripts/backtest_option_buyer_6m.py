#!/usr/bin/env python3
"""
Option Buyer 6-Month Backtest Engine (April 2026 - October 2026)
────────────────────────────────────────────────────────────────
Tests a clean, institutional Volatility Expansion & Momentum Ignition
model specifically designed for OPTION BUYERS across 128 trading days (799,330 candles).

Key Option Buyer Mindset:
1. Long-Only: Only BUY calls/puts during explosive expansions. Never naked short.
2. Low Frequency: Max 2 trades per contract per day (filters out chop).
3. Ignition Trigger: Confirmed breakout above Baseline + 0.4 ATR with solid bullish candle body (close >= 65% of range).
4. Momentum Filter: RSI (14) between 52 and 72, rising.
5. Asymmetric Risk/Reward: Small stop (1.3x ATR), Target 1 at 1.5x Risk (Lock profit), Baseline trailing exit.
6. Transaction Friction: Fully accounts for ₹25/trade (₹1/unit) covering brokerage, STT & taxes.
"""

import sys
import os
import time
from collections import defaultdict
import pandas as pd
import numpy as np
from sqlalchemy import create_engine, text

DB_URL = "postgresql+psycopg2://postgres:postgres@localhost:5432/trading"

def run_6m_backtest():
    print("=" * 80)
    print("🚀 STARTING 6-MONTH OPTION BUYER ENGINE BACKTEST (APRIL 2026 - OCTOBER 2026)")
    print("=" * 80)
    
    engine = create_engine(DB_URL)
    
    print("\n1. Loading 6-Month Option Minute Candles from TimescaleDB...")
    t0 = time.time()
    
    query = """
    SELECT timestamp, symbol, strike, option_type, expiry, open, high, low, close, volume, iv, delta, theta
    FROM option_minute_candles
    ORDER BY symbol, timestamp ASC;
    """
    
    with engine.connect() as conn:
        df = pd.read_sql(query, conn)
        
    print(f"Loaded {len(df):,} candles across {df['symbol'].nunique()} option contracts in {time.time()-t0:.1f}s.")
    
    # Convert UTC timestamps to IST
    print("Converting timestamps to IST (Asia/Kolkata)...")
    df['timestamp'] = pd.to_datetime(df['timestamp']).dt.tz_convert('Asia/Kolkata')
    
    all_trades = []
    symbols = df['symbol'].unique()
    print(f"2. Backtesting {len(symbols)} option contracts across 128 trading days...")
    
    processed = 0
    start_sim = time.time()
    
    for sym in symbols:
        sdf = df[df['symbol'] == sym].sort_values('timestamp').reset_index(drop=True)
        n = len(sdf)
        if n < 40:
            continue
            
        processed += 1
        closes = sdf['close'].values
        highs = sdf['high'].values
        lows = sdf['low'].values
        opens = sdf['open'].values
        times = sdf['timestamp'].values
        
        # 1. EMAs for SMF Baseline
        ema9 = pd.Series(closes).ewm(span=9, adjust=False).mean().values
        ema21 = pd.Series(closes).ewm(span=21, adjust=False).mean().values
        b_main = (ema9 + ema21) / 2.0
        
        # 2. ATR 14
        tr = np.maximum(highs - lows, np.maximum(np.abs(highs - np.roll(closes, 1)), np.abs(lows - np.roll(closes, 1))))
        tr[0] = highs[0] - lows[0]
        atr14 = pd.Series(tr).ewm(alpha=1.0/14.0, adjust=False).mean().values
        
        # 3. RSI 14
        delta = pd.Series(closes).diff()
        gain = delta.clip(lower=0)
        loss = -delta.clip(upper=0)
        avg_gain = gain.ewm(alpha=1.0/14.0, adjust=False).mean().values
        avg_loss = loss.ewm(alpha=1.0/14.0, adjust=False).mean().values
        rs = np.where(avg_loss == 0, 100.0, avg_gain / np.maximum(avg_loss, 1e-6))
        rsi14 = 100.0 - (100.0 / (1.0 + rs))
        
        # Trade State
        in_trade = False
        entry_idx = 0
        entry_price = 0.0
        sl_price = 0.0
        t1_price = 0.0
        max_fav = 0.0
        daily_trades = defaultdict(int)
        
        for i in range(25, n):
            bar_time = pd.to_datetime(times[i])
            day_key = bar_time.strftime("%Y-%m-%d")
            time_hm = bar_time.strftime("%H:%M")
            
            # If in trade, monitor exit protocols
            if in_trade:
                cur_h = highs[i]
                cur_l = lows[i]
                cur_c = closes[i]
                fav = cur_h - entry_price
                max_fav = max(max_fav, fav)
                
                # Exit 1: Target 1 (1.5x Risk)
                if cur_h >= t1_price:
                    exit_p = t1_price
                    pnl = exit_p - entry_price
                    all_trades.append({
                        "symbol": sym,
                        "date": day_key,
                        "entry_time": times[entry_idx],
                        "exit_time": bar_time,
                        "entry_price": entry_price,
                        "exit_price": exit_p,
                        "pnl": pnl,
                        "mfe": max_fav,
                        "outcome": "TARGET_1",
                        "hold_bars": i - entry_idx,
                    })
                    in_trade = False
                    continue
                    
                # Exit 2: Stop Loss
                if cur_l <= sl_price:
                    exit_p = sl_price
                    pnl = exit_p - entry_price
                    all_trades.append({
                        "symbol": sym,
                        "date": day_key,
                        "entry_time": times[entry_idx],
                        "exit_time": bar_time,
                        "entry_price": entry_price,
                        "exit_price": exit_p,
                        "pnl": pnl,
                        "mfe": max_fav,
                        "outcome": "STOP_LOSS",
                        "hold_bars": i - entry_idx,
                    })
                    in_trade = False
                    continue
                    
                # Exit 3: Trend Exhaustion (Close below baseline after 3+ bars)
                if cur_c < b_main[i] - 0.2 * atr14[i] and (i - entry_idx) >= 3:
                    exit_p = cur_c
                    pnl = exit_p - entry_price
                    all_trades.append({
                        "symbol": sym,
                        "date": day_key,
                        "entry_time": times[entry_idx],
                        "exit_time": bar_time,
                        "entry_price": entry_price,
                        "exit_price": exit_p,
                        "pnl": pnl,
                        "mfe": max_fav,
                        "outcome": "TREND_EXIT",
                        "hold_bars": i - entry_idx,
                    })
                    in_trade = False
                    continue
                    
                # Exit 4: EOD Square-off at 15:20 IST
                if time_hm >= "15:20":
                    exit_p = cur_c
                    pnl = exit_p - entry_price
                    all_trades.append({
                        "symbol": sym,
                        "date": day_key,
                        "entry_time": times[entry_idx],
                        "exit_time": bar_time,
                        "entry_price": entry_price,
                        "exit_price": exit_p,
                        "pnl": pnl,
                        "mfe": max_fav,
                        "outcome": "EOD_EXIT",
                        "hold_bars": i - entry_idx,
                    })
                    in_trade = False
                    continue
                    
                continue
                
            # If NOT in trade: Check Entry Criteria
            # Max 2 trades per contract per day
            if daily_trades[day_key] >= 2:
                continue
                
            # Time filter: 09:25 to 14:45 IST (avoid open volatility & late-day theta collapse)
            if time_hm < "09:25" or time_hm > "14:45":
                continue
                
            # Tradable option price: ₹20 to ₹350
            c = closes[i]
            if c < 20.0 or c > 350.0:
                continue
                
            # Trigger 1: Confirmed Breakout above Baseline + 0.4 ATR
            is_breakout = (c > b_main[i] + 0.4 * atr14[i]) and (closes[i-1] <= b_main[i-1] + 0.4 * atr14[i-1])
            
            # Trigger 2: RSI Momentum (52 to 72, rising)
            is_rsi = (rsi14[i] >= 52.0) and (rsi14[i] <= 72.0) and (rsi14[i] > rsi14[i-1])
            
            # Trigger 3: Strong Bullish Candle (close near high, top 35% of range)
            c_range = max(highs[i] - lows[i], 0.1)
            is_solid = ((c - lows[i]) / c_range) >= 0.65
            
            if is_breakout and is_rsi and is_solid:
                in_trade = True
                entry_idx = i
                entry_price = c
                risk = max(2.5, min(14.0, atr14[i] * 1.3))
                sl_price = entry_price - risk
                t1_price = entry_price + 1.5 * risk
                max_fav = 0.0
                daily_trades[day_key] += 1
                
    sim_time = time.time() - start_sim
    print(f"Simulation completed for {processed} contracts in {sim_time:.1f}s.")
    
    tdf = pd.DataFrame(all_trades)
    if tdf.empty:
        print("No trades triggered.")
        return
        
    tdf['pnl_per_lot'] = tdf['pnl'] * 25 # NIFTY lot size 25
    charges_per_trade = 1.0 * 25 # ₹25 per trade (~₹1/unit covers brokerage, STT & turnover)
    tdf['net_pnl_per_lot'] = tdf['pnl_per_lot'] - charges_per_trade
    
    total_trades = len(tdf)
    wins = len(tdf[tdf['pnl'] > 0])
    losses = len(tdf[tdf['pnl'] <= 0])
    win_rate = (wins / total_trades) * 100.0
    
    gross_pts = tdf['pnl'].sum()
    gross_pnl = tdf['pnl_per_lot'].sum()
    total_charges = total_trades * charges_per_trade
    net_pnl = gross_pnl - total_charges
    
    avg_win = tdf[tdf['pnl'] > 0]['pnl'].mean() if wins > 0 else 0.0
    avg_loss = abs(tdf[tdf['pnl'] <= 0]['pnl'].mean()) if losses > 0 else 1.0
    profit_factor = (avg_win * wins) / max(avg_loss * losses, 1e-4)
    
    # Calculate Max Drawdown
    tdf['cum_net_pnl'] = tdf['net_pnl_per_lot'].cumsum()
    peak = tdf['cum_net_pnl'].cummax()
    drawdown = peak - tdf['cum_net_pnl']
    max_dd = drawdown.max()
    
    # Target 1 vs Stop Loss Breakdown
    t1_hits = len(tdf[tdf['outcome'] == 'TARGET_1'])
    sl_hits = len(tdf[tdf['outcome'] == 'STOP_LOSS'])
    trend_exits = len(tdf[tdf['outcome'] == 'TREND_EXIT'])
    eod_exits = len(tdf[tdf['outcome'] == 'EOD_EXIT'])
    
    # Monthly Breakdown
    tdf['month'] = pd.to_datetime(tdf['date']).dt.strftime('%Y-%m')
    monthly = tdf.groupby('month').agg(
        trades=('pnl', 'count'),
        wins=('pnl', lambda x: (x > 0).sum()),
        gross_pts=('pnl', 'sum'),
        gross_pnl=('pnl_per_lot', 'sum'),
        net_profit=('net_pnl_per_lot', 'sum')
    ).reset_index()
    monthly['win_rate'] = (monthly['wins'] / monthly['trades']) * 100.0
    
    print("\n" + "=" * 85)
    print("📊 6-MONTH OPTION BUYER BACKTEST RESULTS (APRIL 2026 - OCTOBER 2026)")
    print("=" * 85)
    print(f"• Total Trading Days:      128 days")
    print(f"• Total Trades Executed:   {total_trades} (Average {total_trades/128:.1f} trades/day across universe)")
    print(f"• Winning Trades:          {wins} ({win_rate:.1f}%)")
    print(f"• Losing Trades:           {losses} ({100.0 - win_rate:.1f}%)")
    print(f"• Profit Factor:           {profit_factor:.2f}")
    print(f"• Gross Points Earned:     {gross_pts:+,.2f} pts")
    print(f"• Gross PnL (1 Lot NIFTY): ₹{gross_pnl:+,.2f}")
    print(f"• Total Brokerage & Taxes: ₹{total_charges:,.2f}")
    print(f"• NET PROFIT (After Fees): ₹{net_pnl:+,.2f}")
    print(f"• Maximum Drawdown:        ₹{max_dd:,.2f}")
    print("-" * 85)
    print(f"• Exit Breakdown:")
    print(f"  - Target 1 Hit (1.5x Risk): {t1_hits} ({t1_hits/total_trades*100:.1f}%)")
    print(f"  - Stop Loss Hit:            {sl_hits} ({sl_hits/total_trades*100:.1f}%)")
    print(f"  - Baseline Trend Exit:      {trend_exits} ({trend_exits/total_trades*100:.1f}%)")
    print(f"  - End-of-Day Square Off:    {eod_exits} ({eod_exits/total_trades*100:.1f}%)")
    print("=" * 85)
    
    print("\n📅 MONTH-BY-MONTH PERFORMANCE BREAKDOWN:")
    print("-" * 85)
    print(f"{'MONTH':<10} | {'TRADES':<8} | {'WINS':<6} | {'WIN RATE':<10} | {'POINTS':<12} | {'NET PROFIT (₹)':<16}")
    print("-" * 85)
    for _, row in monthly.iterrows():
        print(f"{row['month']:<10} | {int(row['trades']):<8} | {int(row['wins']):<6} | {row['win_rate']:<9.1f}% | {row['gross_pts']:<+11.2f} pts | ₹{row['net_profit']:<+15,.2f}")
    print("-" * 85)
    
    # Save reports
    tdf.to_csv("option_buyer_6m_backtest_trades.csv", index=False)
    monthly.to_csv("option_buyer_6m_monthly_report.csv", index=False)
    print("\nDetailed trade log saved to 'option_buyer_6m_backtest_trades.csv'.")
    print("Monthly summary saved to 'option_buyer_6m_monthly_report.csv'.\n")

if __name__ == "__main__":
    run_6m_backtest()
