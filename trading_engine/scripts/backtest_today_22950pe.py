#!/usr/bin/env python3
"""
Backtest today's session (2026-10-19) for NIFTY 22950 PE
────────────────────────────────────────────────────────
Analyzes:
1. NIFTY26O1922950PE (0DTE Weekly Expiry today)
2. NIFTY26OCT22950PE (Monthly / Next Weekly Expiry 27-Oct)

Simulates:
- SMF Cloud signals on 1m and 3m
- Market Order vs Suggested Limit Price Order
- Trade entry times, fill prices, targets (T1 Scalp +8, T2 Runner +20), Stop Loss, and net PnL (points & INR per lot)
"""

import sys
import pandas as pd
import numpy as np
from sqlalchemy import create_engine

DB_URL = "postgresql://postgres:postgres@localhost:5432/trading"

def calc_ema(arr, span):
    k = 2.0 / (span + 1.0)
    ema = np.zeros(len(arr))
    ema[0] = arr[0]
    for i in range(1, len(arr)):
        ema[i] = arr[i] * k + ema[i-1] * (1.0 - k)
    return ema

def calc_atr(highs, lows, closes, length=14):
    n = len(highs)
    atr = np.zeros(n)
    tr = np.zeros(n)
    tr[0] = highs[0] - lows[0]
    atr[0] = tr[0]
    for i in range(1, n):
        tr[i] = max(highs[i] - lows[i], abs(highs[i] - closes[i-1]), abs(lows[i] - closes[i-1]))
        atr[i] = (atr[i-1] * (length - 1) + tr[i]) / length
    return atr

def calc_vsma(volumes, length=20):
    n = len(volumes)
    vsma = np.zeros(n)
    for i in range(n):
        s = max(0, i - length + 1)
        vsma[i] = np.mean(volumes[s:i+1])
    return vsma

def calc_rsi(closes, length=14):
    n = len(closes)
    rsi = np.full(n, 50.0)
    if n <= length:
        return rsi
    gains = np.zeros(n)
    losses = np.zeros(n)
    for i in range(1, n):
        d = closes[i] - closes[i-1]
        if d >= 0:
            gains[i] = d
        else:
            losses[i] = -d
    ag = np.mean(gains[1:length+1])
    al = np.mean(losses[1:length+1])
    rsi[length] = 100.0 if al == 0 else 100.0 - (100.0 / (1.0 + (ag / al)))
    for i in range(length+1, n):
        ag = (ag * (length - 1) + gains[i]) / length
        al = (al * (length - 1) + losses[i]) / length
        rsi[i] = 100.0 if al == 0 else 100.0 - (100.0 / (1.0 + (ag / al)))
    return rsi

def compute_smf_series(df):
    closes = df['close'].values
    highs = df['high'].values
    lows = df['low'].values
    opens = df['open'].values
    volumes = df['volume'].values
    n = len(df)

    # SMF Cloud core: EMA 9, 21, 50, 200
    fast_ma = calc_ema(closes, 9)
    slow_ma = calc_ema(closes, 21)
    b_main = (fast_ma + slow_ma) / 2.0
    atr = calc_atr(highs, lows, closes, 14)
    upper = b_main + 1.25 * atr
    lower = b_main - 1.25 * atr
    vsma = calc_vsma(volumes, 20)
    rsi = calc_rsi(closes, 14)

    # Regime & Signal
    # Bullish regime = fast_ma > slow_ma AND close > b_main
    regime = np.zeros(n, dtype=int)
    for i in range(n):
        if fast_ma[i] > slow_ma[i] and closes[i] >= b_main[i]:
            regime[i] = 1
        elif fast_ma[i] < slow_ma[i] and closes[i] <= b_main[i]:
            regime[i] = -1
        else:
            regime[i] = regime[i-1] if i > 0 else 0

    # Signals: Buy on regime transition 0/-1 -> 1
    # or continuation bounce at Cloud
    sig = np.zeros(n, dtype=int)
    for i in range(1, n):
        if regime[i] == 1 and regime[i-1] != 1:
            sig[i] = 1 # BUY
        elif regime[i] == -1 and regime[i-1] != -1:
            sig[i] = -1 # SELL / EXIT

    return {
        "opens": opens, "highs": highs, "lows": lows, "closes": closes,
        "volumes": volumes, "fast_ma": fast_ma, "slow_ma": slow_ma,
        "b_main": b_main, "upper": upper, "lower": lower,
        "atr": atr, "vsma": vsma, "rsi": rsi, "regime": regime, "sig": sig
    }

