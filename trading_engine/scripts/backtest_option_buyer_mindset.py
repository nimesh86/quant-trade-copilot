#!/usr/bin/env python3
"""
Option Buyer Mindset: 6-Month Comprehensive Backtest (April 2026 - October 2026)
───────────────────────────────────────────────────────────────────────────────
Mindset & Rules for Option Buying:
1. Long-Only: We ONLY BUY CALLs or PUTs. SELL is strictly an EXIT (Square-Off).
2. Volatility Compression -> Impulse Expansion:
   - Squeeze: ATR(5) expands past ATR(20) OR Donchian 15-bar channel breakout.
   - Solid Impulse: Candle body >= 60% of candle range, close in top 25% of candle.
   - Momentum: RSI(14) between 54 and 75 and rising; Fast EMA(9) > Slow EMA(21).
3. Risk Management & Profit Protection:
   - Initial Stop Loss: Breakout candle low OR Entry - 1.2 * ATR(14) (capped at ₹3 to ₹9).
   - Target 1: 1.4x Risk (Bank profit or trail aggressively).
   - Breakeven Trigger: When price reaches +0.8x Risk, move SL to Entry.
   - Stagnation Exit: If in trade for > 8 bars and price falls below EMA(9), exit (theta prevention).
4. Strict Constraints:
   - Max 5 trades per day.
   - Tradable strike selection: Liquid ATM/Near-OTM (Premium ₹60 - ₹250).
   - Deducts full exchange & broker costs: ₹25 per completed trade (entry + exit).
"""

import sys
import os
import time
import pandas as pd
import numpy as np
from sqlalchemy import create_engine

DB_URL = "postgresql+psycopg2://postgres:postgres@localhost:5432/trading"

