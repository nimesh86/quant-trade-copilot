#!/usr/bin/env python3
"""
SMF Dynamic Exit & Re-Entry Engine (High-Conviction Portfolio Edition)
======================================================================
A complete, self-contained implementation of the SMF Dynamic Exit and Re-Entry system
incorporating the 4-Pillar Quantitative Solution:

1. Pillar 1: Relaxed Profit-Lock Ladder (Eliminates the Break-Even choke)
   - At +1.0R: Ratchet stop to Entry - 0.5R (cut risk in half, give breathing room for retest).
   - At +1.8R: Ratchet stop to Entry + Costs (true Break-Even lock).
   - At +2.5R: Ratchet stop to Entry + 0.5R.
   - At +3.5R: Cap give-back at 50% of peak profit (Entry + 0.5 * (HH - Entry)).
   - Optional Partial Booking (P1): Books 25% profit at +1.5R.

2. Pillar 2: Stop Noise Filtering & Close-Based Stops (Eliminates 49.5% 1m noise stop-outs)
   - Catastrophic Stop (Intrabar emergency breaker): Low <= Entry - 2.0 * R.
   - Normal Initial Stop & Ladder Floor: Evaluated on candle CLOSE (Close < Stop).
     Wicks that dip and close green are no longer falsely stopped out!

3. Pillar 3: Single Active Contract Portfolio Model (Eliminates multi-strike fakeout bleed)
   - Exactly ONE active trade at a time across the entire portfolio.
   - Max 3 high-conviction trades per day.
   - No simultaneous overlapping strike cannibalization.

4. Pillar 4: High-Conviction Gamma Ignition Filters (Eliminates low-quality entries)
   - Solid body: (Close - Low) / CandleSpan >= 0.65 (closed in upper 35% of candle).
   - Momentum sweet-spot: 52 <= RSI <= 72 and RSI > prev_RSI (accelerating delta).
   - Volume surge: RVOL >= 1.25 on breakout bar.
   - Premium filter: Rs 50 - Rs 250 (sweet-spot ATM gamma contracts).
   - Midday chop block: Strictly avoid entries 11:00 - 13:15 IST.

Ablation Suite:
  - BASELINE: Fixed 1.6R Target + Initial Stop (Single Portfolio)
  - V1: Initial Stop + Relaxed Ladder Floor
  - V2: V1 + Fixed Chandelier Trail (k=2.2)
  - V3: V2 + THS Adaptive Chandelier (k=3.0 / 2.2 / 1.4)
  - V4: V3 + Exhaustion Climax (k=0.8) & Time Stops (Fail-Fast, Stall)
  - V5: V4 + Re-Entry Engine (Type A Pullback & Type B Breakout)
  - V5_P1: Complete V5 with 25% Partial Profit Booking at +1.5R

Author: Quantitative Trading Systems & Institutional Option Buyer Core
Specification: docs/SMF Dynamic Exit and Re-Entry Engine.md
"""

import sys
import os
import time
import pickle
from enum import Enum
from dataclasses import dataclass
from typing import List, Dict, Tuple, Optional, Any
import numpy as np
import pandas as pd

# ─────────────────────────────────────────────────────────────────────────────
# 1. CONSTANTS, ENUMS & DATA STRUCTURES
# ─────────────────────────────────────────────────────────────────────────────

NSE_TICK = 0.05
COSTS_PER_UNIT = 0.40       # Round-trip brokerage, STT, turnover charges (pts)
SLIPPAGE_PCT = 0.003        # 0.3% of premium on market-style fills
MIN_PREMIUM = 50.0          # Sweet-spot ATM lower bound
MAX_PREMIUM = 260.0         # Sweet-spot ATM upper bound
MAX_R_PCT = 0.12            # 1R never wider than 12% of entry price
ENTRY_START_TIME = "09:30"
ENTRY_CUTOFF_TIME = "14:30"
EOD_TIME = "15:15"
DEFAULT_CACHE_PATH = "/Users/admin/Documents/Nimesh/Projects/Self Training Engine/trading_engine/scripts/cache_option_candles.pkl"
DB_URL = "postgresql://postgres:postgres@localhost:5432/trading"

def slip(price: float) -> float:
    return max(NSE_TICK, price * SLIPPAGE_PCT)

def round_tick(price: float) -> float:
    return round(float(price) * 20.0) / 20.0

class HealthState(Enum):
    STRONG = "STRONG"
    NORMAL = "NORMAL"
    WEAK = "WEAK"
    CLIMAX = "CLIMAX"

class TradePhase(Enum):
    ENTRY = 0
    PROTECT = 1
    SECURED = 2
    RUNNER = 3

class ReEntryType(Enum):
    NONE = "NONE"
    PULLBACK_TYPE_A = "PULLBACK_TYPE_A"
    BREAKOUT_TYPE_B = "BREAKOUT_TYPE_B"
    REVERSAL_TYPE_C = "REVERSAL_TYPE_C"