def run_backtest_for_symbol(symbol, target_date="2026-10-19"):
    engine = create_engine(DB_URL)
    df = pd.DataFrame()
    for tbl in ["minute_candles", "option_minute_candles"]:
        query = f"""
        SELECT timestamp, symbol, open, high, low, close, volume
        FROM {tbl}
        WHERE symbol = '{symbol}'
          AND (timestamp AT TIME ZONE 'Asia/Kolkata')::date = '{target_date}'
        ORDER BY timestamp ASC;
        """
        try:
            res = pd.read_sql(query, engine)
            if len(res) > 0:
                df = res
                break
        except Exception:
            pass

    if len(df) == 0:
        # Try fetching without date filter to see available dates
        query_all = f"""
        SELECT min((timestamp AT TIME ZONE 'Asia/Kolkata')::date) as min_d,
               max((timestamp AT TIME ZONE 'Asia/Kolkata')::date) as max_d,
               count(*) as cnt
        FROM minute_candles WHERE symbol = '{symbol}';
        """
        info = pd.read_sql(query_all, engine)
        print(f"❌ No candles found for {symbol} on {target_date}. Available in minute_candles: {info.to_dict('records')}")
        return

    df['timestamp'] = pd.to_datetime(df['timestamp']).dt.tz_convert('Asia/Kolkata')
    df = df.reset_index(drop=True)
    n = len(df)
    print(f"\n======================================================================")
    print(f"  BACKTEST FOR {symbol} ON {target_date}")
    print(f"  Total 1-min Candles: {n} (from {df['timestamp'].iloc[0].strftime('%H:%M')} to {df['timestamp'].iloc[-1].strftime('%H:%M')})")
    print(f"  Session Open: ₹{df['open'].iloc[0]:.2f} | High: ₹{df['high'].max():.2f} | Low: ₹{df['low'].min():.2f} | Close: ₹{df['close'].iloc[-1]:.2f}")
    print(f"======================================================================\n")

    smf = compute_smf_series(df)
    opens = smf["opens"]
    highs = smf["highs"]
    lows = smf["lows"]
    closes = smf["closes"]
    volumes = smf["volumes"]
    b_main = smf["b_main"]
    atr = smf["atr"]
    vsma = smf["vsma"]
    rsi = smf["rsi"]
    regime = smf["regime"]
    sig = smf["sig"]
    times = [t.strftime('%H:%M') for t in df['timestamp']]

    # Simulate Trades:
    # Mode 1: Market Order at Signal Close
    # Mode 2: Suggested Limit Price with T1 Scalp (+8 pts) & T2 Runner (+20 pts) & BE Lock (+6 pts)

    print("--- ALL SIGNALS GENERATED TODAY ---")
    signals_count = 0
    for i in range(n):
        if sig[i] == 1:
            signals_count += 1
            cur_c = closes[i]
            cur_atr = atr[i] if atr[i] > 0 else 2.0
            cur_bm = b_main[i]
            # Suggested Limit
            s5 = max(0, i - 4)
            rec_low = np.min(lows[s5:i+1])
            suggested_limit = max(cur_bm, rec_low, cur_c - 0.40 * cur_atr)
            suggested_limit = round(min(suggested_limit, cur_c) * 20.0) / 20.0
            
            # SL capped ₹4-₹10
            raw_sl = max(cur_bm - 0.35 * cur_atr, cur_c - 1.5 * cur_atr)
            risk = cur_c - raw_sl
            risk = min(max(risk, 4.0), 10.0)
            sl = round((cur_c - risk) * 20.0) / 20.0
            t1 = round((cur_c + 8.0) * 20.0) / 20.0
            be_lock = round((cur_c + 6.0) * 20.0) / 20.0
            t2 = round((cur_c + 20.0) * 20.0) / 20.0

            print(f"[{signals_count}] {times[i]} IST | Signal: BUY | Close: ₹{cur_c:.2f} | Sug. Limit: ₹{suggested_limit:.2f} | SL: ₹{sl:.2f} | T1: ₹{t1:.2f} | T2: ₹{t2:.2f} | RSI: {rsi[i]:.1f} | ATR: {cur_atr:.2f}")

    if signals_count == 0:
        print("  No BUY signals were triggered on 1m chart today.")
        return

    # Simulate Strategy 1: Market Order Entry
    print("\n--- SIMULATION 1: MARKET ORDER ENTRY (Fixed SL = 8 pts, Target = +16 pts) ---")
    trades_mkt = []
    in_pos = False
    entry_p = 0.0
    entry_t = ""
    entry_i = 0
    sl_p = 0.0
    t_p = 0.0

    for i in range(n):
        if not in_pos and sig[i] == 1:
            in_pos = True
            entry_p = closes[i]
            entry_t = times[i]
            entry_i = i
            sl_p = entry_p - min(max(entry_p - (b_main[i] - 0.35 * atr[i]), 4.0), 10.0)
            t_p = entry_p + 16.0
            continue

        if in_pos:
            # Check SL or Target in candle i
            if lows[i] <= sl_p:
                pnl = sl_p - entry_p
                trades_mkt.append({"entry_t": entry_t, "exit_t": times[i], "entry_p": entry_p, "exit_p": sl_p, "pnl": pnl, "exit_reason": "SL_HIT"})
                in_pos = False
            elif highs[i] >= t_p:
                pnl = t_p - entry_p
                trades_mkt.append({"entry_t": entry_t, "exit_t": times[i], "entry_p": entry_p, "exit_p": t_p, "pnl": pnl, "exit_reason": "TARGET_HIT"})
                in_pos = False
            elif sig[i] == -1 or i == n - 1 or times[i] >= "15:20":
                pnl = closes[i] - entry_p
                trades_mkt.append({"entry_t": entry_t, "exit_t": times[i], "entry_p": entry_p, "exit_p": closes[i], "pnl": pnl, "exit_reason": "REGIME_OR_EOD"})
                in_pos = False

    for idx, t in enumerate(trades_mkt, 1):
        pnl_inr = t['pnl'] * 25 # 25 qty lot size for NIFTY
        res = "✅ PROFIT" if t['pnl'] > 0 else "❌ LOSS"
        print(f"  Trade {idx}: Entry {t['entry_t']} @ ₹{t['entry_p']:.2f} -> Exit {t['exit_t']} @ ₹{t['exit_p']:.2f} | PnL: {t['pnl']:+.2f} pts ({pnl_inr:+,.0f} INR) [{t['exit_reason']}] {res}")

    total_mkt_pts = sum(t['pnl'] for t in trades_mkt)
    print(f"  TOTAL MARKET PnL: {total_mkt_pts:+.2f} pts | ₹{total_mkt_pts * 25:+,.0f} per lot (Trades: {len(trades_mkt)})")

    # Simulate Strategy 2: Suggested Limit Entry + Scalp T1 (+8) & Runner T2 (+20) + BE Lock (+6)
    print("\n--- SIMULATION 2: SUGGESTED LIMIT ORDER + HYBRID SCALP/RUNNER (50% T1 @ +8, 50% T2 @ +20, BE Lock @ +6) ---")
    trades_lim = []
    in_pos = False
    pending_limit = None

    for i in range(n):
        # Place limit if signal
        if not in_pos and pending_limit is None and sig[i] == 1:
            cur_c = closes[i]
            cur_atr = atr[i] if atr[i] > 0 else 2.0
            cur_bm = b_main[i]
            s5 = max(0, i - 4)
            rec_low = np.min(lows[s5:i+1])
            sug_limit = round(max(cur_bm, rec_low, cur_c - 0.40 * cur_atr) * 20.0) / 20.0
            sug_limit = min(sug_limit, cur_c)
            risk = min(max(sug_limit - (cur_bm - 0.35 * cur_atr), 4.0), 10.0)
            pending_limit = {
                "signal_time": times[i],
                "limit_price": sug_limit,
                "expiry_bar": i + 3,
                "sl": round((sug_limit - risk) * 20.0) / 20.0,
                "t1": round((sug_limit + 8.0) * 20.0) / 20.0,
                "be_lock": round((sug_limit + 6.0) * 20.0) / 20.0,
                "t2": round((sug_limit + 20.0) * 20.0) / 20.0,
            }
            continue

        # Check limit fill
        if not in_pos and pending_limit is not None:
            if lows[i] <= pending_limit["limit_price"]:
                fill_p = min(opens[i], pending_limit["limit_price"])
                in_pos = True
                entry_p = fill_p
                entry_t = times[i]
                sl_p = pending_limit["sl"]
                t1_p = pending_limit["t1"]
                be_lock_p = pending_limit["be_lock"]
                t2_p = pending_limit["t2"]
                t1_hit = False
                be_active = False
                pending_limit = None
            elif i >= pending_limit["expiry_bar"]:
                pending_limit = None # Order expired unfilled

        if in_pos:
            # Check BE lock trigger (+6 pts)
            if not be_active and highs[i] >= be_lock_p:
                be_active = True
                sl_p = max(sl_p, entry_p + 0.5) # Lock Break Even + friction

            # Check T1 scalp (+8 pts)
            if not t1_hit and highs[i] >= t1_p:
                t1_hit = True
                # 50% booked at T1

            # Check T2 runner (+20 pts)
            if t1_hit and highs[i] >= t2_p:
                pnl = 0.5 * (t1_p - entry_p) + 0.5 * (t2_p - entry_p)
                trades_lim.append({"entry_t": entry_t, "exit_t": times[i], "entry_p": entry_p, "exit_p": t2_p, "pnl": pnl, "exit_reason": "T2_RUNNER_HIT"})
                in_pos = False
                continue

            # Check SL hit
            if lows[i] <= sl_p:
                if t1_hit:
                    # 50% booked at T1, 50% exited at SL (which is at BE)
                    pnl = 0.5 * (t1_p - entry_p) + 0.5 * (sl_p - entry_p)
                    reason = "T1_THEN_BE_SL"
                else:
                    pnl = sl_p - entry_p
                    reason = "SL_HIT"
                trades_lim.append({"entry_t": entry_t, "exit_t": times[i], "entry_p": entry_p, "exit_p": sl_p, "pnl": pnl, "exit_reason": reason})
                in_pos = False
                continue

            # Regime or EOD exit
            if sig[i] == -1 or i == n - 1 or times[i] >= "15:20":
                exit_price = closes[i]
                if t1_hit:
                    pnl = 0.5 * (t1_p - entry_p) + 0.5 * (exit_price - entry_p)
                else:
                    pnl = exit_price - entry_p
                trades_lim.append({"entry_t": entry_t, "exit_t": times[i], "entry_p": entry_p, "exit_p": exit_price, "pnl": pnl, "exit_reason": "REGIME_OR_EOD"})
                in_pos = False

    if len(trades_lim) == 0:
        print("  No limit orders were filled today (or no signals).")
    else:
        for idx, t in enumerate(trades_lim, 1):
            pnl_inr = t['pnl'] * 25
            res = "✅ PROFIT" if t['pnl'] > 0 else "❌ LOSS"
            print(f"  Trade {idx}: Entry {t['entry_t']} @ ₹{t['entry_p']:.2f} -> Exit {t['exit_t']} @ ₹{t['exit_p']:.2f} | PnL: {t['pnl']:+.2f} pts ({pnl_inr:+,.0f} INR) [{t['exit_reason']}] {res}")

        total_lim_pts = sum(t['pnl'] for t in trades_lim)
        print(f"  TOTAL LIMIT PnL: {total_lim_pts:+.2f} pts | ₹{total_lim_pts * 25:+,.0f} per lot (Trades: {len(trades_lim)})")

    # Simulate Strategy 3: VELOCITY-ADAPTIVE HYBRID (The Improved Engine)
    print("\n--- SIMULATION 3: IMPROVED VELOCITY-ADAPTIVE ENGINE (Market on Rocket, Limit on Pullback, 50% Scalp + 50% Runner) ---")
    trades_adapt = []
    in_pos = False
    pending_limit = None
    t1_pts = 8.0
    t2_pts = 20.0
    be_trigger = 6.0

    for i in range(n):
        cur_c = closes[i]
        cur_o = opens[i]
        cur_h = highs[i]
        cur_l = lows[i]
        cur_atr = atr[i] if atr[i] > 0 else 2.0
        cur_bm = b_main[i]
        vol = volumes[i] if volumes[i] > 0 else 1.0
        avg_v = vsma[i] if vsma[i] > 0 else 1.0
        rvol = vol / max(avg_v, 1.0)
        span = max(cur_h - cur_l, 0.05)
        u_wick = cur_h - max(cur_o, cur_c)
        s_pres = u_wick / span

        is_rocket = (rvol >= 1.35) and ((cur_c - cur_o) >= 0.60 * span) and (s_pres <= 0.25)

        # Trigger new trade if signal
        if not in_pos and pending_limit is None and sig[i] == 1:
            raw_risk = min(max(cur_c - (cur_bm - 0.35 * cur_atr), 4.0), 10.0)
            if is_rocket:
                # Immediate Market fill
                in_pos = True
                entry_p = cur_c
                entry_t = times[i]
                order_mode = "MARKET_ROCKET"
                sl_p = entry_p - raw_risk
                t1_p = entry_p + t1_pts
                be_p = entry_p + be_trigger
                t2_p = entry_p + t2_pts
                t1_hit = False
                be_active = False
                continue
            else:
                # Place limit order on dip
                s5 = max(0, i - 4)
                rec_l = np.min(lows[s5:i+1])
                sug_l = round(max(cur_bm, rec_l, cur_c - 0.40 * cur_atr) * 20.0) / 20.0
                sug_l = min(sug_l, cur_c)
                l_risk = min(max(sug_l - (cur_bm - 0.35 * cur_atr), 4.0), 10.0)
                pending_limit = {
                    "signal_time": times[i],
                    "limit_price": sug_l,
                    "expiry_bar": i + 3,
                    "sl": round((sug_l - l_risk) * 20.0) / 20.0,
                    "t1": round((sug_l + t1_pts) * 20.0) / 20.0,
                    "be_lock": round((sug_l + be_trigger) * 20.0) / 20.0,
                    "t2": round((sug_l + t2_pts) * 20.0) / 20.0,
                }
                continue

        # Check limit fill
        if not in_pos and pending_limit is not None:
            if lows[i] <= pending_limit["limit_price"]:
                fill_p = min(opens[i], pending_limit["limit_price"])
                in_pos = True
                entry_p = fill_p
                entry_t = times[i]
                order_mode = "LIMIT_DIP"
                sl_p = pending_limit["sl"]
                t1_p = pending_limit["t1"]
                be_p = pending_limit["be_lock"]
                t2_p = pending_limit["t2"]
                t1_hit = False
                be_active = False
                pending_limit = None
            elif i >= pending_limit["expiry_bar"]:
                pending_limit = None

        if in_pos:
            # Check BE Lock
            if not be_active and highs[i] >= be_p:
                be_active = True
                sl_p = max(sl_p, entry_p + 0.5)

            # Check T1 Scalp (+8 pts)
            if not t1_hit and highs[i] >= t1_p:
                t1_hit = True

            # Check T2 Runner (+20 pts)
            if t1_hit and highs[i] >= t2_p:
                pnl = 0.5 * (t1_p - entry_p) + 0.5 * (t2_p - entry_p)
                trades_adapt.append({"entry_t": entry_t, "exit_t": times[i], "entry_p": entry_p, "exit_p": t2_p, "pnl": pnl, "exit_reason": "T2_RUNNER_HIT", "mode": order_mode})
                in_pos = False
                continue

            # Check SL
            if lows[i] <= sl_p:
                if t1_hit:
                    pnl = 0.5 * (t1_p - entry_p) + 0.5 * (sl_p - entry_p)
                    reason = "T1_SCALP_THEN_BE"
                else:
                    pnl = sl_p - entry_p
                    reason = "SL_HIT"
                trades_adapt.append({"entry_t": entry_t, "exit_t": times[i], "entry_p": entry_p, "exit_p": sl_p, "pnl": pnl, "exit_reason": reason, "mode": order_mode})
                in_pos = False
                continue

            # Exit on regime flip or session end
            if sig[i] == -1 or i == n - 1 or times[i] >= "15:20":
                exit_p = closes[i]
                if t1_hit:
                    pnl = 0.5 * (t1_p - entry_p) + 0.5 * (exit_p - entry_p)
                else:
                    pnl = exit_p - entry_p
                trades_adapt.append({"entry_t": entry_t, "exit_t": times[i], "entry_p": entry_p, "exit_p": exit_p, "pnl": pnl, "exit_reason": "REGIME_OR_EOD", "mode": order_mode})
                in_pos = False

    if len(trades_adapt) == 0:
        print("  No trades completed.")
    else:
        for idx, t in enumerate(trades_adapt, 1):
            pnl_inr = t['pnl'] * 25
            res = "✅ PROFIT" if t['pnl'] > 0 else "❌ LOSS"
            print(f"  Trade {idx} [{t['mode']}]: Entry {t['entry_t']} @ ₹{t['entry_p']:.2f} -> Exit {t['exit_t']} @ ₹{t['exit_p']:.2f} | PnL: {t['pnl']:+.2f} pts ({pnl_inr:+,.0f} INR) [{t['exit_reason']}] {res}")

        total_adapt_pts = sum(t['pnl'] for t in trades_adapt)
        wins = [t for t in trades_adapt if t['pnl'] > 0]
        win_rate = (len(wins) / len(trades_adapt)) * 100
        print(f"  TOTAL ADAPTIVE PnL: {total_adapt_pts:+.2f} pts | ₹{total_adapt_pts * 25:+,.0f} per lot (Trades: {len(trades_adapt)} | Win Rate: {win_rate:.1f}%)")

if __name__ == '__main__':
    run_backtest_for_symbol("NIFTY26O1922950PE", "2026-10-19")
    run_backtest_for_symbol("NIFTY26OCT22950PE", "2026-10-19")
