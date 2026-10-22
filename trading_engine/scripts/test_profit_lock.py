#!/usr/bin/env python3
"""
Test Profit-Locking Exit vs Letting Moves Evaporate
"""
import pandas as pd
import numpy as np
from sqlalchemy import create_engine
from collections import defaultdict
import time

DB_URL = "postgresql+psycopg2://postgres:postgres@localhost:5432/trading"

def run_profit_lock_test():
    engine = create_engine(DB_URL)
    df = pd.read_sql("SELECT timestamp, symbol, open, high, low, close, volume FROM option_minute_candles ORDER BY timestamp, symbol ASC;", engine)
    df['timestamp'] = pd.to_datetime(df['timestamp']).dt.tz_convert('Asia/Kolkata')
    df['date'] = df['timestamp'].dt.strftime('%Y-%m-%d')
    unique_dates = sorted(df['date'].unique())
    
    all_trades = []
    MAX_TRADES_PER_DAY = 3
    
    for day_str in unique_dates:
        day_df = df[df['date'] == day_str]
        syms_today = day_df['symbol'].unique()
        
        contract_data = {}
        for s in syms_today:
            cdf = day_df[day_df['symbol'] == s].sort_values('timestamp').reset_index(drop=True)
            if len(cdf) < 30: continue
            avg_p = cdf['close'].mean()
            if avg_p < 50.0 or avg_p > 260.0: continue
            
            closes = cdf['close'].values
            highs = cdf['high'].values
            lows = cdf['low'].values
            times = cdf['timestamp'].values
            
            ema9 = pd.Series(closes).ewm(span=9, adjust=False).mean().values
            ema21 = pd.Series(closes).ewm(span=21, adjust=False).mean().values
            b_main = (ema9 + ema21) / 2.0
            
            tr = np.maximum(highs - lows, np.maximum(np.abs(highs - np.roll(closes, 1)), np.abs(lows - np.roll(closes, 1))))
            tr[0] = highs[0] - lows[0]
            atr14 = pd.Series(tr).ewm(alpha=1.0/14.0, adjust=False).mean().values
            
            delta = pd.Series(closes).diff()
            gain = delta.clip(lower=0)
            loss = -delta.clip(upper=0)
            avg_g = gain.ewm(alpha=1.0/14.0, adjust=False).mean().values
            avg_l = loss.ewm(alpha=1.0/14.0, adjust=False).mean().values
            rs = np.where(avg_l == 0, 100.0, avg_g / np.maximum(avg_l, 1e-6))
            rsi14 = 100.0 - (100.0 / (1.0 + rs))
            
            contract_data[s] = {
                "closes": closes, "highs": highs, "lows": lows, "times": times,
                "b_main": b_main, "atr14": atr14, "rsi14": rsi14,
            }
            
        if not contract_data: continue
        
        trades_today = 0
        in_trade = False
        active_contract = None
        active_entry = 0.0
        active_sl = 0.0
        active_target = 0.0
        active_entry_time = None
        active_entry_bar = 0
        max_fav = 0.0
        
        sample_s = list(contract_data.keys())[0]
        day_times = contract_data[sample_s]["times"]
        
        for bar_idx in range(25, len(day_times)):
            cur_time = pd.to_datetime(day_times[bar_idx])
            thm = cur_time.strftime("%H:%M")
            
            if in_trade:
                cdata = contract_data[active_contract]
                if bar_idx >= len(cdata["highs"]):
                    in_trade = False; continue
                    
                cur_h = cdata["highs"][bar_idx]
                cur_l = cdata["lows"][bar_idx]
                cur_c = cdata["closes"][bar_idx]
                fav = cur_h - active_entry
                max_fav = max(max_fav, fav)
                
                # Exit 1: Target Hit (1.2x Risk) -> BANK PROFIT!
                if cur_h >= active_target:
                    exit_p = active_target
                    pnl = exit_p - active_entry
                    all_trades.append({
                        "date": day_str, "symbol": active_contract, "pnl": pnl, "outcome": "TARGET_HIT", "mfe": max_fav
                    })
                    in_trade = False; continue
                    
                # Exit 2: Stop Loss
                if cur_l <= active_sl:
                    exit_p = active_sl
                    pnl = exit_p - active_entry
                    all_trades.append({
                        "date": day_str, "symbol": active_contract, "pnl": pnl, "outcome": "STOP_LOSS", "mfe": max_fav
                    })
                    in_trade = False; continue
                    
                # Exit 3: Trend Exhaustion
                if cur_c < cdata["b_main"][bar_idx] - 0.2 * cdata["atr14"][bar_idx] and (bar_idx - active_entry_bar) >= 3:
                    exit_p = cur_c
                    pnl = exit_p - active_entry
                    all_trades.append({
                        "date": day_str, "symbol": active_contract, "pnl": pnl, "outcome": "TREND_EXIT", "mfe": max_fav
                    })
                    in_trade = False; continue
                    
                if thm >= "15:15":
                    exit_p = cur_c
                    pnl = exit_p - active_entry
                    all_trades.append({
                        "date": day_str, "symbol": active_contract, "pnl": pnl, "outcome": "EOD_EXIT", "mfe": max_fav
                    })
                    in_trade = False; continue
                continue
                
            if trades_today >= MAX_TRADES_PER_DAY: continue
            if thm < "09:25" or thm > "14:45": continue
            
            candidates = []
            for s, cdata in contract_data.items():
                if bar_idx >= len(cdata["closes"]): continue
                c = cdata["closes"][bar_idx]
                prev_c = cdata["closes"][bar_idx - 1]
                b_m = cdata["b_main"][bar_idx]
                prev_bm = cdata["b_main"][bar_idx - 1]
                atr = cdata["atr14"][bar_idx]
                rsi = cdata["rsi14"][bar_idx]
                prev_rsi = cdata["rsi14"][bar_idx - 1]
                h = cdata["highs"][bar_idx]
                l = cdata["lows"][bar_idx]
                
                is_breakout = (c > b_m + 0.4 * atr) and (prev_c <= prev_bm + 0.4 * atr)
                is_rsi = (rsi >= 53.0) and (rsi <= 70.0) and (rsi > prev_rsi)
                c_range = max(h - l, 0.1)
                is_solid = ((c - l) / c_range) >= 0.65
                
                if is_breakout and is_rsi and is_solid:
                    candidates.append((s, c, atr, rsi))
                    
            if candidates:
                candidates.sort(key=lambda x: x[3], reverse=True)
                best_s, best_entry, best_atr, best_rsi = candidates[0]
                in_trade = True
                active_contract = best_s
                active_entry = best_entry
                risk = max(3.0, min(10.0, best_atr * 1.2))
                active_sl = active_entry - risk
                active_target = active_entry + 1.3 * risk
                active_entry_time = cur_time
                active_entry_bar = bar_idx
                max_fav = 0.0
                trades_today += 1
                
    tdf = pd.DataFrame(all_trades)
    tdf['pnl_per_lot'] = tdf['pnl'] * 25
    tdf['net_pnl_per_lot'] = tdf['pnl_per_lot'] - 25.0
    
    total = len(tdf)
    wins = len(tdf[tdf['pnl'] > 0])
    gross_pts = tdf['pnl'].sum()
    gross_pnl = tdf['pnl_per_lot'].sum()
    net_pnl = tdf['net_pnl_per_lot'].sum()
    
    print("\n" + "=" * 60)
    print("PROFIT-LOCKING TARGET RESULTS (128 DAYS):")
    print("=" * 60)
    print(f"Total Trades:      {total} ({total/128:.1f} trades/day)")
    print(f"Win Rate:          {wins}/{total} ({wins/total*100:.1f}%)")
    print(f"Gross Points:      {gross_pts:+.2f} pts")
    print(f"Gross PnL (1 Lot): ₹{gross_pnl:+,.2f}")
    print(f"Net Profit (Fees): ₹{net_pnl:+,.2f}")
    print(f"Target Hits:       {len(tdf[tdf['outcome'] == 'TARGET_HIT'])} ({len(tdf[tdf['outcome'] == 'TARGET_HIT'])/total*100:.1f}%)")
    print(f"Stop Loss Hits:    {len(tdf[tdf['outcome'] == 'STOP_LOSS'])} ({len(tdf[tdf['outcome'] == 'STOP_LOSS'])/total*100:.1f}%)")
    print("=" * 60)

if __name__ == "__main__":
    run_profit_lock_test()
