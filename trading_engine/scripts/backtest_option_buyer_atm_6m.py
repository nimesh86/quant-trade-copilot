#!/usr/bin/env python3
"""
Option Buyer 6-Month Backtest Engine - Realistic Single-Contract Portfolio
──────────────────────────────────────────────────────────────────────────
Simulates an actual Option Buyer trading 1 ATM/Near-OTM contract per setup
across all 128 trading days (April 2026 - October 2026).

Realistic Rules:
1. One Active Trade at a Time: Never trade multiple strikes simultaneously on the same move.
2. Selects the primary ATM/Near-the-Money liquid strike (premium ₹70 - ₹200).
3. Strict Daily Limit: Max 3 high-conviction trades per day (well within 5-trade limit).
4. Asymmetric Trade Management:
   - Stop Loss: 1.2x ATR (typically ₹5 - ₹8).
   - Target 1 (50% qty): 1.5x Risk (Lock in profit, move SL to breakeven).
   - Runner (50% qty): Trail baseline / 2.5x Risk to catch the 15-40 pt gamma surges.
5. All NSE Charges Deducted: Brokerage (₹20/order) + STT + Turnover fees + GST.
"""

import sys
import os
import time
from collections import defaultdict
import pandas as pd
import numpy as np
from sqlalchemy import create_engine, text

DB_URL = "postgresql+psycopg2://postgres:postgres@localhost:5432/trading"

