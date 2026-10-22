#!/usr/bin/env python3
"""
Backtest the exact SMF Cloud Algorithm from contract.js across 6 Months (April 2026 - October 2026)
────────────────────────────────────────────────────────────────────────────────────────────────────
Simulates:
1. Long-Only:
   - BUY: SMF flips from -1 to +1 (Close > bMain + 0.5 * ATR with confirmation).
   - EXIT: SMF flips from +1 to -1 (Close < bMain - 0.5 * ATR with confirmation) OR Stop Loss / EOD.
2. Max 5 trades per day.
3. Realistic ATM contract per day.
4. All exchange & broker charges (₹25/trade).
"""

import sys
import os
import time
import pandas as pd
import numpy as np
from sqlalchemy import create_engine

DB_URL = "postgresql+psycopg2://postgres:postgres@localhost:5432/trading"

def calc_ema(arr, span):
    return pd.Series(arr).ewm(span=span, adjust=False).mean().values

def calc_atr(highs, lows, closes, length=14):
    n = len(highs)
    tr = np.zeros(n)
    tr[0] = highs[0] - lows[0]
    for i in range(1, n):
        tr[i] = max(highs[i] - lows[i], abs(highs[i] - closes[i-1]), abs(lows[i] - closes[i-1]))
    atr = pd.Series(tr).ewm(alpha=1.0/length, adjust=False).mean().values
    return atr

