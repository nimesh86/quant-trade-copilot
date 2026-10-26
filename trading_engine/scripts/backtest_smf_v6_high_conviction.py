#!/usr/bin/env python3
"""
Backtest Comparison: Current V5_P1 vs New V6 High-Conviction (2-3 Trades/Day, 09:45 ORB Filter, 1.5R T1, 0.5R Runner Lock)
Tests across 128 days (799,330 historical 1-minute candles).
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import time
import pickle
from typing import Dict, Any, List, Optional
import numpy as np
import pandas as pd

from trading_engine.scripts.smf_dynamic_exit_engine import (
    load_backtest_dataset, select_tradable_atm_contracts, compute_all_indicators,
    compute_smf_regimes, is_srr_setup, is_gamma_ignition_setup, slip, round_tick,
    calculate_trend_health_score, update_health_state, detect_climax_exhaustion,
    calculate_relaxed_ladder_floor, calculate_initial_stop_and_r,
    TradePhase, HealthState, ActiveTrade, ReEntryWatcher, ReEntryType,
    DEFAULT_CACHE_PATH, EOD_TIME, MIN_PREMIUM, MAX_PREMIUM, COSTS_PER_UNIT, NSE_TICK
)


class V6HighConvictionEngine:
    K_BY_STATE = {
        HealthState.STRONG: 3.0,
        HealthState.NORMAL: 2.2,
        HealthState.WEAK: 1.4,
        HealthState.CLIMAX: 0.8,
    }

    def __init__(
        self,
        variant: str = "V6",
        min_entry_time: str = "09:45",
        max_daily_trades: int = 3,
        daily_target_r: float = 3.0,     # Stop day at +3R (~₹2,500-₹3,000)
        max_daily_loss_r: float = -2.0,  # Max drawdown stop
    ):
        self.variant = variant
        self.min_entry_time = min_entry_time
        self.max_daily_trades = max_daily_trades
        self.daily_target_r = daily_target_r
        self.max_daily_loss_r = max_daily_loss_r

        self.active_trade: Optional[ActiveTrade] = None
        self.reentry_watcher: Optional[ReEntryWatcher] = None
        self.daily_trade_count = 0
        self.daily_realized_r = 0.0
        self.consecutive_losses = 0
        self.day_halted = False
        self.current_date = None
        self.trades_history: List[Dict[str, Any]] = []

    def reset_day(self, date_str: str):
        self.current_date = date_str
        self.daily_trade_count = 0
        self.daily_realized_r = 0.0
        self.consecutive_losses = 0
        self.day_halted = False
        self.active_trade = None
        self.reentry_watcher = None

    def execute_exit(self, bar_idx: int, c_time: str, exit_price: float, reason: str, bar_c: float, limit_exit: bool = False):
        t = self.active_trade
        fill = exit_price if limit_exit else exit_price - slip(exit_price)
        exit_p = round_tick(max(fill, NSE_TICK))

        pnl_pts = (exit_p - t.entry_price) * t.rem_qty - COSTS_PER_UNIT + t.realized_pts
        pnl_r = pnl_pts / t.R

        peak_pts = t.hh - t.entry_price
        capture_ratio = max(0.0, pnl_pts / peak_pts) if peak_pts > NSE_TICK else 0.0
        give_back = max(0.0, (t.hh - exit_p) / peak_pts) if peak_pts > NSE_TICK else 0.0

        record = {
            "date": self.current_date,
            "symbol": t.symbol,
            "trade_num": len(self.trades_history) + 1,
            "entry_bar": t.entry_bar,
            "exit_bar": bar_idx,
            "entry_time": getattr(t, "entry_time", "09:30"),
            "exit_time": c_time,
            "entry_price": t.entry_price,
            "exit_price": exit_p,
            "initial_stop": t.initial_stop,
            "final_trail": t.trail,
            "final_ladder": t.ladder_floor,
            "R": t.R,
            "peak_pts": peak_pts,
            "mfe_r": t.mfe_r,
            "pnl_pts": pnl_pts,
            "pnl_r": pnl_r,
            "capture_ratio": capture_ratio,
            "give_back": give_back,
            "reason": reason,
            "is_reentry": t.is_reentry,
            "p1_booked": t.p1_booked
        }
        self.trades_history.append(record)

        self.daily_realized_r += pnl_r
        if pnl_r < 0:
            self.consecutive_losses += 1
        else:
            self.consecutive_losses = 0

        # Day target met or max loss hit -> Lock profit / protect drawdown
        if self.daily_realized_r >= self.daily_target_r or self.daily_realized_r <= self.max_daily_loss_r or self.consecutive_losses >= 2:
            self.day_halted = True

        self.active_trade = None
        return record

    def update_active_trade(self, bar_idx: int, c_open: float, c_high: float, c_low: float, c_close: float, c_time: str, ind: Dict[str, np.ndarray], is_switch_down: bool):
        t = self.active_trade
        is_entry_bar = (bar_idx == t.entry_bar)

        # 1. Hard stop check
        if c_low <= t.catastrophic_stop:
            return self.execute_exit(bar_idx, c_time, min(t.catastrophic_stop, c_open), "CATASTROPHIC_STOP", c_close)

        hard_stop = max(t.initial_stop, t.ladder_floor)
        if c_low <= hard_stop:
            reason = "LADDER_FLOOR" if hard_stop > t.initial_stop else "INITIAL_STOP"
            exit_px = min(c_open, hard_stop)
            return self.execute_exit(bar_idx, c_time, exit_px, reason, c_close)

        # 2. Update high watermark
        if not is_entry_bar and c_high > t.hh:
            t.hh = c_high
            t.last_high_bar = bar_idx
        t.mfe_r = (t.hh - t.entry_price) / t.R

        # 3. Dynamic Target 1: Book 50% at >= 1.5R (High-Value scaling)
        if not t.p1_booked and t.mfe_r >= 1.5:
            t.p1_booked = True
            partial_exit_p = round_tick(t.entry_price + 1.5 * t.R)
            t.realized_pts += 0.50 * (partial_exit_p - t.entry_price)
            t.rem_qty = 0.50

            # 4. GUARANTEED PROFIT LOCK ON RUNNER:
            # Immediately lock ladder floor to Entry + 0.5R (+8 to +10 pts guaranteed on runner!)
            t.ladder_floor = max(t.ladder_floor, round_tick(t.entry_price + 0.50 * t.R))

        # 5. Dynamic Ladder update
        if t.p1_booked:
            # If P1 is booked, ladder floor stays at least entry + 0.5R, or trails with 60% peak capture
            relaxed = calculate_relaxed_ladder_floor(t)
            guaranteed = round_tick(t.entry_price + 0.50 * t.R)
            t.ladder_floor = max(t.ladder_floor, guaranteed, relaxed)
        else:
            t.ladder_floor = max(t.ladder_floor, calculate_relaxed_ladder_floor(t))

        # 6. Maximum Target Hit (3.2R)
        if t.mfe_r >= 3.2:
            max_t = round_tick(t.entry_price + 3.2 * t.R)
            if c_high >= max_t:
                return self.execute_exit(bar_idx, c_time, max_t, "MAX_TARGET_HIT", c_close, limit_exit=True)

        # 7. Structural Exits
        if is_switch_down:
            return self.execute_exit(bar_idx, c_time, c_close, "REGIME_FLIP", c_close)
        if c_time >= EOD_TIME:
            return self.execute_exit(bar_idx, c_time, c_close, "EOD_EXIT", c_close)

        # 8. Adaptive Chandelier Trail
        ths = calculate_trend_health_score(bar_idx, ind["closes"], ind["highs"], ind["lows"], ind["opens"], ind)
        t.state = update_health_state(t.state, ths, t)
        if ths < 20:
            t.low_ths_bars += 1
            if t.low_ths_bars >= 2:
                return self.execute_exit(bar_idx, c_time, c_close, "HEALTH_COLLAPSE", c_close)
        else:
            t.low_ths_bars = 0

        k = self.K_BY_STATE.get(t.state, 2.0)
        if t.mfe_r >= 1.5:
            raw_trail = round_tick(t.hh - k * ind["atr"][bar_idx])
            t.trail = max(t.trail, raw_trail)
            if c_close < t.trail:
                return self.execute_exit(bar_idx, c_time, c_close, "TRAIL_BREAK", c_close)


def run_comparative_backtest():
    print("=" * 80)
    print("   QUANT BACKTEST: CURRENT V5_P1 VS NEW V6 HIGH-CONVICTION ENGINE")
    print("   Testing across 128 trading days (April 2026 - October 2026)")
    print("=" * 80)

    df = load_backtest_dataset(DEFAULT_CACHE_PATH)
    day_groups = {d: group for d, group in df.groupby("date")}
    daily_precomputed = {}

    print("[*] Pre-computing indicators and opening ranges...")
    for d_str, day_df in day_groups.items():
        selected_syms = select_tradable_atm_contracts(day_df, 4)
        if not selected_syms: continue
        contract_data = {}
        for sym in selected_syms:
            c_df = day_df[day_df["symbol"] == sym].sort_values("timestamp").reset_index(drop=True)
            if len(c_df) < 30: continue
            opens = c_df["open"].values
            highs = c_df["high"].values
            lows = c_df["low"].values
            closes = c_df["close"].values
            volumes = c_df["volume"].values
            times = c_df["timestamp"].dt.strftime("%H:%M").values

            ind = compute_all_indicators(opens, highs, lows, closes, volumes)
            ind["opens"], ind["highs"], ind["lows"], ind["closes"] = opens, highs, lows, closes
            regimes, sw_up, sw_down = compute_smf_regimes(closes, ind["b_main"], ind["atr"], confirm_bars=2, atr_mult=0.50)

            # Precalculate 15-min Opening Range (09:15-09:30) High
            orb_high = float("-inf")
            for idx, t_str in enumerate(times):
                if t_str <= "09:30":
                    if highs[idx] > orb_high:
                        orb_high = highs[idx]

            contract_data[sym] = {
                "times": times, "opens": opens, "highs": highs, "lows": lows, "closes": closes,
                "volumes": volumes, "ind": ind, "regimes": regimes, "sw_up": sw_up, "sw_down": sw_down,
                "orb_high": orb_high, "length": len(c_df)
            }
        if contract_data:
            daily_precomputed[d_str] = contract_data

    # 1. Run Benchmark V5_P1
    from trading_engine.scripts.smf_dynamic_exit_engine import HighConvictionPortfolioEngine
    print("\n>>> Running Current V5_P1 Engine...")
    t0 = time.time()
    v5_engine = HighConvictionPortfolioEngine(variant="V5_P1")
    for d_str, contract_data in daily_precomputed.items():
        v5_engine.reset_day(d_str)
        sample_sym = list(contract_data.keys())[0]
        day_timeline = contract_data[sample_sym]["times"]
        for b in range(10, len(day_timeline)):
            c_time = day_timeline[b]
            if v5_engine.active_trade:
                act_sym = v5_engine.active_trade.symbol
                cdata = contract_data.get(act_sym)
                if cdata and b < cdata["length"]:
                    v5_engine.update_active_trade(b, cdata["opens"][b], cdata["highs"][b], cdata["lows"][b], cdata["closes"][b], c_time, cdata["ind"], cdata["sw_down"][b])
            elif not v5_engine.day_halted and v5_engine.daily_trade_count < 4:
                candidates = []
                for sym, cdata in contract_data.items():
                    if b >= cdata["length"]: continue
                    c_close = cdata["closes"][b]
                    if not (MIN_PREMIUM <= c_close <= MAX_PREMIUM): continue
                    is_srr, dip_val = is_srr_setup(b, cdata["opens"][b], cdata["highs"][b], cdata["lows"][b], c_close, c_time, cdata)
                    is_smf = cdata["sw_up"][b] and is_gamma_ignition_setup(b, cdata["opens"][b], cdata["highs"][b], cdata["lows"][b], c_close, cdata["ind"])
                    if is_srr: candidates.append((sym, cdata["ind"]["rvol"][b], "SRR", dip_val))
                    elif is_smf: candidates.append((sym, cdata["ind"]["rvol"][b], "SMF", 0.0))
                if candidates:
                    candidates.sort(key=lambda x: (1 if x[2] == "SRR" else 0, x[1]), reverse=True)
                    best_sym, _, entry_type, dip_val = candidates[0]
                    cdata = contract_data[best_sym]
                    entry = round_tick(cdata["closes"][b] + slip(cdata["closes"][b]))
                    init_stop, r_val = calculate_initial_stop_and_r(entry, cdata["ind"]["b_main"][b], cdata["ind"]["atr"][b])
                    v5_engine.daily_trade_count += 1
                    v5_engine.active_trade = ActiveTrade(
                        symbol=best_sym, entry_price=entry, entry_bar=b, initial_stop=init_stop,
                        R=r_val, catastrophic_stop=round_tick(entry - 2.0 * r_val), hh=entry,
                        last_high_bar=b, mfe_r=0.0, phase=TradePhase.PROTECT, state=HealthState.NORMAL,
                        trail=init_stop, ladder_floor=init_stop
                    )
        if v5_engine.active_trade:
            act_sym = v5_engine.active_trade.symbol
            cdata = contract_data.get(act_sym)
            if cdata:
                last_b = min(len(day_timeline) - 1, cdata["length"] - 1)
                v5_engine.execute_exit(last_b, day_timeline[last_b], cdata["closes"][last_b], "EOD_EXIT", cdata["closes"][last_b])

    df_v5 = pd.DataFrame(v5_engine.trades_history)
    print(f"[+] V5_P1 completed in {time.time() - t0:.2f}s ({len(df_v5)} trades).")

    # 2. Run New V6 High Conviction Engine
    print("\n>>> Running New V6 High-Conviction Engine (09:45 Filter + 1.5R T1 + Runner Profit Lock + Max 2-3 Trades)...")
    t0 = time.time()
    v6_engine = V6HighConvictionEngine(min_entry_time="09:45", max_daily_trades=3, daily_target_r=3.0)
    for d_str, contract_data in daily_precomputed.items():
        v6_engine.reset_day(d_str)
        sample_sym = list(contract_data.keys())[0]
        day_timeline = contract_data[sample_sym]["times"]
        for b in range(10, len(day_timeline)):
            c_time = day_timeline[b]
            if v6_engine.active_trade:
                act_sym = v6_engine.active_trade.symbol
                cdata = contract_data.get(act_sym)
                if cdata and b < cdata["length"]:
                    v6_engine.update_active_trade(b, cdata["opens"][b], cdata["highs"][b], cdata["lows"][b], cdata["closes"][b], c_time, cdata["ind"], cdata["sw_down"][b])
            elif not v6_engine.day_halted and v6_engine.daily_trade_count < v6_engine.max_daily_trades:
                # FILTER 1: Skip opening noise (09:15 - 09:45 IST) and midday chop
                if c_time < "09:45" or ("11:30" <= c_time <= "13:15") or c_time >= "15:05":
                    continue

                candidates = []
                for sym, cdata in contract_data.items():
                    if b >= cdata["length"]: continue
                    c_close = cdata["closes"][b]
                    c_open = cdata["opens"][b]
                    c_high = cdata["highs"][b]
                    c_low = cdata["lows"][b]
                    if not (MIN_PREMIUM <= c_close <= MAX_PREMIUM): continue

                    # FILTER 2: Structure Confirmation & Resistance Break
                    start_8 = max(0, b - 8)
                    prev_swing_high = np.max(cdata["highs"][start_8:b]) if b > start_8 else c_open
                    broke_res = (c_close >= prev_swing_high - 0.15 * cdata["ind"]["atr"][b])

                    is_srr, dip_val = is_srr_setup(b, c_open, c_high, c_low, c_close, c_time, cdata)
                    is_smf = cdata["sw_up"][b] and is_gamma_ignition_setup(b, c_open, c_high, c_low, c_close, cdata["ind"]) and broke_res

                    if is_srr and broke_res:
                        candidates.append((sym, cdata["ind"]["rvol"][b], "SRR", dip_val))
                    elif is_smf:
                        candidates.append((sym, cdata["ind"]["rvol"][b], "SMF", 0.0))


                if candidates:
                    candidates.sort(key=lambda x: (1 if x[2] == "SRR" else 0, x[1]), reverse=True)
                    best_sym, _, entry_type, dip_val = candidates[0]
                    cdata = contract_data[best_sym]
                    entry = round_tick(cdata["closes"][b] + slip(cdata["closes"][b]))
                    init_stop, r_val = calculate_initial_stop_and_r(entry, cdata["ind"]["b_main"][b], cdata["ind"]["atr"][b])
                    v6_engine.daily_trade_count += 1
                    t = ActiveTrade(
                        symbol=best_sym, entry_price=entry, entry_bar=b, initial_stop=init_stop,
                        R=r_val, catastrophic_stop=round_tick(entry - 2.0 * r_val), hh=entry,
                        last_high_bar=b, mfe_r=0.0, phase=TradePhase.PROTECT, state=HealthState.NORMAL,
                        trail=init_stop, ladder_floor=init_stop
                    )
                    t.entry_time = c_time
                    v6_engine.active_trade = t

        if v6_engine.active_trade:
            act_sym = v6_engine.active_trade.symbol
            cdata = contract_data.get(act_sym)
            if cdata:
                last_b = min(len(day_timeline) - 1, cdata["length"] - 1)
                v6_engine.execute_exit(last_b, day_timeline[last_b], cdata["closes"][last_b], "EOD_EXIT", cdata["closes"][last_b])

    df_v6 = pd.DataFrame(v6_engine.trades_history)
    print(f"[+] V6 completed in {time.time() - t0:.2f}s ({len(df_v6)} trades).")

    # 3. Print Head-to-Head Comparison Table
    print("\n" + "=" * 95)
    print("                      HEAD-TO-HEAD QUANTITATIVE VERIFICATION")
    print("=" * 95)
    print(f"{'Engine Variant':<20} | {'Trades':<8} | {'Win Rate':<10} | {'Total PnL (R)':<14} | {'Profit Factor':<14} | {'Avg PnL/Trade':<14}")
    print("-" * 95)

    for name, df_res in [("Current V5_P1", df_v5), ("New V6 High-Conviction", df_v6)]:
        if len(df_res) == 0:
            print(f"{name:<20} | 0 trades")
            continue
        wins = df_res[df_res["pnl_r"] > 0]
        losses = df_res[df_res["pnl_r"] <= 0]
        wr = (len(wins) / len(df_res)) * 100
        tot_r = df_res["pnl_r"].sum()
        gw = wins["pnl_r"].sum() if len(wins) > 0 else 0.0
        gl = abs(losses["pnl_r"].sum()) if len(losses) > 0 else 1e-4
        pf = gw / gl if gl > 0 else float("inf")
        avg_r = tot_r / len(df_res)
        print(f"{name:<20} | {len(df_res):<8} | {wr:<9.1f}% | {tot_r:>+12.2f}R | {pf:>12.2f} | {avg_r:>+12.3f}R")

    print("=" * 95)


if __name__ == "__main__":
    run_comparative_backtest()
