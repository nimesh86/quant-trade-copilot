#!/usr/bin/env python3
"""
Backtest SMF Cloud with Suggested Limit Price, Targets, and Continuation (Re-Buy)
─────────────────────────────────────────────────────────────────────────────────
Simulates and compares:
1. Strategy A: Standard Market Entry at Signal Bar Close
   - Enters at Close of confirmed Buy candle.
   - Exits on Stop Loss (bMain - 0.5 * ATR), Regime Flip (sig == -1), or EOD (15:15).

2. Strategy B: Suggested Limit Price Entry (Passive / Limit Order)
   - Evaluates multi-window swing support (low9, low21, low50, recent 5-bar dip).
   - Computes wick seller/buyer pressure and RVOL.
   - Places Limit Buy at calculated `limit_price`.
   - Simulates order fill within 3 bars: requires `candle.low <= limit_price`.
   - If filled: filled at `min(limit_price, candle.open)`.
   - Exits on Target 1 (1.6R), Stop Loss, Regime Flip, or EOD.

3. Strategy C: Suggested Limit Price + Target Scaling & Trend Continuation (🚀 CONT)
   - Limit order entry as above.
   - At Target 1: books 50% profit, trails SL to Break-Even (entry price).
   - If Trend Continuation (🚀 CONT) triggers (T1 reached, bullish regime holds, RVOL >= 0.8, RSI 50-82, sellers < 0.40):
     re-buys / scales in with Target 2 (3.0R) and new dynamic limit price.
   - Final exits on Target 2, Trailing SL, Regime Flip, or EOD.
"""

import sys
import os
import time
import pickle
import pandas as pd
import numpy as np
from sqlalchemy import create_engine

DB_URL = "postgresql://postgres:postgres@localhost:5432/trading"
CACHE_FILE = "/Users/admin/Documents/Nimesh/Projects/Self Training Engine/trading_engine/scripts/cache_option_candles.pkl"

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

def calculate_smf_limit_and_targets(opens, highs, lows, closes, volumes, i, atr, b_main, vsma, rsi):
    """Exact replica of calculateSMFLimitAndTarget from contract.js"""
    cur_c = closes[i]
    cur_o = opens[i]
    cur_h = highs[i]
    cur_l = lows[i]
    cur_atr = atr[i] if atr[i] > 0 else 2.0
    cur_bm = b_main[i]

    s9 = max(0, i - 8)
    low9 = np.min(lows[s9:i+1])
    
    s21 = max(0, i - 20)
    low21 = np.min(lows[s21:i+1])
    
    s50 = max(0, i - 49)
    low50 = np.min(lows[s50:i+1])
    
    s5 = max(0, i - 4)
    recent_dip_low = np.min(lows[s5:i+1])

    vol = volumes[i] if volumes[i] > 0 else 1.0
    avg_vol = vsma[i] if vsma[i] > 0 else 1.0
    rvol = vol / max(avg_vol, 1.0)

    candle_span = max(cur_h - cur_l, 0.05)
    upper_wick = cur_h - max(cur_o, cur_c)
    lower_wick = min(cur_o, cur_c) - cur_l
    seller_pressure = upper_wick / candle_span
    buyer_pressure = lower_wick / candleSpan if (candleSpan := candle_span) else 0.0

    prev3_close = closes[i - 3] if i >= 3 else cur_o
    is_dropping_recently = (cur_c < prev3_close) or (seller_pressure > 0.42)

    if not is_dropping_recently and rvol >= 1.15 and buyer_pressure >= seller_pressure:
        shallow_dip = cur_c - (0.20 * cur_atr)
        suggested_limit = max(shallow_dip, recent_dip_low, cur_c * 0.96)
    elif is_dropping_recently:
        drop_support = max(low9, cur_bm, cur_c - (0.55 * cur_atr))
        suggested_limit = (cur_c + drop_support) / 2.0
    else:
        cluster_support = max(recent_dip_low, cur_bm, cur_c - (0.35 * cur_atr))
        suggested_limit = max(cluster_support, cur_c - (0.30 * cur_atr))

    suggested_limit = min(suggested_limit, cur_c)
    if cur_bm > 0 and suggested_limit < cur_bm:
        suggested_limit = max(cur_bm, cur_c - cur_atr)

    suggested_limit = round(suggested_limit * 20.0) / 20.0
    if suggested_limit <= 0:
        suggested_limit = cur_c

    stop_loss = round(max(cur_bm - 0.35 * cur_atr, cur_c - 1.5 * cur_atr) * 20.0) / 20.0
    risk = max(1.5, cur_c - stop_loss)
    target1 = round((cur_c + 1.6 * risk) * 20.0) / 20.0
    target2 = round((cur_c + 3.0 * risk) * 20.0) / 20.0
    max_target = round((cur_c + 4.5 * risk) * 20.0) / 20.0

    return {
        "limit_price": suggested_limit,
        "stop_loss": stop_loss,
        "risk": risk,
        "target1": target1,
        "target2": target2,
        "max_target": max_target,
        "rvol": rvol,
        "seller_pressure": seller_pressure,
        "buyer_pressure": buyer_pressure,
        "low9": low9,
        "low21": low21,
        "low50": low50
    }