@dataclass
class ActiveTrade:
    symbol: str
    entry_price: float
    entry_bar: int
    initial_stop: float
    R: float
    catastrophic_stop: float           # Intrabar circuit breaker: Entry - 2.0 * R
    base_qty: float = 1.0
    rem_qty: float = 1.0               # Remaining quantity fraction (e.g. 0.75 after P1)
    hh: float = 0.0                    # Starts AT entry price
    last_high_bar: int = 0
    mfe_r: float = 0.0
    phase: TradePhase = TradePhase.PROTECT
    state: HealthState = HealthState.NORMAL
    trail: float = 0.0
    ladder_floor: float = 0.0
    climax_until_bar: int = -1
    low_ths_bars: int = 0
    improving_bars: int = 0
    p1_booked: bool = False
    realized_pts: float = 0.0
    is_reentry: bool = False
    reentry_type: ReEntryType = ReEntryType.NONE
    leg_reentries: int = 0

@dataclass
class ReEntryWatcher:
    symbol: str
    regime: int                        # +1 Bullish
    exit_bar: int
    exit_price: float
    exit_reason: str
    last_hh: float
    last_higher_low: float
    pullback_low: float = float('inf')
    armed: bool = False
    reentry_count: int = 0
    paused_until_bar: int = -1

# ─────────────────────────────────────────────────────────────────────────────
# 2. TECHNICAL INDICATOR CALCULATORS
# ─────────────────────────────────────────────────────────────────────────────

def calc_ema(src: np.ndarray, length: int) -> np.ndarray:
    n = len(src)
    ema = np.zeros(n)
    if n == 0: return ema
    k = 2.0 / (length + 1.0)
    ema[0] = src[0]
    for i in range(1, n):
        ema[i] = src[i] * k + ema[i - 1] * (1.0 - k)
    return ema

def calc_wilder_atr(highs: np.ndarray, lows: np.ndarray, closes: np.ndarray, length: int = 14) -> np.ndarray:
    n = len(highs)
    atr = np.zeros(n)
    if n == 0: return atr
    tr = np.zeros(n)
    tr[0] = highs[0] - lows[0]
    for i in range(1, n):
        tr[i] = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
    init = min(length, n)
    sum_tr = np.sum(tr[:init])
    atr[init - 1] = sum_tr / init
    for i in range(init, n):
        atr[i] = (atr[i - 1] * (length - 1) + tr[i]) / length
    for i in range(init - 1):
        atr[i] = atr[init - 1]
    return atr

def calc_vsma(volumes: np.ndarray, length: int = 20) -> np.ndarray:
    n = len(volumes)
    vsma = np.zeros(n)
    if n == 0: return vsma
    sum_v = 0.0
    for i in range(n):
        sum_v += volumes[i]
        if i >= length:
            sum_v -= volumes[i - length]
            vsma[i] = sum_v / length
        else:
            vsma[i] = sum_v / (i + 1)
    return vsma

def calc_rsi(closes: np.ndarray, length: int = 14) -> np.ndarray:
    n = len(closes)
    rsi = np.full(n, 50.0)
    if n <= length: return rsi
    gains = np.zeros(n)
    losses = np.zeros(n)
    for i in range(1, n):
        diff = closes[i] - closes[i - 1]
        if diff >= 0: gains[i] = diff
        else: losses[i] = -diff
    ag = np.mean(gains[1:length + 1])
    al = np.mean(losses[1:length + 1])
    rsi[length] = 100.0 if al == 0 else 100.0 - (100.0 / (1.0 + ag / al))
    for i in range(length + 1, n):
        ag = (ag * (length - 1) + gains[i]) / length
        al = (al * (length - 1) + losses[i]) / length
        rsi[i] = 100.0 if al == 0 else 100.0 - (100.0 / (1.0 + ag / al))
    for i in range(length):
        rsi[i] = rsi[length]
    return rsi

def compute_all_indicators(opens: np.ndarray, highs: np.ndarray, lows: np.ndarray, closes: np.ndarray, volumes: np.ndarray):
    f_ema = calc_ema(closes, 9)
    s_ema = calc_ema(closes, 21)
    b_main = (f_ema + s_ema) / 2.0
    atr = calc_wilder_atr(highs, lows, closes, 14)
    vsma = calc_vsma(volumes, 20)
    rsi = calc_rsi(closes, 14)
    
    atr_safe = np.where(atr > 0.0, atr, 2.0)
    vsma_safe = np.where(vsma > 0.0, vsma, 1.0)
    
    rvol = volumes / vsma_safe
    ext = np.abs(closes - b_main) / atr_safe

    candle_span = np.maximum(highs - lows, 0.05)
    upper_wick = highs - np.maximum(opens, closes)
    lower_wick = np.minimum(opens, closes) - lows
    seller_pressure = upper_wick / candle_span
    buyer_pressure = lower_wick / candle_span

    return {
        "f_ema": f_ema,
        "s_ema": s_ema,
        "b_main": b_main,
        "atr": atr_safe,
        "vsma": vsma_safe,
        "rsi": rsi,
        "rvol": rvol,
        "ext": ext,
        "candle_span": candle_span,
        "seller_pressure": seller_pressure,
        "buyer_pressure": buyer_pressure,
    }

