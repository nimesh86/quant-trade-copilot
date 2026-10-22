# SMF Dynamic Exit & Re-Entry Engine

*Draft v1 for backtesting. Extends the SMF Cloud strategy. Written for CE/PE option buying on Nifty with 1-minute entries.*

## 1. The problem and the core idea

Two mistakes cost most traders their edge: cutting a winner early (exit at 115, market goes to 215) and holding a loser while hoping it comes back. A fixed target at entry causes the first mistake, because it decides the exit before the market has said anything. Hope causes the second.

The fix is to stop predicting the exit and let the market take you out:

- The only price fixed at entry is the **initial stop**. It defines your risk, called **1R**.
- After entry the stop can only move in your favour (ratchet). It never moves down.
- The gap between price and stop is not fixed. It is wide while the trend is healthy and shrinks when the trend weakens or shows exhaustion.
- You exit when price breaks the stop or when trend health collapses, never because a guessed target was reached.
- After an exit the engine keeps watching and signals a re-entry if the trend resumes or reverses.

In one line: entry gives the direction, and every following bar answers one question: **HOLD or EXIT?**

## 2. The four phases of a trade

| Phase | Starts | Stop rule | Goal |
| :-- | :-- | :-- | :-- |
| 0 Entry | SMF flip, limit or market order | none yet | get filled at a sensible price |
| 1 Protect | fill | initial stop (hard, intrabar) | be wrong at a known small loss |
| 2 Secured | MFE ≥ +1R | stop moves to break-even, then up a ladder | remove the risk |
| 3 Runner | MFE ≥ +2R | chandelier trail + health score | stay in until the market ends the trend |

MFE (max favourable excursion) = the highest price reached since entry, measured in R.

## 3. Building blocks

### 3.1 Initial stop and R

Scale with ATR and premium, not with fixed rupee values.

```
InitialStop = max(bMain - 0.35 * ATR, Entry - 1.5 * ATR)
R           = Entry - InitialStop
Clamp       : R >= 1.5 points and R <= 12% of Entry
Round all prices to the 0.05 tick
```

Phase 1 uses an intrabar check (Low <= InitialStop means exit) because it is the capital guard.

### 3.2 Profit-lock ladder (a floor under the stop)

As the trade moves up, the lowest allowed stop moves up with it.

| MFE reached | Minimum stop |
| :-- | :-- |
| +1.0R | Entry + costs (about break-even) |
| +2.0R | Entry + 0.5R |
| +3.0R or more | Entry + 50% of the peak profit, i.e. Entry + 0.5 x (HH - Entry) |

The last row caps how much of the open profit can ever be given back (50% by default). The chandelier trail below usually sits higher in a strong trend, so the ladder is the safety net, not the main exit.

### 3.3 Chandelier trail (ratchet)

```
HH       = highest High since entry
TrailRaw = HH - k * ATR_trail
Trail    = max(Trail_previous, TrailRaw)   # ratchet: never moves down
```

| Health state | k (ATR multiple) |
| :-- | :-- |
| STRONG | 3.0 |
| NORMAL | 2.2 |
| WEAK | 1.4 |
| CLIMAX / exhaustion | 0.8 |

When health improves, k widens but the trail does not drop; it only waits for price to catch up. `ATR_trail` is the ATR(14) of the trade timeframe. Variant to test: take ATR and the exit decision from 5-minute bars while entering on 1-minute bars, so 1-minute noise is ignored.

**Exit triggers**

- Hard stop (intrabar): Low <= max(InitialStop, LadderFloor). Fill at that price minus slippage.
- Trail break (close-based): a bar **closes** below Trail. Exit at the close, or at the next open in the conservative test. A close-based trail ignores single-wick shakeouts, while the intrabar floor limits the damage.

### 3.4 Trend Health Score (THS, 0 to 100)

Calculated on every closed bar from the option candles, plus the Nifty futures regime.

| Component | Test | Points |
| :-- | :-- | :-- |
| Cloud position | Close >= bMain + Buffer: 20; Close >= bMain: 10; else 0 | 20 |
| Cloud slope | bMain now > bMain 3 bars ago | 15 |
| Structure | lowest Low of last 3 bars > lowest Low of the 3 bars before (higher lows) | 15 |
| Momentum | RSI 50 to 80: 10; RSI 45 to 50 or above 80: 5; else 0 | 10 |
| Participation | RVOL >= 0.8: 10 (quiet pullbacks are fine). A red bar with body >= 60% of range and RVOL >= 1.5: 0 | 10 |
| Candle pressure | average SellerPressure of last 3 bars < 0.40 | 10 |
| Extension | Ext < 2.5: 10; 2.5 to 3.5: 5; above 3.5: 0 | 10 |
| Underlying | futures regime agrees with the option side (CE: bullish, PE: bearish) | 10 |