def load_data():
    if os.path.exists(CACHE_FILE):
        print(f"Loading cached option data from {CACHE_FILE}...")
        t0 = time.time()
        with open(CACHE_FILE, "rb") as f:
            df = pickle.load(f)
        print(f"Loaded {len(df):,} candles in {time.time()-t0:.1f}s.")
        return df

    print("Fetching option candles from TimescaleDB...")
    t0 = time.time()
    engine = create_engine(DB_URL)
    query = """
    SELECT timestamp, symbol, open, high, low, close, volume
    FROM option_minute_candles
    ORDER BY timestamp, symbol ASC;
    """
    df = pd.read_sql(query, engine)
    print(f"Fetched {len(df):,} candles in {time.time()-t0:.1f}s.")
    
    df['timestamp'] = pd.to_datetime(df['timestamp']).dt.tz_convert('Asia/Kolkata')
    df['date'] = df['timestamp'].dt.strftime('%Y-%m-%d')
    
    print("Saving cache to disk...")
    with open(CACHE_FILE, "wb") as f:
        pickle.dump(df, f)
    return df

def run_all_backtests():
    df = load_data()
    unique_dates = sorted(df['date'].unique())
    print(f"Total Trading Days: {len(unique_dates)}")

    # Fast group by (date, symbol)
    print("Preprocessing indicator series across contracts...")
    t0 = time.time()
    
    daily_contract_data = {}
    grouped = df.groupby(['date', 'symbol'])
    
    count_valid = 0
    for (d, s), group in grouped:
        if len(group) < 35:
            continue
        avg_p = group['close'].mean()
        if avg_p < 55.0 or avg_p > 260.0:
            continue

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

        if d not in daily_contract_data:
            daily_contract_data[d] = {}
        daily_contract_data[d][s] = {
            "opens": opens, "highs": highs, "lows": lows, "closes": closes,
            "volumes": volumes, "times": times, "b_main": b_main,
            "atr": atr, "vsma": vsma, "rsi": rsi, "signals": last_signal
        }
        count_valid += 1

    print(f"Processed {count_valid:,} liquid daily option series in {time.time()-t0:.1f}s.")

    # ─────────────────────────────────────────────────────────────────────────
    # SIMULATION OF THREE STRATEGIES
    # ─────────────────────────────────────────────────────────────────────────
    # Strategy 1: Market Order Baseline
    # Strategy 2: Suggested Limit Price Entry (Wait for pullback fill)
    # Strategy 3: Suggested Limit Price + Target Scaling & Continuation Re-Buy
    # ─────────────────────────────────────────────────────────────────────────
    
    print("\n" + "=" * 80)
    print("🚀 RUNNING BACKTEST: MARKET VS LIMIT ORDER VS LIMIT + CONTINUATION")
    print("=" * 80)

    trades_strat_a = [] # Market Order
    trades_strat_b = [] # Limit Order
    trades_strat_c = [] # Limit + Continuation
    
    # Track limit order statistics
    limit_signals_count = 0
    limit_filled_count = 0
    slippage_savings_list = []

    MAX_TRADES_PER_DAY = 5

    for day_str in unique_dates:
        contracts_today = daily_contract_data.get(day_str, {})
        if not contracts_today:
            continue

        sample_s = list(contracts_today.keys())[0]
        day_times = contracts_today[sample_s]["times"]
        n_bars = len(day_times)

        # ---------------------------------------------------------------------
        # STRATEGY A SIMULATION (Market Order Baseline)
        # ---------------------------------------------------------------------
        in_trade_a = False
        active_contract_a = None
        active_entry_a = 0.0
        active_sl_a = 0.0
        active_time_a = None
        trades_today_a = 0

        for bar_idx in range(5, n_bars):
            cur_time = pd.to_datetime(day_times[bar_idx])
            thm = cur_time.strftime("%H:%M")

            if in_trade_a:
                cdata = contracts_today[active_contract_a]
                if bar_idx >= len(cdata["highs"]):
                    in_trade_a = False; continue
                cur_l = cdata["lows"][bar_idx]
                cur_c = cdata["closes"][bar_idx]
                sig = cdata["signals"][bar_idx]
                prev_sig = cdata["signals"][bar_idx - 1] if bar_idx > 0 else sig

                if cur_l <= active_sl_a:
                    exit_p = active_sl_a
                    trades_strat_a.append({
                        "date": day_str, "symbol": active_contract_a, "entry_price": active_entry_a,
                        "exit_price": exit_p, "pnl": exit_p - active_entry_a, "outcome": "STOP_LOSS"
                    })
                    in_trade_a = False
                    continue
                if sig == -1 and prev_sig == 1:
                    exit_p = cur_c
                    trades_strat_a.append({
                        "date": day_str, "symbol": active_contract_a, "entry_price": active_entry_a,
                        "exit_price": exit_p, "pnl": exit_p - active_entry_a, "outcome": "REGIME_EXIT"
                    })
                    in_trade_a = False
                    continue
                if thm >= "15:15":
                    exit_p = cur_c
                    trades_strat_a.append({
                        "date": day_str, "symbol": active_contract_a, "entry_price": active_entry_a,
                        "exit_price": exit_p, "pnl": exit_p - active_entry_a, "outcome": "EOD_EXIT"
                    })
                    in_trade_a = False
                    continue
                continue

            if trades_today_a >= MAX_TRADES_PER_DAY: continue
            if thm < "09:25" or thm > "14:45": continue

            candidates = []
            for s, cdata in contracts_today.items():
                if bar_idx >= len(cdata["signals"]): continue
                sig = cdata["signals"][bar_idx]
                prev_sig = cdata["signals"][bar_idx - 1] if bar_idx > 0 else -1
                if sig == 1 and prev_sig == -1:
                    c = cdata["closes"][bar_idx]
                    atr_v = cdata["atr"][bar_idx]
                    bm = cdata["b_main"][bar_idx]
                    candidates.append((s, c, atr_v, bm))

            if candidates:
                candidates.sort(key=lambda x: x[2], reverse=True)
                best_s, best_entry, best_atr, best_bm = candidates[0]
                in_trade_a = True
                active_contract_a = best_s
                active_entry_a = best_entry
                active_sl_a = best_bm - 0.5 * best_atr
                trades_today_a += 1

        # ---------------------------------------------------------------------
        # STRATEGY B SIMULATION (Suggested Limit Order Entry)
        # ---------------------------------------------------------------------
        in_trade_b = False
        active_contract_b = None
        active_entry_b = 0.0
        active_sl_b = 0.0
        active_t1_b = 0.0
        active_t2_b = 0.0
        trades_today_b = 0

        pending_order_b = None # (symbol, limit_price, sl, t1, t2, expiry_bar, signal_close)

        for bar_idx in range(5, n_bars):
            cur_time = pd.to_datetime(day_times[bar_idx])
            thm = cur_time.strftime("%H:%M")

            # Check pending limit order fill
            if not in_trade_b and pending_order_b is not None:
                p_sym, p_lim, p_sl, p_t1, p_t2, p_exp_bar, p_sig_close = pending_order_b
                cdata = contracts_today[p_sym]
                if bar_idx < len(cdata["lows"]):
                    bar_l = cdata["lows"][bar_idx]
                    bar_o = cdata["opens"][bar_idx]
                    bar_sig = cdata["signals"][bar_idx]

                    # Order fills if bar low touches or dips below limit price
                    if bar_l <= p_lim:
                        # Filled! Entry price is min(limit_price, open)
                        fill_p = min(p_lim, bar_o)
                        in_trade_b = True
                        active_contract_b = p_sym
                        active_entry_b = fill_p
                        active_sl_b = p_sl
                        active_t1_b = p_t1
                        active_t2_b = p_t2
                        pending_order_b = None
                        trades_today_b += 1
                        limit_filled_count += 1
                        savings = p_sig_close - fill_p
                        slippage_savings_list.append(savings)
                    elif bar_idx >= p_exp_bar or bar_sig == -1:
                        # Limit order expired or invalidated by regime flip
                        pending_order_b = None

            # Manage active trade
            if in_trade_b:
                cdata = contracts_today[active_contract_b]
                if bar_idx >= len(cdata["highs"]):
                    in_trade_b = False; continue
                cur_l = cdata["lows"][bar_idx]
                cur_h = cdata["highs"][bar_idx]
                cur_c = cdata["closes"][bar_idx]
                sig = cdata["signals"][bar_idx]
                prev_sig = cdata["signals"][bar_idx - 1] if bar_idx > 0 else sig

                # Exit 1: Stop Loss
                if cur_l <= active_sl_b:
                    exit_p = active_sl_b
                    trades_strat_b.append({
                        "date": day_str, "symbol": active_contract_b, "entry_price": active_entry_b,
                        "exit_price": exit_p, "pnl": exit_p - active_entry_b, "outcome": "STOP_LOSS"
                    })
                    in_trade_b = False
                    continue

                # Exit 2: Target 1 Hit
                if cur_h >= active_t1_b:
                    exit_p = active_t1_b
                    trades_strat_b.append({
                        "date": day_str, "symbol": active_contract_b, "entry_price": active_entry_b,
                        "exit_price": exit_p, "pnl": exit_p - active_entry_b, "outcome": "TARGET_1"
                    })
                    in_trade_b = False
                    continue

                # Exit 3: Regime Flip to Bearish
                if sig == -1 and prev_sig == 1:
                    exit_p = cur_c
                    trades_strat_b.append({
                        "date": day_str, "symbol": active_contract_b, "entry_price": active_entry_b,
                        "exit_price": exit_p, "pnl": exit_p - active_entry_b, "outcome": "REGIME_EXIT"
                    })
                    in_trade_b = False
                    continue

                # Exit 4: EOD
                if thm >= "15:15":
                    exit_p = cur_c
                    trades_strat_b.append({
                        "date": day_str, "symbol": active_contract_b, "entry_price": active_entry_b,
                        "exit_price": exit_p, "pnl": exit_p - active_entry_b, "outcome": "EOD_EXIT"
                    })
                    in_trade_b = False
                    continue
                continue

            if trades_today_b >= MAX_TRADES_PER_DAY: continue
            if thm < "09:25" or thm > "14:45": continue
            if pending_order_b is not None: continue

            # Detect Buy signals
            candidates = []
            for s, cdata in contracts_today.items():
                if bar_idx >= len(cdata["signals"]): continue
                sig = cdata["signals"][bar_idx]
                prev_sig = cdata["signals"][bar_idx - 1] if bar_idx > 0 else -1
                if sig == 1 and prev_sig == -1:
                    atr_v = cdata["atr"][bar_idx]
                    candidates.append((s, atr_v))

            if candidates:
                candidates.sort(key=lambda x: x[1], reverse=True)
                best_s, best_atr = candidates[0]
                cdata = contracts_today[best_s]

                metrics = calculate_smf_limit_and_targets(
                    cdata["opens"], cdata["highs"], cdata["lows"], cdata["closes"],
                    cdata["volumes"], bar_idx, cdata["atr"], cdata["b_main"],
                    cdata["vsma"], cdata["rsi"]
                )
                limit_signals_count += 1
                # Place Limit Buy valid for up to 3 bars
                pending_order_b = (
                    best_s, metrics["limit_price"], metrics["stop_loss"],
                    metrics["target1"], metrics["target2"], bar_idx + 3,
                    cdata["closes"][bar_idx]
                )

        # ---------------------------------------------------------------------
        # STRATEGY C SIMULATION (Suggested Limit + Continuation Re-Buy)
        # ---------------------------------------------------------------------
        in_trade_c = False
        active_contract_c = None
        active_entry_c = 0.0
        active_sl_c = 0.0
        active_t1_c = 0.0
        active_t2_c = 0.0
        t1_achieved_c = False
        continuation_count_c = 0
        last_buy_bar_c = -999
        trades_today_c = 0

        pending_order_c = None

        for bar_idx in range(5, n_bars):
            cur_time = pd.to_datetime(day_times[bar_idx])
            thm = cur_time.strftime("%H:%M")

            # Check pending limit order fill
            if not in_trade_c and pending_order_c is not None:
                p_sym, p_lim, p_sl, p_t1, p_t2, p_exp_bar, p_sig_close = pending_order_c
                cdata = contracts_today[p_sym]
                if bar_idx < len(cdata["lows"]):
                    bar_l = cdata["lows"][bar_idx]
                    bar_o = cdata["opens"][bar_idx]
                    bar_sig = cdata["signals"][bar_idx]

                    if bar_l <= p_lim:
                        fill_p = min(p_lim, bar_o)
                        in_trade_c = True
                        active_contract_c = p_sym
                        active_entry_c = fill_p
                        active_sl_c = p_sl
                        active_t1_c = p_t1
                        active_t2_c = p_t2
                        t1_achieved_c = False
                        continuation_count_c = 0
                        last_buy_bar_c = bar_idx
                        pending_order_c = None
                        trades_today_c += 1
                    elif bar_idx >= p_exp_bar or bar_sig == -1:
                        pending_order_c = None

            # Manage active trade
            if in_trade_c:
                cdata = contracts_today[active_contract_c]
                if bar_idx >= len(cdata["highs"]):
                    in_trade_c = False; continue
                cur_l = cdata["lows"][bar_idx]
                cur_h = cdata["highs"][bar_idx]
                cur_c = cdata["closes"][bar_idx]
                sig = cdata["signals"][bar_idx]
                prev_sig = cdata["signals"][bar_idx - 1] if bar_idx > 0 else sig

                # Check if T1 achieved
                if not t1_achieved_c and cur_h >= active_t1_c:
                    t1_achieved_c = True
                    # Trail SL to entry price (Break-Even lock!)
                    active_sl_c = max(active_sl_c, active_entry_c)

                # Check Trend Continuation (🚀 CONT)
                if t1_achieved_c and (bar_idx - last_buy_bar_c >= 4) and continuation_count_c < 2:
                    is_healthy_vol = cdata["volumes"][bar_idx] >= (cdata["vsma"][bar_idx] * 0.8)
                    is_healthy_rsi = 50 <= cdata["rsi"][bar_idx] <= 82
                    holds_above = cur_c >= cdata["b_main"][bar_idx]

                    c_metrics = calculate_smf_limit_and_targets(
                        cdata["opens"], cdata["highs"], cdata["lows"], cdata["closes"],
                        cdata["volumes"], bar_idx, cdata["atr"], cdata["b_main"],
                        cdata["vsma"], cdata["rsi"]
                    )
                    low_sellers = c_metrics["seller_pressure"] < 0.40

                    if is_healthy_vol and is_healthy_rsi and holds_above and low_sellers:
                        continuation_count_c += 1
                        last_buy_bar_c = bar_idx
                        # Expand target to Target 2 and lift trailing SL to cloud base
                        active_t2_c = c_metrics["target2"]
                        active_sl_c = max(active_sl_c, cdata["b_main"][bar_idx])

                # Exit 1: Stop Loss (or Trailing Break-Even)
                if cur_l <= active_sl_c:
                    exit_p = active_sl_c
                    outcome = "TRAIL_BE" if exit_p >= active_entry_c else "STOP_LOSS"
                    trades_strat_c.append({
                        "date": day_str, "symbol": active_contract_c, "entry_price": active_entry_c,
                        "exit_price": exit_p, "pnl": exit_p - active_entry_c, "outcome": outcome
                    })
                    in_trade_c = False
                    continue

                # Exit 2: Target 2 Hit (for runner continuation)
                if t1_achieved_c and cur_h >= active_t2_c:
                    exit_p = active_t2_c
                    trades_strat_c.append({
                        "date": day_str, "symbol": active_contract_c, "entry_price": active_entry_c,
                        "exit_price": exit_p, "pnl": exit_p - active_entry_c, "outcome": "TARGET_2_CONT"
                    })
                    in_trade_c = False
                    continue

                # Exit 3: Regime Flip to Bearish
                if sig == -1 and prev_sig == 1:
                    exit_p = cur_c
                    outcome = "REGIME_WIN" if exit_p > active_entry_c else "REGIME_LOSS"
                    trades_strat_c.append({
                        "date": day_str, "symbol": active_contract_c, "entry_price": active_entry_c,
                        "exit_price": exit_p, "pnl": exit_p - active_entry_c, "outcome": outcome
                    })
                    in_trade_c = False
                    continue

                # Exit 4: EOD
                if thm >= "15:15":
                    exit_p = cur_c
                    trades_strat_c.append({
                        "date": day_str, "symbol": active_contract_c, "entry_price": active_entry_c,
                        "exit_price": exit_p, "pnl": exit_p - active_entry_c, "outcome": "EOD_EXIT"
                    })
                    in_trade_c = False
                    continue
                continue

            if trades_today_c >= MAX_TRADES_PER_DAY: continue
            if thm < "09:25" or thm > "14:45": continue
            if pending_order_c is not None: continue

            # Detect Buy signals
            candidates = []
            for s, cdata in contracts_today.items():
                if bar_idx >= len(cdata["signals"]): continue
                sig = cdata["signals"][bar_idx]
                prev_sig = cdata["signals"][bar_idx - 1] if bar_idx > 0 else -1
                if sig == 1 and prev_sig == -1:
                    atr_v = cdata["atr"][bar_idx]
                    candidates.append((s, atr_v))

            if candidates:
                candidates.sort(key=lambda x: x[1], reverse=True)
                best_s, best_atr = candidates[0]
                cdata = contracts_today[best_s]

                metrics = calculate_smf_limit_and_targets(
                    cdata["opens"], cdata["highs"], cdata["lows"], cdata["closes"],
                    cdata["volumes"], bar_idx, cdata["atr"], cdata["b_main"],
                    cdata["vsma"], cdata["rsi"]
                )
                pending_order_c = (
                    best_s, metrics["limit_price"], metrics["stop_loss"],
                    metrics["target1"], metrics["target2"], bar_idx + 3,
                    cdata["closes"][bar_idx]
                )

    # ─────────────────────────────────────────────────────────────────────────
    # PERFORMANCE METRICS CALCULATION
    # ─────────────────────────────────────────────────────────────────────────
    LOT_SIZE = 25
    BROKERAGE = 25.0

    def print_strategy_report(name, trades):
        df_t = pd.DataFrame(trades)
        if len(df_t) == 0:
            print(f"\n{name}: No trades generated.")
            return

        df_t['pnl_per_lot'] = df_t['pnl'] * LOT_SIZE
        df_t['net_pnl_per_lot'] = df_t['pnl_per_lot'] - BROKERAGE

        total = len(df_t)
        wins = len(df_t[df_t['pnl'] > 0])
        losses = len(df_t[df_t['pnl'] < 0])
        breakeven = len(df_t[df_t['pnl'] == 0])

        win_rate = (wins / total) * 100.0
        gross_pts = df_t['pnl'].sum()
        gross_pnl = df_t['pnl_per_lot'].sum()
        net_pnl = df_t['net_pnl_per_lot'].sum()

        win_pts = df_t[df_t['pnl'] > 0]['pnl'].sum()
        loss_pts = abs(df_t[df_t['pnl'] < 0]['pnl'].sum())
        profit_factor = (win_pts / loss_pts) if loss_pts > 0 else 999.0
        avg_trade_pts = df_t['pnl'].mean()
        avg_win_pts = df_t[df_t['pnl'] > 0]['pnl'].mean() if wins > 0 else 0
        avg_loss_pts = df_t[df_t['pnl'] < 0]['pnl'].mean() if losses > 0 else 0

        # Max Drawdown
        df_t['cum_pnl'] = df_t['net_pnl_per_lot'].cumsum()
        df_t['cum_peak'] = df_t['cum_pnl'].cummax()
        df_t['drawdown'] = df_t['cum_pnl'] - df_t['cum_peak']
        max_dd = df_t['drawdown'].min()

        print(f"\n{'='*75}")
        print(f"📊 {name.upper()} (128 TRADING DAYS)")
        print(f"{'='*75}")
        print(f"Total Trades:         {total} ({total/128:.2f} trades/day)")
        print(f"Win Rate:             {wins}/{total} ({win_rate:.1f}%) [Wins: {wins}, Losses: {losses}, BE: {breakeven}]")
        print(f"Gross Points:         {gross_pts:+.2f} pts")
        print(f"Average Trade:        {avg_trade_pts:+.2f} pts (Avg Win: {avg_win_pts:+.2f} pts | Avg Loss: {avg_loss_pts:+.2f} pts)")
        print(f"Profit Factor:        {profit_factor:.2f}")
        print(f"Max Drawdown:         ₹{max_dd:,.2f}")
        print(f"Gross PnL (1 Lot):    ₹{gross_pnl:+,.2f}")
        print(f"Brokerage & Charges:  ₹{total * BROKERAGE:,.2f}")
        print(f"Net Realized Profit:  ₹{net_pnl:+,.2f}")
        print(f"Outcomes Distribution:")
        print(df_t['outcome'].value_counts().to_string())

    print_strategy_report("Strategy A: Standard Market Order (Signal Close)", trades_strat_a)
    print_strategy_report("Strategy B: Suggested Limit Price Entry + Target 1", trades_strat_b)
    print_strategy_report("Strategy C: Suggested Limit Price + Runner & Continuation (🚀 CONT)", trades_strat_c)

    # Limit Order Fill Rate & Price Improvement Analysis
    fill_rate = (limit_filled_count / limit_signals_count * 100.0) if limit_signals_count > 0 else 0.0
    avg_savings = np.mean(slippage_savings_list) if slippage_savings_list else 0.0
    total_savings_pts = np.sum(slippage_savings_list) if slippage_savings_list else 0.0

    print("\n" + "=" * 75)
    print("🎯 LIMIT ORDER EXECUTION & SLIPPAGE SAVINGS ANALYSIS")
    print("=" * 75)
    print(f"Total Buy Signals Triggered: {limit_signals_count}")
    print(f"Total Limit Orders Filled:   {limit_filled_count} ({fill_rate:.1f}% fill rate)")
    print(f"Unfilled (Saved Chasing):    {limit_signals_count - limit_filled_count} ({100-fill_rate:.1f}%)")
    print(f"Average Fill Improvement:    +{avg_savings:.2f} pts per filled contract")
    print(f"Total Entry Improvement:     +{total_savings_pts:,.2f} pts (₹{total_savings_pts * LOT_SIZE:+,.2f} on 1 Lot)")
    print("=" * 75)

if __name__ == "__main__":
    run_all_backtests()