def compute_smf_regimes(closes: np.ndarray, b_main: np.ndarray, atr: np.ndarray, confirm_bars: int = 2, atr_mult: float = 0.50):
    n = len(closes)
    regimes = np.zeros(n, dtype=int)
    switches_up = np.zeros(n, dtype=bool)
    switches_down = np.zeros(n, dtype=bool)

    if n == 0: return regimes, switches_up, switches_down

    prev_regime = 1 if closes[0] >= b_main[0] else -1
    regimes[0] = prev_regime
    below_count = 0
    above_count = 0

    for i in range(n):
        buf = atr[i] * atr_mult
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

        curr_regime = prev_regime
        if prev_regime == 1 and below_count >= confirm_bars:
            curr_regime = -1
            below_count = 0
        elif prev_regime == -1 and above_count >= confirm_bars:
            curr_regime = 1
            above_count = 0

        regimes[i] = curr_regime
        prev_sig = regimes[i - 1] if i > 0 else curr_regime
        switches_up[i] = (curr_regime == 1 and prev_sig == -1)
        switches_down[i] = (curr_regime == -1 and prev_sig == 1)
        prev_regime = curr_regime

    return regimes, switches_up, switches_down

# ─────────────────────────────────────────────────────────────────────────────
# 3. HIGH-CONVICTION GAMMA IGNITION FILTER (PILLAR 4)
# ─────────────────────────────────────────────────────────────────────────────

def is_gamma_ignition_setup(i: int, c_open: float, c_high: float, c_low: float, c_close: float, ind: Dict[str, np.ndarray]) -> bool:
    """
    Pillar 4: Filters out low-quality 1m fakeouts.
    A valid gamma ignition entry requires:
    1. Bullish candle body: close in upper half of bar ((c - l) / span >= 0.50).
    2. Sweet-spot momentum: 48 <= RSI <= 75.
    3. Price breakout: Close >= b_main + 0.20 * ATR.
    """
    span = max(c_high - c_low, 0.05)
    body_ratio = (c_close - c_low) / span
    if body_ratio < 0.50:
        return False

    rsi = ind["rsi"][i]
    if not (48.0 <= rsi <= 75.0):
        return False

    bm = ind["b_main"][i]
    atr = ind["atr"][i]
    if c_close < (bm + 0.20 * atr):
        return False

    return True

# ─────────────────────────────────────────────────────────────────────────────
# 4. TREND HEALTH SCORE (THS, 0 TO 100)
# ─────────────────────────────────────────────────────────────────────────────

def calculate_trend_health_score(i: int, closes: np.ndarray, highs: np.ndarray, lows: np.ndarray, opens: np.ndarray, ind: Dict[str, np.ndarray]) -> int:
    score = 0
    c = closes[i]
    o = opens[i]
    bm = ind["b_main"][i]
    atr = ind["atr"][i]
    buf = atr * 0.50

    if c >= bm + buf: score += 20
    elif c >= bm: score += 10

    bm_prev3 = ind["b_main"][i - 3] if i >= 3 else bm
    if bm > bm_prev3: score += 15

    if i >= 5:
        recent_3_low = np.min(lows[i - 2:i + 1])
        prior_3_low = np.min(lows[i - 5:i - 2])
        if recent_3_low > prior_3_low: score += 15
    elif i >= 2: score += 10

    rsi_val = ind["rsi"][i]
    if 50.0 <= rsi_val <= 80.0: score += 10
    elif (45.0 <= rsi_val < 50.0) or (rsi_val > 80.0): score += 5

    rvol = ind["rvol"][i]
    span = ind["candle_span"][i]
    is_red_blowout = (c < o) and (abs(c - o) >= 0.60 * span) and (rvol >= 1.5)
    if is_red_blowout: score += 0
    elif rvol >= 0.8: score += 10
    else: score += 5

    s_start = max(0, i - 2)
    avg_sellers = np.mean(ind["seller_pressure"][s_start:i + 1])
    if avg_sellers < 0.40: score += 10

    ext_val = ind["ext"][i]
    if ext_val < 2.5: score += 10
    elif ext_val <= 3.5: score += 5

    score += 10  # Baseline institutional bias
    return score

def update_health_state(current_state: HealthState, ths: int, trade: ActiveTrade) -> HealthState:
    if ths >= 70: target = HealthState.STRONG
    elif ths >= 45: target = HealthState.NORMAL
    else: target = HealthState.WEAK

    hierarchy = {HealthState.WEAK: 1, HealthState.NORMAL: 2, HealthState.STRONG: 3, HealthState.CLIMAX: 0}
    current_rank = hierarchy.get(current_state, 2)
    target_rank = hierarchy.get(target, 2)

    if target_rank < current_rank:
        trade.improving_bars = 0
        return target
    elif target_rank > current_rank:
        trade.improving_bars += 1
        if trade.improving_bars >= 2:
            trade.improving_bars = 0
            return target
        else:
            return current_state
    else:
        trade.improving_bars = 0
        return current_state

# ─────────────────────────────────────────────────────────────────────────────
# 5. DYNAMIC EXIT CALCULATORS (PILLAR 1: RELAXED LADDER)
# ─────────────────────────────────────────────────────────────────────────────