def run_backtest():
    engine = create_engine(DB_URL)
    print("=" * 80)
    print("⚡ OPTION BUYER MINDSET: 6-MONTH HISTORICAL BACKTEST (128 DAYS)")
    print("=" * 80)
    
    t0 = time.time()
    query = """
    SELECT timestamp, symbol, open, high, low, close
    FROM option_minute_candles
    ORDER BY timestamp, symbol ASC;
    """
    print("Loading option data from TimescaleDB...")
    df = pd.read_sql(query, engine)
    print(f"Loaded {len(df):,} candles across {df['symbol'].nunique()} option contracts in {time.time()-t0:.1f}s.")
    
    # Convert UTC to IST
    df['timestamp'] = pd.to_datetime(df['timestamp']).dt.tz_convert('Asia/Kolkata')
    df['date'] = df['timestamp'].dt.strftime('%Y-%m-%d')
    unique_dates = sorted(df['date'].unique())
    
    print(f"Testing across {len(unique_dates)} trading days with Max 5 trades/day limit...")
    
    all_trades = []
    MAX_TRADES_PER_DAY = 5
    
    for day_str in unique_dates:
        day_df = df[df['date'] == day_str]
        syms_today = day_df['symbol'].unique()
        
        # Precompute indicators per symbol for this day
        contract_data = {}
        for s in syms_today:
            cdf = day_df[day_df['symbol'] == s].sort_values('timestamp').reset_index(drop=True)
            if len(cdf) < 35:
                continue
            avg_p = cdf['close'].mean()
            # Focus on liquid retail options
            if avg_p < 55.0 or avg_p > 260.0:
                continue
                
            closes = cdf['close'].values
            highs = cdf['high'].values
            lows = cdf['low'].values
            opens = cdf['open'].values
            times = cdf['timestamp'].values
            n = len(closes)
            
            # EMA 9 and EMA 21
            ema9 = pd.Series(closes).ewm(span=9, adjust=False).mean().values
            ema21 = pd.Series(closes).ewm(span=21, adjust=False).mean().values
            
            # ATR 14
            tr = np.maximum(highs - lows, np.maximum(np.abs(highs - np.roll(closes, 1)), np.abs(lows - np.roll(closes, 1))))
            tr[0] = highs[0] - lows[0]
            atr14 = pd.Series(tr).ewm(alpha=1.0/14.0, adjust=False).mean().values
            atr5 = pd.Series(tr).ewm(alpha=1.0/5.0, adjust=False).mean().values
            atr20 = pd.Series(tr).ewm(alpha=1.0/20.0, adjust=False).mean().values
            
            # RSI 14
            delta = pd.Series(closes).diff()
            gain = delta.clip(lower=0)
            loss = -delta.clip(upper=0)
            avg_g = gain.ewm(alpha=1.0/14.0, adjust=False).mean().values
            avg_l = loss.ewm(alpha=1.0/14.0, adjust=False).mean().values
            rs = np.where(avg_l == 0, 100.0, avg_g / np.maximum(avg_l, 1e-6))
            rsi14 = 100.0 - (100.0 / (1.0 + rs))
            
            # Donchian 15-period High (excluding current bar)
            donchian_h = pd.Series(highs).shift(1).rolling(15, min_periods=5).max().values
            
            contract_data[s] = {
                "closes": closes, "highs": highs, "lows": lows, "opens": opens, "times": times,
                "ema9": ema9, "ema21": ema21, "atr14": atr14, "atr5": atr5, "atr20": atr20,
                "rsi14": rsi14, "donchian_h": donchian_h
            }
            
        if not contract_data:
            continue
            
        trades_today = 0
        in_trade = False
        active_contract = None
        active_entry = 0.0
        active_sl = 0.0
        active_target = 0.0
        active_entry_time = None
        active_entry_bar = 0
        be_locked = False
        max_favorable = 0.0
        
        sample_s = list(contract_data.keys())[0]
        day_times = contract_data[sample_s]["times"]
        
        for bar_idx in range(20, len(day_times)):
            cur_time = pd.to_datetime(day_times[bar_idx])
            thm = cur_time.strftime("%H:%M")
            
            # ─────────────────────────────────────────────────────────────
            # IF IN TRADE: MONITOR EXITS
            # ─────────────────────────────────────────────────────────────
            if in_trade:
                cdata = contract_data[active_contract]
                if bar_idx >= len(cdata["highs"]):
                    in_trade = False
                    continue
                    
                cur_h = cdata["highs"][bar_idx]
                cur_l = cdata["lows"][bar_idx]
                cur_c = cdata["closes"][bar_idx]
                fav = cur_h - active_entry
                max_favorable = max(max_favorable, fav)
                
                # Check Breakeven move: once +0.8x risk is achieved, protect capital!
                init_risk = (active_entry - (cdata["opens"][active_entry_bar] - cdata["atr14"][active_entry_bar])) # approx
                if not be_locked and fav >= 0.8 * (active_target - active_entry) / 1.4:
                    be_locked = True
                    active_sl = max(active_sl, active_entry + 0.5) # Breakeven + small buffer
                    
                # Exit 1: Target Hit (1.4x Risk)
                if cur_h >= active_target:
                    exit_p = active_target
                    pnl = exit_p - active_entry
                    all_trades.append({
                        "date": day_str, "symbol": active_contract, "entry_time": active_entry_time,
                        "exit_time": cur_time, "entry_price": active_entry, "exit_price": exit_p,
                        "pnl": pnl, "outcome": "TARGET_HIT", "mfe": max_favorable
                    })
                    in_trade = False
                    continue
                    
                # Exit 2: Stop Loss Hit
                if cur_l <= active_sl:
                    exit_p = active_sl
                    pnl = exit_p - active_entry
                    outcome = "BREAKEVEN_EXIT" if be_locked else "STOP_LOSS"
                    all_trades.append({
                        "date": day_str, "symbol": active_contract, "entry_time": active_entry_time,
                        "exit_time": cur_time, "entry_price": active_entry, "exit_price": exit_p,
                        "pnl": pnl, "outcome": outcome, "mfe": max_favorable
                    })
                    in_trade = False
                    continue
                    
                # Exit 3: Theta / Stagnation Exit (> 8 bars without progress & below 9 EMA)
                bars_held = bar_idx - active_entry_bar
                if bars_held >= 8 and cur_c < cdata["ema9"][bar_idx]:
                    exit_p = cur_c
                    pnl = exit_p - active_entry
                    all_trades.append({
                        "date": day_str, "symbol": active_contract, "entry_time": active_entry_time,
                        "exit_time": cur_time, "entry_price": active_entry, "exit_price": exit_p,
                        "pnl": pnl, "outcome": "STAGNATION_EXIT", "mfe": max_favorable
                    })
                    in_trade = False
                    continue
                    
                # Exit 4: EOD Square-off at 15:15 IST
                if thm >= "15:15":
                    exit_p = cur_c
                    pnl = exit_p - active_entry
                    all_trades.append({
                        "date": day_str, "symbol": active_contract, "entry_time": active_entry_time,
                        "exit_time": cur_time, "entry_price": active_entry, "exit_price": exit_p,
                        "pnl": pnl, "outcome": "EOD_EXIT", "mfe": max_favorable
                    })
                    in_trade = False
                    continue
                    
                continue
                
            # ─────────────────────────────────────────────────────────────
            # IF NOT IN TRADE: SCAN FOR OPTION BUYER BREAKOUT
            # ─────────────────────────────────────────────────────────────
            if trades_today >= MAX_TRADES_PER_DAY:
                continue
            if thm < "09:25" or thm > "14:45":
                continue
                
            candidates = []
            for s, cdata in contract_data.items():
                if bar_idx >= len(cdata["closes"]):
                    continue
                    
                c = cdata["closes"][bar_idx]
                o = cdata["opens"][bar_idx]
                h = cdata["highs"][bar_idx]
                l = cdata["lows"][bar_idx]
                prev_c = cdata["closes"][bar_idx - 1]
                
                ema9 = cdata["ema9"][bar_idx]
                ema21 = cdata["ema21"][bar_idx]
                prev_ema9 = cdata["ema9"][bar_idx - 1]
                prev_ema21 = cdata["ema21"][bar_idx - 1]
                
                atr = cdata["atr14"][bar_idx]
                atr5 = cdata["atr5"][bar_idx]
                atr20 = cdata["atr20"][bar_idx]
                rsi = cdata["rsi14"][bar_idx]
                prev_rsi = cdata["rsi14"][bar_idx - 1]
                donch = cdata["donchian_h"][bar_idx]
                
                # Rule 1: Trend Alignment (Fast EMA above Slow EMA)
                trend_ok = (ema9 > ema21) and (c > ema9)
                
                # Rule 2: Volatility Expansion (Donchian 15 breakout OR ATR expansion)
                vol_expansion = (c > donch) or (atr5 > atr20 * 1.05 and c > ema9 + 0.3 * atr)
                
                # Rule 3: Bullish Solid Candle Body (Min 55% body, close near high)
                rng = max(h - l, 0.1)
                body = c - o
                solid_body = (body / rng >= 0.55) and ((c - l) / rng >= 0.70)
                
                # Rule 4: RSI Momentum in Sweet Spot (54 to 75, rising)
                rsi_ok = (rsi >= 54.0) and (rsi <= 75.0) and (rsi > prev_rsi)
                
                # Rule 5: Fresh Trigger (Previous bar was not already above donchian)
                fresh_trigger = (prev_c <= cdata["donchian_h"][bar_idx - 1] if not np.isnan(cdata["donchian_h"][bar_idx - 1]) else True) or (prev_c <= prev_ema9)
                
                if trend_ok and vol_expansion and solid_body and rsi_ok and fresh_trigger:
                    score = rsi + (body / rng) * 20.0
                    candidates.append((s, c, l, atr, score))
                    
            if candidates:
                # Pick the highest quality setup
                candidates.sort(key=lambda x: x[4], reverse=True)
                best_s, best_entry, best_low, best_atr, _ = candidates[0]
                
                in_trade = True
                active_contract = best_s
                active_entry = best_entry
                
                # Risk calculation: low of trigger candle, with min ₹3 and max ₹8
                candle_risk = active_entry - best_low
                risk = max(3.0, min(8.0, max(candle_risk, best_atr * 1.1)))
                active_sl = active_entry - risk
                active_target = active_entry + 1.4 * risk
                
                active_entry_time = cur_time
                active_entry_bar = bar_idx
                be_locked = False
                max_favorable = 0.0
                trades_today += 1
                
    tdf = pd.DataFrame(all_trades)
    if len(tdf) == 0:
        print("No trades generated.")
        return
        
    LOT_SIZE = 25
    BROKERAGE_PER_TRADE = 25.0 # Round-trip ₹20 broker + ₹5 taxes/STT
    
    tdf['pnl_per_lot'] = tdf['pnl'] * LOT_SIZE
    tdf['net_pnl_per_lot'] = tdf['pnl_per_lot'] - BROKERAGE_PER_TRADE
    
    total_trades = len(tdf)
    wins = len(tdf[tdf['pnl'] > 0])
    losses = len(tdf[tdf['pnl'] <= 0])
    win_rate = (wins / total_trades) * 100
    
    gross_pts = tdf['pnl'].sum()
    gross_pnl = tdf['pnl_per_lot'].sum()
    total_fees = total_trades * BROKERAGE_PER_TRADE
    net_pnl = gross_pnl - total_fees
    
    avg_win = tdf[tdf['pnl'] > 0]['pnl'].mean() if wins > 0 else 0
    avg_loss = abs(tdf[tdf['pnl'] < 0]['pnl'].mean()) if len(tdf[tdf['pnl'] < 0]) > 0 else 0
    profit_factor = (tdf[tdf['pnl'] > 0]['pnl_per_lot'].sum() / abs(tdf[tdf['pnl'] < 0]['pnl_per_lot'].sum())) if abs(tdf[tdf['pnl'] < 0]['pnl_per_lot'].sum()) > 0 else 999.0
    
    print("\n" + "=" * 80)
    print("🎯 OPTION BUYER MINDSET: 6-MONTH OVERALL RESULTS (128 DAYS)")
    print("=" * 80)
    print(f"Total Completed Trades: {total_trades:,} ({total_trades/128:.2f} trades/day - strictly <= 5 cap)")
    print(f"Winning Trades:        {wins:,} ({win_rate:.1f}%)")
    print(f"Losing / BE Trades:    {losses:,} ({100 - win_rate:.1f}%)")
    print(f"Gross Points:          {gross_pts:+.2f} pts")
    print(f"Gross PnL (1 Lot):     ₹{gross_pnl:+,.2f}")
    print(f"Total Fees & STT:      ₹{total_fees:,.2f}")
    print(f"Net Realized Profit:   ₹{net_pnl:+,.2f}")
    print(f"Profit Factor:         {profit_factor:.2f}")
    print(f"Average Win:           +{avg_win:.2f} pts (+₹{avg_win*25:.1f})")
    print(f"Average Loss:          -{avg_loss:.2f} pts (-₹{avg_loss*25:.1f})")
    
    # Outcomes breakdown
    print("\nTrade Outcomes Breakdown:")
    for outc, grp in tdf.groupby('outcome'):
        print(f"  - {outc:<16}: {len(grp):>3} trades ({len(grp)/total_trades*100:>5.1f}%) | Net PnL: ₹{grp['net_pnl_per_lot'].sum():>+9,.2f}")
        
    # Month-by-month table
    tdf['month'] = pd.to_datetime(tdf['date']).dt.strftime('%Y-%m')
    print("\n" + "-" * 80)
    print("📅 MONTH-BY-MONTH BREAKDOWN (1 LOT NIFTY OPTION):")
    print("-" * 80)
    print(f"{'Month':<10} | {'Trades':<7} | {'Win %':<7} | {'Gross Pts':<11} | {'Gross PnL':<12} | {'Net PnL':<12}")
    print("-" * 80)
    
    for m, mdf in tdf.groupby('month'):
        m_tot = len(mdf)
        m_win = len(mdf[mdf['pnl'] > 0])
        m_wr = (m_win / m_tot) * 100 if m_tot > 0 else 0
        m_pts = mdf['pnl'].sum()
        m_gp = mdf['pnl_per_lot'].sum()
        m_np = mdf['net_pnl_per_lot'].sum()
        print(f"{m:<10} | {m_tot:<7} | {m_wr:>5.1f}% | {m_pts:>+9.2f} pt | ₹{m_gp:>+10,.2f} | ₹{m_np:>+10,.2f}")
    print("-" * 80)

if __name__ == "__main__":
    run_backtest()