| THS | State |
| :-- | :-- |
| 70 or more | STRONG |
| 45 to 69 | NORMAL |
| 25 to 44 | WEAK |
| below 25 on 2 consecutive closed bars | EXIT (health collapse) |

Hysteresis: the state degrades immediately but improves only after 2 consecutive bars, so it does not flicker.

### 3.5 Exhaustion tightening (booking near tops without predicting them)

- **Climax bar:** RVOL >= 2.5 AND range >= 2 x ATR AND SellerPressure >= 0.5 AND Ext >= 2.5. Set k = 0.8 for the next 3 bars. If partial booking is enabled, book half of the remaining quantity.
- **Divergence:** price makes a new high but RSI is at least 5 points below its previous swing high AND RVOL is below its 20-bar average. Cap k at 1.4.
- **Over-extension:** Ext > 3.5. Cap k at 1.4.

Tops often show a volume spike and a long upper wick. The engine tightens into them instead of guessing where they are.

### 3.6 Time-based stops

- **Fail-fast:** after FailBars (default 8 on 1m), if MFE < +0.5R and Close < Entry, exit. This cuts trades that are only bleeding theta.
- **Stall:** if bars since the last new high >= StallBars (default 12 on 1m) and THS < 60, exit.
- **After 14:30 IST:** no new entries or adds, and k is capped at 1.4.
- **15:15 IST:** square off everything.

### 3.7 Structural exits

- Confirmed SMF flip to bearish (isSwitchDown): exit at the close.
- Futures regime flips against the option side: exit at the close. The underlying usually turns before the premium reacts.
- Expiry day: same logic with a separate parameter set (smaller k, StallBars halved), because theta is much faster. This matches your normal-day vs expiry-day split.

### 3.8 Optional partial booking

Test three variants. P0: none, the whole position rides the trail. P1: 25% at +1.5R. P2: 33% at +2R. The remainder always rides the trail. If early exits are your main regret, P0 and P1 are the main candidates, and the backtest decides.

## 4. Live guidance: what the screen should say

One status line per closed bar, plus the trail drawn as a stepping line on the chart, so the question becomes: has the line been broken?

```
HOLD  STRONG  | THS 82 | Trail 194.3 | Locked +3.5R | MFE +9.2R
HOLD  NORMAL  | THS 58 | Trail 201.0 | Room before exit: Rs 6.4
TIGHTEN       | THS 34 | Trail 208.9 | Reason: climax bar, RSI divergence
EXIT          | Close below trail 208.9 | Result +9.1R
```

Also raise an alert whenever the state degrades (STRONG to NORMAL to WEAK).

## 5. Re-entry engine

After any exit the engine moves to WATCHING and keeps reading the cloud.

**A. Pullback re-entry (trend still intact).** The exit came from the trail, stall or fail-fast while the regime is still bullish. Wait for price to come within 0.5 x ATR of bMain (or a retest dot), then take the first bar that closes above the previous bar high with THS >= 50. Stop: pullback swing low minus 0.25 x ATR.

**B. Breakout re-entry (trend resumes without a pullback).** Close above the high of the previous trade (its HH) with RVOL >= 1.0 and THS >= 60. Stop: below the last higher low.

**C. Reversal re-entry (opposite side).** When the CE side flips bearish, check the PE (or let the futures flip choose the side) and take its own SMF buy signal with the normal entry rules. If you watch only one contract chart, a reversal shows up as the same contract flipping up again, which the normal SMF entry already handles. Running the engine on both ATM CE and ATM PE is the cleanest setup.

**Rules**

- Cooldown of 2 bars after an exit before type A. Type B has none.
- Maximum 2 re-entries per trend leg (one leg = one regime period).
- Every re-entry is a new trade with its own initial stop, R and ladder.
- If the previous exit was a stop-loss rather than a trail, the next same-direction entry needs THS >= 60.
- After 2 consecutive losing trades, pause 20 bars. After 3, stop for the day.