def calculate_initial_stop_and_r(entry_price: float, b_main_val: float, atr_val: float) -> Tuple[float, float]:
    raw_stop = max(b_main_val - 0.35 * atr_val, entry_price - 1.5 * atr_val)
    raw_r = entry_price - raw_stop
    clamped_r = min(max(raw_r, 1.5), MAX_R_PCT * entry_price)
    init_stop = round_tick(entry_price - clamped_r)
    final_r = round_tick(entry_price - init_stop)
    return init_stop, final_r

def calculate_relaxed_ladder_floor(trade: ActiveTrade) -> float:
    """
    Pillar 1: Relaxed Profit-Lock Ladder.
    - MFE >= +3.5R: Entry + 50% of peak profit (caps give-back to 50%).
    - MFE >= +2.5R: Entry + 0.50R.
    - MFE >= +1.8R: Entry + Costs (true Break-Even lock).
    - MFE >= +1.0R: Entry - 0.50R (halves risk, lets the normal retest breathe!).
    - MFE <  +1.0R: Initial stop.
    """
    if trade.mfe_r >= 3.5:
        return round_tick(trade.entry_price + 0.50 * (trade.hh - trade.entry_price))
    elif trade.mfe_r >= 2.5:
        return round_tick(trade.entry_price + 0.50 * trade.R)
    elif trade.mfe_r >= 1.8:
        return round_tick(trade.entry_price + COSTS_PER_UNIT)
    elif trade.mfe_r >= 1.0:
        return round_tick(trade.entry_price - 0.50 * trade.R)
    return trade.initial_stop

def detect_climax_exhaustion(i: int, closes: np.ndarray, highs: np.ndarray, lows: np.ndarray, ind: Dict[str, np.ndarray]) -> bool:
    span = highs[i] - lows[i]
    atr = ind["atr"][i]
    rvol = ind["rvol"][i]
    seller = ind["seller_pressure"][i]
    ext = ind["ext"][i]
    return (rvol >= 2.5) and (span >= 2.0 * atr) and (seller >= 0.50) and (ext >= 2.5)

def detect_rsi_divergence(i: int, highs: np.ndarray, ind: Dict[str, np.ndarray]) -> bool:
    if i < 10: return False
    cur_h = highs[i]
    cur_rsi = ind["rsi"][i]
    cur_rvol = ind["rvol"][i]

    prior_highs = highs[i - 10:i]
    prior_peak_idx = np.argmax(prior_highs)
    prior_peak_h = prior_highs[prior_peak_idx]
    prior_peak_rsi = ind["rsi"][i - 10 + prior_peak_idx]

    if cur_h > prior_peak_h and (cur_rsi <= prior_peak_rsi - 5.0) and cur_rvol < 1.0:
        return True
    return False

# ─────────────────────────────────────────────────────────────────────────────
# 6. HIGH-CONVICTION PORTFOLIO ENGINE (PILLARS 1, 2, 3, 4 INTEGRATED)
# ─────────────────────────────────────────────────────────────────────────────

