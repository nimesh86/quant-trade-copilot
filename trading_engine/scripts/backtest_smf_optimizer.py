#!/usr/bin/env python3
import pickle
import numpy as np
import pandas as pd
from trading_engine.scripts.backtest_smf_limit_order_6m import (
    calc_ema, calc_atr, calc_vsma, calc_rsi, calculate_smf_limit_and_targets
)

CACHE_FILE = "/Users/admin/Documents/Nimesh/Projects/Self Training Engine/trading_engine/scripts/cache_option_candles.pkl"

def main():
    print("Loading pre-cached option candles...")
    with open(CACHE_FILE, "rb") as f:
        df = pickle.load(f)
    unique_dates = sorted(df['date'].unique())

    print("Building indicator tables...")
    daily_contract_data = {}
    for (d, s), group in df.groupby(['date', 'symbol']):
        if len(group) < 35: continue
        avg_p = group['close'].mean()
        if avg_p < 55.0 or avg_p > 260.0: continue
        c_df = group.sort_values('timestamp')
        opens = c_df['open'].values
        highs = c_df['high'].values
        lows = c_df['low'].values
        closes = c_df['close'].values
        volumes = c_df['volume'].values.astype(float)
        times = c_df['timestamp'].values
        n = len(closes)

        fast_ema = calc_ema(closes, 9)
        slow_ema = calc_ema(closes, 21)
        b_main = (fast_ema + slow_ema) / 2.0
        atr = calc_atr(highs, lows, closes, 14)
        vsma = calc_vsma(volumes, 20)
        rsi = calc_rsi(closes, 14)

        mult, req_bars = 0.5, 2
        last_signal = np.zeros(n, dtype=int)
        prev_regime = 1 if closes[0] >= b_main[0] else -1
        last_signal[0] = prev_regime
        below_count, above_count = 0, 0
        for i in range(n):
            buf = atr[i] * mult
            if closes[i] < (b_main[i] - buf):
                below_count += 1; above_count = 0
            elif closes[i] > (b_main[i] + buf):
                above_count += 1; below_count = 0
            else:
                below_count = above_count = 0
            curr_ls = prev_regime
            if prev_regime == 1 and below_count >= req_bars:
                curr_ls = -1; below_count = 0
            elif prev_regime == -1 and above_count >= req_bars:
                curr_ls = 1; above_count = 0
            last_signal[i] = curr_ls
            prev_regime = curr_ls

        if d not in daily_contract_data: daily_contract_data[d] = {}
        daily_contract_data[d][s] = {
            'opens': opens, 'highs': highs, 'lows': lows, 'closes': closes,
            'volumes': volumes, 'times': times, 'b_main': b_main, 'atr': atr,
            'vsma': vsma, 'rsi': rsi, 'signals': last_signal
        }

    configs = [
        {'name': '1. Baseline Market Entry (Signal Close, No Limit)', 'mode': 'MARKET', 'sl_type': 'BMAIN', 'sl_mult': 0.5, 'tgt_r': 1.6, 'min_rvol': 0.0},
        {'name': '2. Suggested Limit (Current: SL bMain - 0.35 ATR, T1: 1.6R)', 'mode': 'LIMIT', 'sl_type': 'BMAIN', 'sl_mult': 0.35, 'tgt_r': 1.6, 'min_rvol': 0.0},
        {'name': '3. Suggested Limit (Buffer SL: bMain - 0.75 ATR, T1: 1.6R)', 'mode': 'LIMIT', 'sl_type': 'BMAIN', 'sl_mult': 0.75, 'tgt_r': 1.6, 'min_rvol': 0.0},
        {'name': '4. Suggested Limit (Swing Low SL: Low9, T1: 1.5R)', 'mode': 'LIMIT', 'sl_type': 'SWING', 'sl_mult': 0.0, 'tgt_r': 1.5, 'min_rvol': 0.0},
        {'name': '5. Suggested Limit (Swing Low SL + RVOL >= 1.0 Filter)', 'mode': 'LIMIT', 'sl_type': 'SWING', 'sl_mult': 0.0, 'tgt_r': 1.5, 'min_rvol': 1.0},
        {'name': '6. Suggested Limit (Swing Low SL + RVOL >= 1.25 Volume Surge)', 'mode': 'LIMIT', 'sl_type': 'SWING', 'sl_mult': 0.0, 'tgt_r': 1.5, 'min_rvol': 1.25},
    ]

    print("\n" + "=" * 90)
    print("PARAMETRIC STUDY: TESTING LIMIT ORDER REFINEMENTS OVER 128 DAYS (APR - OCT 2026)")
    print("=" * 90)

    for cfg in configs:
        trades = []
        for day_str in unique_dates:
            contracts_today = daily_contract_data.get(day_str, {})
            if not contracts_today: continue
            sample_s = list(contracts_today.keys())[0]
            day_times = contracts_today[sample_s]['times']
            n_bars = len(day_times)

            in_trade = False
            active_contract, active_entry, active_sl, active_t1 = None, 0.0, 0.0, 0.0
            trades_today = 0
            pending_order = None

            for bar_idx in range(5, n_bars):
                cur_time = pd.to_datetime(day_times[bar_idx])
                thm = cur_time.strftime('%H:%M')

                if not in_trade and pending_order is not None:
                    p_sym, p_lim, p_sl, p_t1, p_exp_bar = pending_order
                    cdata = contracts_today[p_sym]
                    if bar_idx < len(cdata['lows']):
                        if cdata['lows'][bar_idx] <= p_lim:
                            fill_p = min(p_lim, cdata['opens'][bar_idx])
                            in_trade = True
                            active_contract = p_sym
                            active_entry = fill_p
                            active_sl = p_sl
                            active_t1 = p_t1
                            pending_order = None
                            trades_today += 1
                        elif bar_idx >= p_exp_bar or cdata['signals'][bar_idx] == -1:
                            pending_order = None

                if in_trade:
                    cdata = contracts_today[active_contract]
                    if bar_idx >= len(cdata['highs']):
                        in_trade = False; continue
                    cur_l, cur_h, cur_c = cdata['lows'][bar_idx], cdata['highs'][bar_idx], cdata['closes'][bar_idx]
                    sig = cdata['signals'][bar_idx]
                    prev_sig = cdata['signals'][bar_idx - 1] if bar_idx > 0 else sig

                    if cur_l <= active_sl:
                        trades.append({'pnl': active_sl - active_entry, 'outcome': 'SL'})
                        in_trade = False; continue
                    if active_t1 > 0 and cur_h >= active_t1:
                        trades.append({'pnl': active_t1 - active_entry, 'outcome': 'TGT'})
                        in_trade = False; continue
                    if sig == -1 and prev_sig == 1:
                        trades.append({'pnl': cur_c - active_entry, 'outcome': 'REGIME'})
                        in_trade = False; continue
                    if thm >= '15:15':
                        trades.append({'pnl': cur_c - active_entry, 'outcome': 'EOD'})
                        in_trade = False; continue
                    continue

                if trades_today >= 5 or thm < '09:25' or thm > '14:45' or pending_order is not None:
                    continue

                candidates = []
                for s, cdata in contracts_today.items():
                    if bar_idx >= len(cdata['signals']): continue
                    if cdata['signals'][bar_idx] == 1 and (cdata['signals'][bar_idx - 1] if bar_idx > 0 else -1) == -1:
                        candidates.append((s, cdata['atr'][bar_idx]))
                if candidates:
                    candidates.sort(key=lambda x: x[1], reverse=True)
                    best_s = candidates[0][0]
                    cdata = contracts_today[best_s]
                    m = calculate_smf_limit_and_targets(
                        cdata['opens'], cdata['highs'], cdata['lows'], cdata['closes'],
                        cdata['volumes'], bar_idx, cdata['atr'], cdata['b_main'], cdata['vsma'], cdata['rsi']
                    )
                    if cfg['min_rvol'] > 0 and m['rvol'] < cfg['min_rvol']:
                        continue

                    if cfg['sl_type'] == 'SWING':
                        sl = round(min(m['low9'] - 0.5, cdata['closes'][bar_idx] - 0.75 * cdata['atr'][bar_idx]) * 20.0) / 20.0
                    else:
                        sl = round(max(cdata['b_main'][bar_idx] - cfg['sl_mult'] * cdata['atr'][bar_idx], cdata['closes'][bar_idx] - 1.5 * cdata['atr'][bar_idx]) * 20.0) / 20.0

                    risk = max(1.5, cdata['closes'][bar_idx] - sl)
                    t1 = round((cdata['closes'][bar_idx] + cfg['tgt_r'] * risk) * 20.0) / 20.0 if cfg['tgt_r'] > 0 else 0.0

                    if cfg['mode'] == 'MARKET':
                        in_trade = True
                        active_contract = best_s
                        active_entry = cdata['closes'][bar_idx]
                        active_sl = sl
                        active_t1 = 0.0 # Market baseline holds until regime flip/SL
                        trades_today += 1
                    else:
                        pending_order = (best_s, m['limit_price'], sl, t1, bar_idx + 3)

        tdf = pd.DataFrame(trades)
        if len(tdf) > 0:
            pts = tdf['pnl'].sum()
            wins = len(tdf[tdf['pnl'] > 0])
            losses = len(tdf[tdf['pnl'] < 0])
            wr = (wins / len(tdf)) * 100.0
            gross_rs = pts * 25.0
            fees = len(tdf) * 25.0
            net_rs = gross_rs - fees
            w_pts = tdf[tdf['pnl'] > 0]['pnl'].sum()
            l_pts = abs(tdf[tdf['pnl'] < 0]['pnl'].sum())
            pf = (w_pts / l_pts) if l_pts > 0 else 99.0
            print(f"{cfg['name']:<65} | Trades: {len(tdf):>3} | WinRate: {wr:>5.1f}% | PF: {pf:>4.2f} | Net PnL: ₹{net_rs:>9,.2f}")
        else:
            print(f"{cfg['name']:<65} | No trades")
    print("=" * 90)

if __name__ == "__main__":
    main()