## 6. Adding to winners (CONT, updated)

Allowed only when all of these hold: Phase 3, stop at or above break-even, THS >= 70, close above the previous swing high, RSI 50 to 80, at least 4 bars since the last add, fewer than 2 adds so far. Add size is at most 50% of the base quantity.

**House-money rule:** `add_qty x (AddPrice - Trail) <= profit already locked on the base position`. This way an add can never turn a locked winner into a net loser.

## 7. Daily risk controls

- Maximum 5 original entries per day, and at most 7 entries in total including re-entries.
- Daily loss limit, for example -3R, then stop for the day.
- 11:00 to 13:15: skip entries unless THS >= 75 and RVOL >= 1.35.
- No new entries after 14:30.

## 8. Worked example (illustrative numbers, not backtest results)

Entry ₹100, ATR 4, so R = 1.5 x 4 = 6 and the initial stop is ₹94.

| Price (HH) | Stop logic | Stop | Engine says |
| :-- | :-- | :-- | :-- |
| 106 (+1R) | ladder: break-even | 100 | HOLD |
| 112 (+2R) | ladder: +0.5R | 103 | HOLD |
| 115 (+2.5R) | trail 115 - 3x4 = 103, ladder 103 | 103 | HOLD, still in the trade (your manual exit was here) |
| 118 (+3R) | ladder: half of profit = 109, trail 106 | 109 | HOLD |
| 160 | ATR 6: trail 160 - 18 = 142, ladder 130 | 142 | HOLD STRONG |
| 215 | ATR 7: trail 215 - 21 = 194, ladder 157.5 | 194 | HOLD STRONG |
| 215, climax bar | k drops to 0.8: trail 215 - 5.6 | about 209 | TIGHTEN, exit on a close below 209 |

The exit lands near 205 to 209, so about ₹105 per unit captured versus ₹15 from the early exit. The price paid for this is the give-back from the peak (about ₹6 to ₹10 here). Trades that reverse early are handled too: a trade that reaches 112 and falls back exits near 103 instead of at a loss, and a trade that never gets going is cut by fail-fast or the initial stop at about -1R.

## 9. Parameters to sweep in the backtest

| Parameter | Default | Sweep range |
| :-- | :-- | :-- |
| k STRONG / NORMAL / WEAK / CLIMAX | 3.0 / 2.2 / 1.4 / 0.8 | 2.5 to 4.0 / 1.8 to 3.0 / 1.0 to 1.8 / 0.5 to 1.2 |
| Ladder profit share (3R and above) | 50% | 40% to 65% |
| Initial stop ATR multiple | 1.5 | 1.2 to 2.0 |
| R cap (percent of entry) | 12% | 8% to 15% |
| FailBars / StallBars (1m) | 8 / 12 | 5 to 12 / 8 to 20 |
| THS thresholds | 70 / 45 / 25 | plus or minus 10 |
| Partial booking | P0 | P0, P1, P2 |
| Trail timeframe | 1m | 1m or 5m |
| Trail trigger | close-based | close-based or intrabar |
| Re-entry types | A, B, C on | each on or off |
| Parameter sets | one set | separate sets for normal day and expiry day |

## 10. Backtest plan and metrics

**Variants (build up one piece at a time so you can see what each adds):**

- Baseline: current SMF fixed T1/T2 logic (both the production and the backtest formulas).
- V1: initial stop + ladder only.
- V2: V1 + chandelier trail with fixed k.
- V3: V2 + health-score adaptive k.
- V4: V3 + exhaustion and time stops.
- V5: V4 + re-entry engine.

**Execution assumptions**

- Limit entries fill only if price trades through the limit price.
- Exits fill at the next bar open or at stop minus slippage (use 0.2% to 0.5% of premium per side as a starting range).
- Include brokerage, STT, exchange charges and GST.
- If a bar touches both the stop and a higher price, assume the stop was hit first.

**Metrics**

- Expectancy (R per trade), profit factor, win rate, average win R vs average loss R, max drawdown, longest losing streak.
- **Capture ratio:** realized profit divided by the trade MFE profit. This directly measures the early-exit problem.
- **Give-back:** (peak - exit) divided by (peak - entry).
- **Regret:** how far price ran after your exit before the trend ended, in R.
- Exit-reason breakdown, time in trade, results by time window and by normal vs expiry day.

**Validation**