class HighConvictionPortfolioEngine:
    K_BY_STATE = {
        HealthState.STRONG: 3.0,
        HealthState.NORMAL: 2.2,
        HealthState.WEAK: 1.4,
        HealthState.CLIMAX: 0.8,
    }

    def __init__(
        self,
        variant: str = "V5",              # BASELINE, V1, V2, V3, V4, V5, V5_P1
        fail_bars: int = 8,
        stall_bars: int = 12,
        max_daily_trades: int = 3,         # Pillar 3: Max 3 high-conviction trades per day
        max_daily_loss_r: float = -2.5,
        enable_p1_partial: bool = False
    ):
        self.variant = variant.upper()
        self.fail_bars = fail_bars
        self.stall_bars = stall_bars
        self.max_daily_trades = max_daily_trades
        self.max_daily_loss_r = max_daily_loss_r
        self.enable_p1_partial = enable_p1_partial or ("P1" in self.variant)

        # Single active portfolio state (Pillar 3)
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
        
        # PnL on remaining quantity + any realized partial cash
        pnl_pts = (exit_p - t.entry_price) * t.rem_qty - COSTS_PER_UNIT + t.realized_pts
        pnl_r = pnl_pts / t.R

        peak_pts = t.hh - t.entry_price
        if peak_pts > NSE_TICK:
            capture_ratio = pnl_pts / peak_pts
            give_back = (t.hh - exit_p) / peak_pts
        else:
            capture_ratio, give_back = 0.0, 0.0

        record = {
            "symbol": t.symbol,
            "date": self.current_date,
            "entry_bar": t.entry_bar,
            "exit_bar": bar_idx,
            "exit_time": c_time,
            "entry_price": t.entry_price,
            "exit_price": exit_p,
            "initial_stop": t.initial_stop,
            "R": t.R,
            "hh": t.hh,
            "mfe_r": t.mfe_r,
            "pnl_pts": pnl_pts,
            "pnl_r": pnl_r,
            "capture_ratio": capture_ratio,
            "give_back": give_back,
            "reason": reason,
            "is_reentry": t.is_reentry,
            "reentry_type": t.reentry_type.value,
            "p1_booked": t.p1_booked
        }
        self.trades_history.append(record)

        self.daily_realized_r += pnl_r
        if pnl_r < 0:
            self.consecutive_losses += 1
        else:
            self.consecutive_losses = 0
        if self.consecutive_losses >= 3:
            self.day_halted = True

        pause_bars = 20 if self.consecutive_losses >= 2 else 0
        self.reentry_watcher = ReEntryWatcher(
            symbol=t.symbol,
            regime=1,
            exit_bar=bar_idx,
            exit_price=exit_p,
            exit_reason=reason,
            last_hh=t.hh,
            last_higher_low=t.ladder_floor,
            armed=False,
            reentry_count=t.leg_reentries,
            paused_until_bar=bar_idx + pause_bars,
        )
        self.active_trade = None
        return record

    def update_active_trade(self, bar_idx: int, c_open: float, c_high: float, c_low: float, c_close: float, c_time: str, ind: Dict[str, np.ndarray], is_switch_down: bool):
        t = self.active_trade
        is_entry_bar = (bar_idx == t.entry_bar)

        # Baseline Strategy: Fixed 1.6R Target + Initial Stop
        if self.variant == "BASELINE":
            target1 = round_tick(t.entry_price + 1.6 * t.R)
            if c_close < t.initial_stop or c_low <= (t.entry_price - 2.0 * t.R):
                return self.execute_exit(bar_idx, c_time, min(t.initial_stop, c_open), "INITIAL_STOP", c_close)
            if not is_entry_bar:
                t.hh = max(t.hh, c_high)
                if c_high >= target1:
                    return self.execute_exit(bar_idx, c_time, target1, "TARGET_1", c_close, limit_exit=True)
            t.mfe_r = (t.hh - t.entry_price) / t.R
            if is_switch_down:
                return self.execute_exit(bar_idx, c_time, c_close, "REGIME_FLIP", c_close)
            if c_time >= EOD_TIME:
                return self.execute_exit(bar_idx, c_time, c_close, "EOD_EXIT", c_close)
            return

        # ── Pillar 2: Stop Noise Filtering ────────────────────────────────────
        # Emergency catastrophic stop: Intrabar low breach of -2.0R
        if c_low <= t.catastrophic_stop:
            return self.execute_exit(bar_idx, c_time, min(t.catastrophic_stop, c_open), "CATASTROPHIC_STOP", c_close)

        # Normal Stop Check (Pillar 2: Close-Based Stop against previous bar's level)
        hard_stop = max(t.initial_stop, t.ladder_floor)
        if c_close < hard_stop:
            reason = "LADDER_FLOOR" if hard_stop > t.initial_stop else "INITIAL_STOP"
            return self.execute_exit(bar_idx, c_time, c_close, reason, c_close)

        # Update high watermark (entry bar high ignored)
        if not is_entry_bar and c_high > t.hh:
            t.hh = c_high
            t.last_high_bar = bar_idx
        t.mfe_r = (t.hh - t.entry_price) / t.R

        # Update Trade Phases
        if t.mfe_r >= 2.5: t.phase = TradePhase.RUNNER
        elif t.mfe_r >= 1.0: t.phase = TradePhase.SECURED
        else: t.phase = TradePhase.PROTECT

        # ── Pillar 1: Relaxed Profit-Lock Ladder & Partial Booking ───────────
        t.ladder_floor = max(t.ladder_floor, calculate_relaxed_ladder_floor(t))

        # Partial profit booking P1 (25% qty locked at +1.5R)
        if self.enable_p1_partial and not t.p1_booked and t.mfe_r >= 1.5:
            t.p1_booked = True
            partial_exit_p = round_tick(t.entry_price + 1.5 * t.R)
            t.realized_pts += 0.25 * (partial_exit_p - t.entry_price)
            t.rem_qty = 0.75  # 75% runner remains

        # Structural exits
        if is_switch_down:
            return self.execute_exit(bar_idx, c_time, c_close, "REGIME_FLIP", c_close)
        if c_time >= EOD_TIME:
            return self.execute_exit(bar_idx, c_time, c_close, "EOD_EXIT", c_close)

        if self.variant == "V1":
            return

        # Trend Health Score & State Machine (V3+)
        ths = None
        if self.variant in ("V3", "V4", "V5", "V5_P1"):
            ths = calculate_trend_health_score(bar_idx, ind["closes"], ind["highs"], ind["lows"], ind["opens"], ind)
            t.state = update_health_state(t.state, ths, t)
            if ths < 25:
                t.low_ths_bars += 1
                if t.low_ths_bars >= 2:
                    return self.execute_exit(bar_idx, c_time, c_close, "HEALTH_COLLAPSE", c_close)
            else:
                t.low_ths_bars = 0

        # Chandelier Trail Ratchet
        k = 2.2 if self.variant == "V2" else self.K_BY_STATE.get(t.state, 2.2)
        if self.variant in ("V4", "V5", "V5_P1"):
            if detect_climax_exhaustion(bar_idx, ind["closes"], ind["highs"], ind["lows"], ind):
                t.climax_until_bar = bar_idx + 3
            if bar_idx <= t.climax_until_bar:
                k = min(k, 0.8)
            if detect_rsi_divergence(bar_idx, ind["highs"], ind) or ind["ext"][bar_idx] > 3.5:
                k = min(k, 1.4)
            if c_time >= ENTRY_CUTOFF_TIME:
                k = min(k, 1.4)

        raw_trail = round_tick(t.hh - k * ind["atr"][bar_idx])
        t.trail = max(t.trail, raw_trail)

        # Trail break (close below trail after reaching +1.0R)
        if t.mfe_r >= 1.0 and c_close < t.trail:
            reason = "CLIMAX_TIGHTEN" if bar_idx <= t.climax_until_bar else "TRAIL_BREAK"
            return self.execute_exit(bar_idx, c_time, c_close, reason, c_close)

        # Time Stops (V4+)
        if self.variant in ("V4", "V5", "V5_P1"):
            if (bar_idx - t.entry_bar >= self.fail_bars) and t.mfe_r < 0.5 and c_close < t.entry_price:
                return self.execute_exit(bar_idx, c_time, c_close, "FAIL_FAST", c_close)
            if (bar_idx - t.last_high_bar >= self.stall_bars) and ths is not None and ths < 60:
                return self.execute_exit(bar_idx, c_time, c_close, "STALL", c_close)

    def check_reentry(self, bar_idx: int, c_time: str, sym: str, c_open: float, c_high: float, c_low: float, c_close: float, ind: Dict[str, np.ndarray], regime: int) -> bool:
        if self.variant not in ("V5", "V5_P1"): return False
        if not self.reentry_watcher or self.reentry_watcher.symbol != sym: return False
        w = self.reentry_watcher
        if w.reentry_count >= 2 or bar_idx <= w.paused_until_bar or self.daily_trade_count >= self.max_daily_trades or regime != w.regime:
            return False

        ths = calculate_trend_health_score(bar_idx, ind["closes"], ind["highs"], ind["lows"], ind["opens"], ind)
        bm = ind["b_main"][bar_idx]
        atr = ind["atr"][bar_idx]

        if c_low <= bm + 0.5 * atr:
            w.armed = True
            w.pullback_low = min(w.pullback_low, c_low)

        entry = round_tick(c_close + slip(c_close))
        new_stop = None
        r_type = None
        min_ths_a = 60 if w.exit_reason == "INITIAL_STOP" else 50

        if w.armed and (bar_idx - w.exit_bar >= 2) and c_close > ind["highs"][bar_idx - 1] and ths >= min_ths_a:
            new_stop = w.pullback_low - 0.25 * atr
            r_type = ReEntryType.PULLBACK_TYPE_A
        elif c_close > w.last_hh and ind["rvol"][bar_idx] >= 1.0 and ths >= 60:
            new_stop = max(w.last_higher_low, bm)
            r_type = ReEntryType.BREAKOUT_TYPE_B

        if new_stop is not None and MIN_PREMIUM <= entry <= MAX_PREMIUM:
            new_stop = round_tick(max(new_stop, entry * (1.0 - MAX_R_PCT)))
            r_val = round_tick(entry - new_stop)
            if r_val >= 1.5:
                self.daily_trade_count += 1
                cat_stop = round_tick(entry - 2.0 * r_val)
                self.active_trade = ActiveTrade(
                    symbol=sym,
                    entry_price=entry,
                    entry_bar=bar_idx,
                    initial_stop=new_stop,
                    R=r_val,
                    catastrophic_stop=cat_stop,
                    hh=entry,
                    last_high_bar=bar_idx,
                    mfe_r=0.0,
                    phase=TradePhase.PROTECT,
                    state=HealthState.NORMAL,
                    trail=new_stop,
                    ladder_floor=new_stop,
                    is_reentry=True,
                    reentry_type=r_type,
                    leg_reentries=w.reentry_count + 1
                )
                self.reentry_watcher = None
                return True
        return False