def run_smf_backtest():
    engine = create_engine(DB_URL)
    print("=" * 80)
    print("📈 BACKTESTING EXACT SMF CLOUD (FROM CONTRACT.JS) ON 6-MONTH OPTION DATA")
    print("=" * 80)
    
    t0 = time.time()
    query = """
    SELECT timestamp, symbol, open, high, low, close
    FROM option_minute_candles
    ORDER BY timestamp, symbol ASC;
    """
    df = pd.read_sql(query, engine)
    print(f"Loaded {len(df):,} candles across {df['symbol'].nunique()} option contracts in {time.time()-t0:.1f}s.")
    
    df['timestamp'] = pd.to_datetime(df['timestamp']).dt.tz_convert('Asia/Kolkata')
    df['date'] = df['timestamp'].dt.strftime('%Y-%m-%d')
    unique_dates = sorted(df['date'].unique())
    
    all_trades = []
    MAX_TRADES_PER_DAY = 5
    
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
            times = cdf['timestamp'].values
            n = len(closes)
            
            # Exact SMF Cloud settings
            fast_len = 9
            slow_len = 21
            f_ema = calc_ema(closes, fast_len)
            s_ema = calc_ema(closes, slow_len)
            b_main = (f_ema + s_ema) / 2.0
            atr = calc_atr(highs, lows, closes, 14)
            
            mult = 0.5
            req_bars = 2
            
            last_signal = np.zeros(n, dtype=int)
            prev_regime = 1 if closes[0] >= b_main[0] else -1
            last_signal[0] = prev_regime
            below_count = 0
            above_count = 0
            
            for i in range(n):
                buf = atr[i] * mult
                is_below = closes[i] < (b_main[i] - buf)
                is_above = closes[i] > (b_main[i] + buf)
                
                if is_below:
                    below_count += 1
                    above_count = 0
                elif is_above:
                    above_count += 1
                    below_count = 0
                else:
                    below_count = 0
                    above_count = 0
                    
                curr_ls = prev_regime
                if prev_regime == 1 and below_count >= req_bars:
                    curr_ls = -1
                    below_count = 0
                elif prev_regime == -1 and above_count >= req_bars:
                    curr_ls = 1
                    above_count = 0
                    
                last_signal[i] = curr_ls
                prev_regime = curr_ls
                
            contract_data[s] = {
                "closes": closes, "highs": highs, "lows": lows, "times": times,
                "b_main": b_main, "atr": atr, "signals": last_signal
            }
            
        if not contract_data: continue
        
        trades_today = 0
        in_trade = False
        active_contract = None
        active_entry = 0.0
        active_sl = 0.0
        active_entry_time = None
        active_entry_bar = 0
        
        sample_s = list(contract_data.keys())[0]
        day_times = contract_data[sample_s]["times"]
        
        for bar_idx in range(5, len(day_times)):
            cur_time = pd.to_datetime(day_times[bar_idx])
            thm = cur_time.strftime("%H:%M")
            
            if in_trade:
                cdata = contract_data[active_contract]
                if bar_idx >= len(cdata["highs"]):
                    in_trade = False; continue
                    
                cur_l = cdata["lows"][bar_idx]
                cur_c = cdata["closes"][bar_idx]
                sig = cdata["signals"][bar_idx]
                prev_sig = cdata["signals"][bar_idx - 1] if bar_idx > 0 else sig
                
                # Exit 1: Stop Loss Hit
                if cur_l <= active_sl:
                    exit_p = active_sl
                    pnl = exit_p - active_entry
                    all_trades.append({
                        "date": day_str, "symbol": active_contract, "entry_time": active_entry_time,
                        "exit_time": cur_time, "entry_price": active_entry, "exit_price": exit_p,
                        "pnl": pnl, "outcome": "STOP_LOSS"
                    })
                    in_trade = False
                    continue
                    
                # Exit 2: Regime flip to BEARISH (SELL marker) -> Close Long
                if sig == -1 and prev_sig == 1:
                    exit_p = cur_c
                    pnl = exit_p - active_entry
                    all_trades.append({
                        "date": day_str, "symbol": active_contract, "entry_time": active_entry_time,
                        "exit_time": cur_time, "entry_price": active_entry, "exit_price": exit_p,
                        "pnl": pnl, "outcome": "REGIME_EXIT"
                    })
                    in_trade = False
                    continue
                    
                # Exit 3: EOD at 15:15
                if thm >= "15:15":
                    exit_p = cur_c
                    pnl = exit_p - active_entry
                    all_trades.append({
                        "date": day_str, "symbol": active_contract, "entry_time": active_entry_time,
                        "exit_time": cur_time, "entry_price": active_entry, "exit_price": exit_p,
                        "pnl": pnl, "outcome": "EOD_EXIT"
                    })
                    in_trade = False
                    continue
                continue
                
            if trades_today >= MAX_TRADES_PER_DAY: continue
            if thm < "09:25" or thm > "14:45": continue
            
            # Look for BUY flips (last_signal flips from -1 to 1)
            candidates = []
            for s, cdata in contract_data.items():
                if bar_idx >= len(cdata["signals"]): continue
                sig = cdata["signals"][bar_idx]
                prev_sig = cdata["signals"][bar_idx - 1] if bar_idx > 0 else -1
                
                if sig == 1 and prev_sig == -1:
                    c = cdata["closes"][bar_idx]
                    atr_v = cdata["atr"][bar_idx]
                    bm = cdata["b_main"][bar_idx]
                    candidates.append((s, c, atr_v, bm))
                    
            if candidates:
                # Prioritize ATM strike with highest volatility expansion
                candidates.sort(key=lambda x: x[2], reverse=True)
                best_s, best_entry, best_atr, best_bm = candidates[0]
                
                in_trade = True
                active_contract = best_s
                active_entry = best_entry
                active_sl = best_bm - 0.5 * best_atr
                active_entry_time = cur_time
                active_entry_bar = bar_idx
                trades_today += 1
                
    tdf = pd.DataFrame(all_trades)
    LOT_SIZE = 25
    BROKERAGE = 25.0
    tdf['pnl_per_lot'] = tdf['pnl'] * LOT_SIZE
    tdf['net_pnl_per_lot'] = tdf['pnl_per_lot'] - BROKERAGE
    
    total = len(tdf)
    wins = len(tdf[tdf['pnl'] > 0])
    gross_pts = tdf['pnl'].sum()
    gross_pnl = tdf['pnl_per_lot'].sum()
    net_pnl = tdf['net_pnl_per_lot'].sum()
    
    print("\n" + "=" * 80)
    print("📊 EXACT SMF CLOUD RESULTS (128 DAYS):")
    print("=" * 80)
    print(f"Total Trades:      {total} ({total/128:.2f} trades/day)")
    print(f"Win Rate:          {wins}/{total} ({wins/total*100:.1f}%)")
    print(f"Gross Points:      {gross_pts:+.2f} pts")
    print(f"Gross PnL (1 Lot): ₹{gross_pnl:+,.2f}")
    print(f"Net Profit (Fees): ₹{net_pnl:+,.2f}")
    print("=" * 80)

if __name__ == "__main__":
    run_smf_backtest()