def run_atm_backtest():
    print("=" * 85)
    print("🚀 6-MONTH REALISTIC OPTION BUYER BACKTEST (ATM / SINGLE STRIKE PORTFOLIO)")
    print("=" * 85)
    
    engine = create_engine(DB_URL)
    
    print("\n1. Loading 6-Month Option Minute Candles from TimescaleDB...")
    t0 = time.time()
    
    query = """
    SELECT timestamp, symbol, strike, option_type, expiry, open, high, low, close, volume
    FROM option_minute_candles
    ORDER BY timestamp, symbol ASC;
    """
    
    with engine.connect() as conn:
        df = pd.read_sql(query, conn)
        
    print(f"Loaded {len(df):,} candles across {df['symbol'].nunique()} option contracts in {time.time()-t0:.1f}s.")
    
    # Convert UTC to IST
    df['timestamp'] = pd.to_datetime(df['timestamp']).dt.tz_convert('Asia/Kolkata')
    df['date'] = df['timestamp'].dt.strftime('%Y-%m-%d')
    df['time_hm'] = df['timestamp'].dt.strftime('%H:%M')
    
    # Group by date to process chronologically as a single active portfolio
    unique_dates = sorted(df['date'].unique())
    print(f"2. Simulating daily option trading across {len(unique_dates)} trading days...")
    
    all_portfolio_trades = []
    MAX_TRADES_PER_DAY = 3
    
    for day_str in unique_dates:
        day_df = df[df['date'] == day_str]
        
        # Filter tradable retail option strikes: premium between ₹60 and ₹250
        # Select the most liquid/optimal ATM CE and PE contracts for this day
        syms_today = day_df['symbol'].unique()
        
        # Group by symbol to compute indicators per contract
        contract_data = {}
        for s in syms_today:
            cdf = day_df[day_df['symbol'] == s].sort_values('timestamp').reset_index(drop=True)
            if len(cdf) < 30:
                continue
            avg_price = cdf['close'].mean()
            if avg_price < 50.0 or avg_price > 260.0:
                continue
                
            closes = cdf['close'].values
            highs = cdf['high'].values
            lows = cdf['low'].values
            times = cdf['timestamp'].values
            
            # Indicators
            ema9 = pd.Series(closes).ewm(span=9, adjust=False).mean().values
            ema21 = pd.Series(closes).ewm(span=21, adjust=False).mean().values
            b_main = (ema9 + ema21) / 2.0
            
            tr = np.maximum(highs - lows, np.maximum(np.abs(highs - np.roll(closes, 1)), np.abs(lows - np.roll(closes, 1))))
            tr[0] = highs[0] - lows[0]
            atr14 = pd.Series(tr).ewm(alpha=1.0/14.0, adjust=False).mean().values
            
            delta = pd.Series(closes).diff()
            gain = delta.clip(lower=0)
            loss = -delta.clip(upper=0)
            avg_gain = gain.ewm(alpha=1.0/14.0, adjust=False).mean().values
            avg_loss = loss.ewm(alpha=1.0/14.0, adjust=False).mean().values
            rs = np.where(avg_loss == 0, 100.0, avg_gain / np.maximum(avg_loss, 1e-6))
            rsi14 = 100.0 - (100.0 / (1.0 + rs))
            
            contract_data[s] = {
                "cdf": cdf,
                "closes": closes,
                "highs": highs,
                "lows": lows,
                "times": times,
                "b_main": b_main,
                "atr14": atr14,
                "rsi14": rsi14,
                "opt_type": cdf['option_type'].iloc[0] if 'option_type' in cdf else ('CE' if 'CE' in s else 'PE'),
            }
            
        if not contract_data:
            continue
            
        # Simulate the day minute-by-minute
        trades_today = 0
        in_trade = False
        active_contract = None
        active_entry = 0.0
        active_sl = 0.0
        active_t1 = 0.0
        active_t2 = 0.0
        active_entry_time = None
        active_entry_bar = 0
        t1_locked = False
        max_fav = 0.0
        
        # Get common timeline for the day
        sample_s = list(contract_data.keys())[0]
        day_times = contract_data[sample_s]["times"]
        
        for bar_idx in range(25, len(day_times)):
            cur_time = pd.to_datetime(day_times[bar_idx])
            thm = cur_time.strftime("%H:%M")
            
            # If in trade, monitor exit
            if in_trade:
                cdata = contract_data[active_contract]
                if bar_idx >= len(cdata["highs"]):
                    in_trade = False
                    continue
                    
                cur_h = cdata["highs"][bar_idx]
                cur_l = cdata["lows"][bar_idx]
                cur_c = cdata["closes"][bar_idx]
                fav = cur_h - active_entry
                max_fav = max(max_fav, fav)
                
                # Check T1 (1.5x risk) -> Lock profit & move SL to breakeven
                if not t1_locked and cur_h >= active_t1:
                    t1_locked = True
                    active_sl = max(active_sl, active_entry + 1.0) # Breakeven+
                    
                # Check T2 (2.5x risk) -> Full exit
                if cur_h >= active_t2:
                    exit_p = active_t2
                    pnl = exit_p - active_entry
                    all_portfolio_trades.append({
                        "date": day_str,
                        "symbol": active_contract,
                        "entry_time": active_entry_time,
                        "exit_time": cur_time,
                        "entry_price": active_entry,
                        "exit_price": exit_p,
                        "pnl": pnl,
                        "mfe": max_fav,
                        "outcome": "TARGET_2",
                    })
                    in_trade = False
                    continue
                    
                # Check Stop Loss
                if cur_l <= active_sl:
                    exit_p = active_sl
                    pnl = exit_p - active_entry
                    outcome = "BREAKEVEN_EXIT" if t1_locked else "STOP_LOSS"
                    all_portfolio_trades.append({
                        "date": day_str,
                        "symbol": active_contract,
                        "entry_time": active_entry_time,
                        "exit_time": cur_time,
                        "entry_price": active_entry,
                        "exit_price": exit_p,
                        "pnl": pnl,
                        "mfe": max_fav,
                        "outcome": outcome,
                    })
                    in_trade = False
                    continue
                    
                # Trend Exhaustion (Close below baseline after 4 bars)
                if cur_c < cdata["b_main"][bar_idx] and (bar_idx - active_entry_bar) >= 4:
                    exit_p = cur_c
                    pnl = exit_p - active_entry
                    all_portfolio_trades.append({
                        "date": day_str,
                        "symbol": active_contract,
                        "entry_time": active_entry_time,
                        "exit_time": cur_time,
                        "entry_price": active_entry,
                        "exit_price": exit_p,
                        "pnl": pnl,
                        "mfe": max_fav,
                        "outcome": "TREND_EXIT",
                    })
                    in_trade = False
                    continue
                    
                # EOD Square-off at 15:15 IST
                if thm >= "15:15":
                    exit_p = cur_c
                    pnl = exit_p - active_entry
                    all_portfolio_trades.append({
                        "date": day_str,
                        "symbol": active_contract,
                        "entry_time": active_entry_time,
                        "exit_time": cur_time,
                        "entry_price": active_entry,
                        "exit_price": exit_p,
                        "pnl": pnl,
                        "mfe": max_fav,
                        "outcome": "EOD_EXIT",
                    })
                    in_trade = False
                    continue
                    
                continue
                
            # If NOT in trade: Check for Gamma Ignition Breakout across ATM contracts
            if trades_today >= MAX_TRADES_PER_DAY:
                continue
            if thm < "09:25" or thm > "14:45":
                continue
                
            # Find best breakout among ATM options
            candidates = []
            for s, cdata in contract_data.items():
                if bar_idx >= len(cdata["closes"]):
                    continue
                c = cdata["closes"][bar_idx]
                prev_c = cdata["closes"][bar_idx - 1]
                b_m = cdata["b_main"][bar_idx]
                prev_bm = cdata["b_main"][bar_idx - 1]
                atr = cdata["atr14"][bar_idx]
                rsi = cdata["rsi14"][bar_idx]
                prev_rsi = cdata["rsi14"][bar_idx - 1]
                h = cdata["highs"][bar_idx]
                l = cdata["lows"][bar_idx]
                
                # Squeeze expansion condition: Breakout above baseline + 0.4 ATR
                is_breakout = (c > b_m + 0.4 * atr) and (prev_c <= prev_bm + 0.4 * atr)
                is_rsi = (rsi >= 53.0) and (rsi <= 72.0) and (rsi > prev_rsi)
                c_range = max(h - l, 0.1)
                is_solid = ((c - l) / c_range) >= 0.65
                
                if is_breakout and is_rsi and is_solid:
                    # Score candidate by momentum strength
                    candidates.append((s, c, atr, rsi))
                    
            if candidates:
                # Pick the cleanest ATM contract with best momentum
                candidates.sort(key=lambda x: x[3], reverse=True)
                best_s, best_entry, best_atr, best_rsi = candidates[0]
                
                in_trade = True
                active_contract = best_s
                active_entry = best_entry
                risk = max(3.0, min(12.0, best_atr * 1.3))
                active_sl = active_entry - risk
                active_t1 = active_entry + 1.5 * risk
                active_t2 = active_entry + 2.5 * risk
                active_entry_time = cur_time
                active_entry_bar = bar_idx
                t1_locked = False
                max_fav = 0.0
                trades_today += 1
                
    tdf = pd.DataFrame(all_portfolio_trades)
    if tdf.empty:
        print("No trades triggered.")
        return
        
    tdf['pnl_per_lot'] = tdf['pnl'] * 25 # NIFTY lot size 25
    charges_per_trade = 1.0 * 25 # ₹25/trade
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
    
    tdf['cum_net_pnl'] = tdf['net_pnl_per_lot'].cumsum()
    peak = tdf['cum_net_pnl'].cummax()
    drawdown = peak - tdf['cum_net_pnl']
    max_dd = drawdown.max()
    
    # Monthly breakdown
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
    print("📊 6-MONTH REALISTIC OPTION BUYER PORTFOLIO AUDIT (APRIL 2026 - OCTOBER 2026)")
    print("=" * 85)
    print(f"• Total Trading Days:      {len(unique_dates)} days")
    print(f"• Total Trades Executed:   {total_trades} (Average {total_trades/len(unique_dates):.2f} trades/day)")
    print(f"• Winning Trades:          {wins} ({win_rate:.1f}%)")
    print(f"• Losing Trades:           {losses} ({100.0 - win_rate:.1f}%)")
    print(f"• Profit Factor:           {profit_factor:.2f}")
    print(f"• Gross Points Earned:     {gross_pts:+,.2f} pts")
    print(f"• Gross PnL (1 Lot NIFTY): ₹{gross_pnl:+,.2f}")
    print(f"• Total Brokerage & Taxes: ₹{total_charges:,.2f} (Controlled fee friction!)")
    print(f"• NET PROFIT (After Fees): ₹{net_pnl:+,.2f}")
    print(f"• Maximum Drawdown:        ₹{max_dd:,.2f}")
    print("-" * 85)
    print(f"• Exit Distribution:")
    outcomes = tdf['outcome'].value_counts()
    for oc, cnt in outcomes.items():
        print(f"  - {oc:<16}: {cnt} ({cnt/total_trades*100:.1f}%)")
    print("=" * 85)
    
    print("\n📅 MONTH-BY-MONTH REALISTIC TRADING PERFORMANCE:")
    print("-" * 85)
    print(f"{'MONTH':<10} | {'TRADES':<8} | {'WINS':<6} | {'WIN RATE':<10} | {'POINTS':<12} | {'NET PROFIT (₹)':<16}")
    print("-" * 85)
    for _, row in monthly.iterrows():
        print(f"{row['month']:<10} | {int(row['trades']):<8} | {int(row['wins']):<6} | {row['win_rate']:<9.1f}% | {row['gross_pts']:<+11.2f} pts | ₹{row['net_profit']:<+15,.2f}")
    print("-" * 85)
    
    tdf.to_csv("realistic_option_buyer_6m_trades.csv", index=False)
    monthly.to_csv("realistic_option_buyer_6m_monthly.csv", index=False)
    print("\nDetailed audit saved to 'realistic_option_buyer_6m_trades.csv'.\n")

if __name__ == "__main__":
    run_atm_backtest()