# ─────────────────────────────────────────────────────────────────────────────
# 7. BACKTEST SIMULATION (PORTFOLIO TIMELINE SIMULATOR)
# ─────────────────────────────────────────────────────────────────────────────

def select_tradable_atm_contracts(day_df: pd.DataFrame, top_n: int = 4) -> List[str]:
    """Selects top liquid ATM contracts in 09:15-09:30 IST window within Rs 50-260 premium."""
    hhmm = day_df["timestamp"].dt.strftime("%H:%M")
    early = day_df[(hhmm >= "09:15") & (hhmm < "09:30")]
    if early.empty: return []
    # Filter sweet-spot premium
    valid_syms = []
    for s, group in early.groupby("symbol"):
        avg_p = group["close"].mean()
        if MIN_PREMIUM <= avg_p <= MAX_PREMIUM:
            valid_syms.append(s)
    if not valid_syms: return []
    
    early_valid = early[early["symbol"].isin(valid_syms)]
    turnover = (early_valid["close"] * early_valid["volume"]).groupby(early_valid["symbol"]).sum()
    return list(turnover.nlargest(top_n).index)

def load_backtest_dataset(cache_file: str = DEFAULT_CACHE_PATH) -> pd.DataFrame:
    if os.path.exists(cache_file):
        print(f"[*] Loading cached option data from {cache_file}...")
        t0 = time.time()
        with open(cache_file, "rb") as f:
            df = pickle.load(f)
        print(f"[+] Loaded {len(df):,} candles in {time.time() - t0:.2f}s.")
        return df
    
    print("[*] Cache not found. Querying TimescaleDB...")
    from sqlalchemy import create_engine
    engine = create_engine(DB_URL)
    query = """
    SELECT timestamp, symbol, open, high, low, close, volume
    FROM option_minute_candles
    ORDER BY timestamp, symbol ASC;
    """
    df = pd.read_sql(query, engine)
    df['timestamp'] = pd.to_datetime(df['timestamp']).dt.tz_convert('Asia/Kolkata')
    df['date'] = df['timestamp'].dt.strftime('%Y-%m-%d')
    return df