- Tune on the oldest 70% of data, validate on the next 15%, final test on the latest 15%. Walk forward month by month.
- Pick parameter regions where results are stable, not the single best cell.
- A day like 100 to 215 shows what is possible; the backtest shows what is typical. Judge k by capture ratio and drawdown together.
- Then paper trade for at least 4 weeks with the live status line, compare with the backtest, and only then use small size.

## 11. Pseudocode

```python
def ladder_floor(t):
    if t.mfe_r >= 3.0: return t.entry + 0.5 * (t.hh - t.entry)
    if t.mfe_r >= 2.0: return t.entry + 0.5 * t.R
    if t.mfe_r >= 1.0: return t.entry + COSTS
    return t.initial_stop

def manage_trade(t, c, i, ind, fut):
    # update extremes
    if c.high > t.hh:
        t.hh = c.high
        t.last_high_bar = i
    t.mfe_r = (t.hh - t.entry) / t.R

    # 1. hard stop, intrabar
    hard_stop = max(t.initial_stop, ladder_floor(t))
    if c.low <= hard_stop:
        return exit_trade(t, min(hard_stop, c.open) - SLIP, 'HARD_STOP')

    # 2. structural exits, on bar close
    if ind.is_switch_down:     return exit_trade(t, c.close, 'REGIME_FLIP')
    if fut_against(t, fut):    return exit_trade(t, c.close, 'FUTURES_FLIP')
    if is_eod(c):              return exit_trade(t, c.close, 'EOD')

    # 3. health score and state
    ths = trend_health(c, i, ind, fut)          # section 3.4
    t.state = update_state(t.state, ths)        # degrade now, improve after 2 bars
    t.low_ths = t.low_ths + 1 if ths < 25 else 0
    if t.low_ths >= 2:         return exit_trade(t, c.close, 'HEALTH_COLLAPSE')

    # 4. adaptive k and ratcheting trail
    k = {'STRONG': 3.0, 'NORMAL': 2.2, 'WEAK': 1.4}[t.state]
    if is_climax(c, ind):      t.climax_until = i + 3
    if i <= t.climax_until:    k = min(k, 0.8)
    if divergence(i, ind) or ind.ext > 3.5: k = min(k, 1.4)
    if after_1430(c):          k = min(k, 1.4)
    t.trail = max(t.trail, t.hh - k * ind.atr_trail)

    # 5. trail break, close-based (variant: exit at next open)
    if t.mfe_r >= 1.0 and c.close < t.trail:
        return exit_trade(t, c.close, 'TRAIL')

    # 6. time stops
    if i - t.entry_bar >= FAIL_BARS and t.mfe_r < 0.5 and c.close < t.entry:
        return exit_trade(t, c.close, 'FAIL_FAST')
    if i - t.last_high_bar >= STALL_BARS and ths < 60:
        return exit_trade(t, c.close, 'STALL')

    publish_status(t, ths, k)                   # section 4

def watch_for_reentry(w, c, prev, i, ind, fut):
    if w.count >= 2 or w.paused_until > i or entries_today() >= MAX_ENTRIES:
        return None
    if ind.regime != w.regime:                  # leg ended, wait for a fresh SMF flip
        return None
    ths = trend_health(c, i, ind, fut)
    if c.low <= ind.b_main + 0.5 * ind.atr:     # arm the pullback setup
        w.armed = True
    # A: pullback re-entry
    if w.armed and i - w.exit_bar >= 2 and c.close > prev.high and ths >= 50:
        return enter(c, stop=w.pullback_low - 0.25 * ind.atr)
    # B: breakout re-entry
    if c.close > w.last_hh and ind.rvol >= 1.0 and ths >= 60:
        return enter(c, stop=w.last_higher_low)
    return None
```

## 12. Phase 2: machine learning on top of the rules

Get the rule-based version working and tested first. Then:

- Log per-bar features inside each trade: the THS components, ATR percentile, time to expiry, delta, IV change, and futures order-book imbalance.
- Label each bar: did price make a new high before the trail broke, or how many more R followed over the next N bars.
- Train XGBoost, LightGBM or CatBoost on this. Use the predicted probability to adjust k (high probability: widen, low probability: tighten).
- The model may only move k. The ratchet rule stays, so the stop can never be lowered and the rules remain the safety layer.
- Combine with the entry meta-labeler discussed earlier, so the model filters weak entries and the exit engine handles the hold decision.