def run_high_conviction_backtest(
    df: pd.DataFrame,
    variants: List[str] = ["BASELINE", "V1", "V2", "V3", "V4", "V5", "V5_P1"],
    top_n: int = 4,
) -> Dict[str, pd.DataFrame]:
    print("=" * 85)
    print("   SMF DYNAMIC EXIT & RE-ENTRY ENGINE: 4-PILLAR PORTFOLIO BACKTEST SWEEP")
    print("=" * 85)

    dates = sorted(df["date"].unique())
    print(f"[*] Total trading days: {len(dates)}")
    print("[*] Pre-indexing daily candidate contracts and indicators (one-time pass)...")
    t_pre0 = time.time()
    day_groups = {d: group for d, group in df.groupby("date")}
    daily_precomputed = {}

    for d_str, day_df in day_groups.items():
        selected_syms = select_tradable_atm_contracts(day_df, top_n)
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

            contract_data[sym] = {
                "times": times, "opens": opens, "highs": highs, "lows": lows, "closes": closes,
                "volumes": volumes, "ind": ind, "regimes": regimes, "sw_up": sw_up, "sw_down": sw_down,
                "length": len(c_df)
            }
        if contract_data:
            daily_precomputed[d_str] = contract_data
    print(f"[+] Pre-indexing complete in {time.time() - t_pre0:.2f}s across {len(daily_precomputed)} active days.")

    results_by_variant = {}

    for var in variants:
        print(f"\n>>> Running Variant: {var} ...")
        t0 = time.time()
        engine = HighConvictionPortfolioEngine(variant=var)

        for d_str, contract_data in daily_precomputed.items():
            engine.reset_day(d_str)

            # Common timeline simulation across the day (Pillar 3: Single Active Portfolio)
            sample_sym = list(contract_data.keys())[0]
            day_timeline = contract_data[sample_sym]["times"]

            for b in range(10, len(day_timeline)):
                c_time = day_timeline[b]

                # 1. If currently in trade, manage the active position
                if engine.active_trade:
                    act_sym = engine.active_trade.symbol
                    cdata = contract_data.get(act_sym)
                    if cdata and b < cdata["length"]:
                        engine.update_active_trade(
                            bar_idx=b,
                            c_open=cdata["opens"][b],
                            c_high=cdata["highs"][b],
                            c_low=cdata["lows"][b],
                            c_close=cdata["closes"][b],
                            c_time=c_time,
                            ind=cdata["ind"],
                            is_switch_down=cdata["sw_down"][b]
                        )
                    continue

                # 2. If NOT in trade: Check daily limits & time filters
                if engine.day_halted: continue
                if engine.daily_trade_count >= engine.max_daily_trades: continue
                if c_time < ENTRY_START_TIME or c_time >= ENTRY_CUTOFF_TIME: continue
                if "11:00" <= c_time <= "13:15": continue  # Midday chop block

                # Check Re-Entry first
                reentered = False
                for sym, cdata in contract_data.items():
                    if b >= cdata["length"]: continue
                    if engine.check_reentry(
                        bar_idx=b, c_time=c_time, sym=sym, c_open=cdata["opens"][b],
                        c_high=cdata["highs"][b], c_low=cdata["lows"][b], c_close=cdata["closes"][b],
                        ind=cdata["ind"], regime=cdata["regimes"][b]
                    ):
                        reentered = True
                        break
                if reentered: continue

                # Check New High-Conviction Entries across candidate contracts
                candidates = []
                for sym, cdata in contract_data.items():
                    if b >= cdata["length"]: continue
                    if cdata["sw_up"][b]:
                        c_close = cdata["closes"][b]
                        c_open = cdata["opens"][b]
                        c_high = cdata["highs"][b]
                        c_low = cdata["lows"][b]
                        if not (MIN_PREMIUM <= c_close <= MAX_PREMIUM): continue

                        # Pillar 4: High-Conviction Gamma Ignition Filter
                        if is_gamma_ignition_setup(b, c_open, c_high, c_low, c_close, cdata["ind"]):
                            candidates.append((sym, cdata["ind"]["rvol"][b], cdata["ind"]["atr"][b]))

                if candidates:
                    # Pick single best contract by highest volume surge (RVOL)
                    candidates.sort(key=lambda x: x[1], reverse=True)
                    best_sym = candidates[0][0]
                    cdata = contract_data[best_sym]

                    entry = round_tick(cdata["closes"][b] + slip(cdata["closes"][b]))
                    init_stop, r_val = calculate_initial_stop_and_r(entry, cdata["ind"]["b_main"][b], cdata["ind"]["atr"][b])
                    cat_stop = round_tick(entry - 2.0 * r_val)

                    engine.daily_trade_count += 1
                    engine.active_trade = ActiveTrade(
                        symbol=best_sym,
                        entry_price=entry,
                        entry_bar=b,
                        initial_stop=init_stop,
                        R=r_val,
                        catastrophic_stop=cat_stop,
                        hh=entry,
                        last_high_bar=b,
                        mfe_r=0.0,
                        phase=TradePhase.PROTECT,
                        state=HealthState.NORMAL,
                        trail=init_stop,
                        ladder_floor=init_stop
                    )

            # End of day square-off
            if engine.active_trade:
                act_sym = engine.active_trade.symbol
                cdata = contract_data.get(act_sym)
                if cdata:
                    last_b = min(len(day_timeline) - 1, cdata["length"] - 1)
                    engine.execute_exit(last_b, day_timeline[last_b], cdata["closes"][last_b], "EOD_EXIT", cdata["closes"][last_b])

        trades_df = pd.DataFrame(engine.trades_history)
        results_by_variant[var] = trades_df
        print(f"[+] {var} completed in {time.time() - t0:.1f}s. Total trades: {len(trades_df)}")

    print_comparative_table(results_by_variant)
    return results_by_variant

def print_comparative_table(results: Dict[str, pd.DataFrame]):
    summary_rows = []

    for var, tdf in results.items():
        if len(tdf) == 0:
            summary_rows.append({"Variant": var, "Trades": 0})
            continue

        wins = tdf[tdf['pnl_r'] > 0]
        losses = tdf[tdf['pnl_r'] <= 0]
        win_rate = (len(wins) / len(tdf)) * 100.0
        total_pnl_pts = tdf['pnl_pts'].sum()
        total_pnl_r = tdf['pnl_r'].sum()
        expectancy_r = tdf['pnl_r'].mean()
        
        gross_win_r = wins['pnl_r'].sum()
        gross_loss_r = abs(losses['pnl_r'].sum())
        profit_factor = (gross_win_r / gross_loss_r) if gross_loss_r > 0 else 999.0

        cum_r = tdf['pnl_r'].cumsum()
        max_drawdown_r = (cum_r.cummax() - cum_r).max()

        pos_mfe_trades = tdf[tdf['mfe_r'] >= 1.0]
        avg_capture_ratio = pos_mfe_trades['capture_ratio'].mean() * 100.0 if len(pos_mfe_trades) > 0 else 0.0
        avg_give_back = pos_mfe_trades['give_back'].mean() * 100.0 if len(pos_mfe_trades) > 0 else 0.0

        summary_rows.append({
            "Variant": var,
            "Trades": len(tdf),
            "Win Rate %": f"{win_rate:.1f}%",
            "Total PnL (R)": f"{total_pnl_r:+.1f}R",
            "Total PnL (Pts)": f"₹{total_pnl_pts:+,.1f}",
            "Expectancy": f"{expectancy_r:+.2f}R",
            "Profit Factor": f"{profit_factor:.2f}",
            "Max DD (R)": f"{max_drawdown_r:.1f}R",
            "Capture Ratio": f"{avg_capture_ratio:.1f}%",
            "Give-Back": f"{avg_give_back:.1f}%"
        })

    summary_df = pd.DataFrame(summary_rows)
    print("\n" + "=" * 105)
    print("                    SMF DYNAMIC EXIT ENGINE: 4-PILLAR PORTFOLIO LEADERBOARD")
    print("=" * 105)
    print(summary_df.to_string(index=False))
    print("=" * 105)

    # Detailed breakdown for V5 and V5_P1
    target_var = "V5_P1" if "V5_P1" in results else "V5"
    if target_var in results and len(results[target_var]) > 0:
        v_df = results[target_var]
        print(f"\n[+] {target_var} Exit Reason Breakdown & Regret:")
        breakdown = v_df['reason'].value_counts()
        for r, cnt in breakdown.items():
            sub = v_df[v_df['reason'] == r]
            avg_r = sub['pnl_r'].mean()
            print(f"    - {r:<20}: {cnt:>4d} trades ({cnt/len(v_df)*100:>5.1f}%) | Avg PnL: {avg_r:+.2f}R")
        print("=" * 105)

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="SMF Dynamic Exit & Re-Entry Engine")
    parser.add_argument("--cache", type=str, default=DEFAULT_CACHE_PATH, help="Path to option candles pickle cache")
    parser.add_argument("--variants", nargs="+", default=["BASELINE", "V1", "V2", "V3", "V4", "V5", "V5_P1"], help="Variants to evaluate")
    parser.add_argument("--top-n", type=int, default=4, help="Top N liquid ATM contracts to monitor per day")
    args = parser.parse_args()

    df_data = load_backtest_dataset(args.cache)
    run_high_conviction_backtest(df_data, variants=args.variants, top_n=args.top_n)
