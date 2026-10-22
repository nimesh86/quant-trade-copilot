# SMF Cloud Strategy: Comprehensive Technical Specification & Implementation Guide
**Smart Money Flow Cloud (SMF) & Institutional Limit Order Execution Engine**
*A Fully Self-Contained, Language-Agnostic Specification for Intraday Options, Spot, and Futures Algorithmic Trading*

---

## Table of Contents
1. [Executive Overview & Core Philosophy](#1-executive-overview--core-philosophy)
2. [Exact Indicator Parameters & Mathematical Formulations](#2-exact-indicator-parameters--mathematical-formulations)
   - [2.1 Fast & Slow Exponential Moving Averages (EMA)](#21-fast--slow-exponential-moving-averages-ema)
   - [2.2 Baseline (bMain) & Dynamic Cloud Bands](#22-baseline-bmain--dynamic-cloud-bands)
   - [2.3 Wilder's Smoothed Average True Range (ATR)](#23-wilders-smoothed-average-true-range-atr)
   - [2.4 Volatility Buffer Multiplier & Adaptive Timeframe Scaling](#24-volatility-buffer-multiplier--adaptive-timeframe-scaling)
   - [2.5 Volume SMA & Relative Volume (RVOL)](#25-volume-sma--relative-volume-rvol)
   - [2.6 Relative Strength Index (RSI)](#26-relative-strength-index-rsi)
   - [2.7 Volume Weighted Average Price (VWAP)](#27-volume-weighted-average-price-vwap)
   - [2.8 Extension Ratio (Ext)](#28-extension-ratio-ext)
3. [Regime Detection & Multi-Bar Confirmation Engine](#3-regime-detection--multi-bar-confirmation-engine)
   - [3.1 Volatility Buffer Thresholds](#31-volatility-buffer-thresholds)
   - [3.2 Consecutive Close Streak Counters](#32-consecutive-close-streak-counters)
   - [3.3 Regime Transition & Signal Trigger Rules](#33-regime-transition--signal-trigger-rules)
   - [3.4 Retest Signals (✦) & Cooldown Engine](#34-retest-signals---cooldown-engine)
4. [Institutional Limit Order & Target Price Predictor](#4-institutional-limit-order--target-price-predictor)
   - [4.1 Multi-Window Swing Support Analysis](#41-multi-window-swing-support-analysis)
   - [4.2 Order Flow & Wick Physics (Buyer vs. Seller Pressure)](#42-order-flow--wick-physics-buyer-vs-seller-pressure)
   - [4.3 Dynamic Limit Price Decision Tree](#43-dynamic-limit-price-decision-tree)
   - [4.4 Order Type Classification (MARKET vs. LIMIT)](#44-order-type-classification-market-vs-limit)
   - [4.5 Safety Clamps, Support Floors & Tick Rounding](#45-safety-clamps-support-floors--tick-rounding)
5. [Risk Management & Trade Lifecycle Engine](#5-risk-management--trade-lifecycle-engine)
   - [5.1 Strict 5-Trade Daily Discipline](#51-strict-5-trade-daily-discipline)
   - [5.2 Initial Stop Loss Formulation](#52-initial-stop-loss-formulation)
   - [5.3 Scalp Target 1 (T1) & Partial Profit Booking (50%)](#53-scalp-target-1-t1--partial-profit-booking-50)
   - [5.4 Break-Even Lock (BE-Lock) Trailing Mechanism](#54-break-even-lock-be-lock-trailing-mechanism)
   - [5.5 Runner Target 2 (T2) & Max Target Exhaustion](#55-runner-target-2-t2--max-target-exhaustion)
   - [5.6 Trend Continuation & Re-Buy Triggers (🚀 CONT)](#56-trend-continuation--re-buy-triggers--cont)
   - [5.7 Exit & Square-Off Conditions](#57-exit--square-off-conditions)
   - [5.8 Pending Limit Order Fill Simulation Physics](#58-pending-limit-order-fill-simulation-physics)
6. [Strike Selection & Ranking (Multi-Contract Scanner)](#6-strike-selection--ranking-multi-contract-scanner)
7. [Intraday Session Windows (Timing Discipline)](#7-intraday-session-windows-timing-discipline)
8. [Complete Production Algorithmic Pseudocode](#8-complete-production-algorithmic-pseudocode)
9. [Reference Implementations](#9-reference-implementations)
   - [9.1 Reference Python Implementation](#91-reference-python-implementation)
   - [9.2 Reference TypeScript / JavaScript Implementation](#92-reference-typescript--javascript-implementation)
10. [Edge Cases & Verification Checklist](#10-edge-cases--verification-checklist)

---

## 1. Executive Overview & Core Philosophy

The **SMF Cloud Strategy** (Smart Money Flow Cloud) is a quantitative momentum and volatility-breakout framework engineered specifically for **intraday option buyers** (e.g., Nifty, BankNifty, Sensex options, or equity index options) as well as spot/futures momentum traders.

### Why Standard Strategies Fail for Option Buyers:
1. **Theta Decay (Time Value Bleed)**: Option prices decay rapidly during sideways markets. Entering early during consolidation leads to guaranteed premium erosion.
2. **1-Bar Noise & Shakeouts**: On 1-minute and 3-minute charts, stop hunts frequently breach standard moving averages only to reverse immediately.
3. **Slippage on Market Orders**: In fast momentum breakouts, retail traders chase market prices at the exact candle top, suffering immediate drawdowns upon minor pullbacks.

### The SMF Cloud Solution:
1. **Volatility-Buffered Regimes**: A trend flip requires price not just to cross the baseline, but to breach an ATR-derived volatility band and **hold consecutive closes** (`confirmBars`).
2. **Institutional Limit Dip Pricing**: Instead of chasing market tops, the algorithm calculates an institutional dip entry level (`limitPrice`) based on multi-window swing support (9, 21, 50 bars) and candle wick pressure.
3. **Rocket vs. Pullback Classification**: If volume explodes ($RVOL \ge 1.35$) with strong candle expansion and negligible overhead wick rejection, an immediate **MARKET** order is signaled so massive runners are never missed; otherwise, a passive **LIMIT** dip order is placed.
4. **Asymmetric Option Buyer Execution**: Never sells naked options. Takes only Long calls or Long puts (or long contracts). Once in profit, locks Break-Even at +1.0R / +6.0 points, books partials at Target 1, trails the runner to Target 2, and allows up to 2 continuation re-entries (`🚀 CONT`).

---

## 2. Exact Indicator Parameters & Mathematical Formulations

### Standard Parameter Set
| Parameter | Default (1-Minute) | Default (3m / 5m / 15m) | Description |
| :--- | :--- | :--- | :--- |
| `fastLen` | `9` | `9` | Fast EMA period |
| `slowLen` | `21` | `21` | Slow EMA period |
| `atrLen` | `14` | `14` | Wilder's Smoothing ATR period |
| `confirmBars` | `2` | `1` | Consecutive closed bars required to confirm a regime flip |
| `atrMult` | `0.50` | `0.35` | Volatility buffer multiplier (fractions of ATR) |
| `rsiPeriod` | `14` | `14` | RSI period for continuation filters |
| `vsmaPeriod` | `20` | `20` | Volume moving average period |
| `dotCooldown`| `15` | `10` | Minimum bars between consecutive retest dots (✦) |
| `maxDailyTrades` | `5` | `5` | Hard stop daily trade count to eliminate overtrading |

---

### 2.1 Fast & Slow Exponential Moving Averages (EMA)
Given a series of closing prices $C = [C_0, C_1, \dots, C_{n-1}]$ and period $L$:

$$\alpha = \frac{2}{L + 1}$$

$$\text{EMA}_0 = C_0$$

$$\text{EMA}_t = \alpha \cdot C_t + (1 - \alpha) \cdot \text{EMA}_{t-1} \quad \text{for } t \ge 1$$

- $fEMA_t = \text{EMA}(C, \text{fastLen})$
- $sEMA_t = \text{EMA}(C, \text{slowLen})$

---

### 2.2 Baseline (bMain) & Dynamic Cloud Bands
The baseline represents the central gravity of institutional equilibrium:

$$bMain_t = \frac{fEMA_t + sEMA_t}{2}$$

The outer dynamic cloud bands encompass standard volatility expansion:

$$UpperBand_t = bMain_t + ATR_t$$

$$LowerBand_t = bMain_t - ATR_t$$

---

### 2.3 Wilder's Smoothed Average True Range (ATR)
True Range ($TR$) for candle $t$:

$$TR_0 = High_0 - Low_0$$

$$TR_t = \max\Big(High_t - Low_t, \; |High_t - Close_{t-1}|, \; |Low_t - Close_{t-1}|\Big) \quad \text{for } t \ge 1$$

Wilder's Smoothing ($L = \text{atrLen} = 14$):
- Initial seed (Simple average of first $L$ true ranges):
  $$ATR_{L-1} = \frac{1}{L} \sum_{i=0}^{L-1} TR_i$$
- For $t \ge L$:
  $$ATR_t = \frac{ATR_{t-1} \cdot (L - 1) + TR_t}{L}$$
- For warm-up candles $t < L - 1$, backfill with $ATR_{L-1}$ to prevent undefined states.

---

### 2.4 Volatility Buffer Multiplier & Adaptive Timeframe Scaling
To eliminate false breakouts and 1-bar fakeouts:

$$Buffer_t = ATR_t \times mult$$

Where:
- On 1-minute timeframe ($TF \le 1$): $mult = 0.50$ (wider buffer to absorb high-frequency noise).
- On higher timeframes ($TF > 1$, e.g., 3m, 5m): $mult = 0.35$ (candles are already aggregated).

---

### 2.5 Volume SMA & Relative Volume (RVOL)
$$VSMA_t = \frac{1}{\min(t+1, 20)} \sum_{k=\max(0, t-19)}^{t} Volume_k$$

$$RVOL_t = \frac{Volume_t}{\max(VSMA_t, 1.0)}$$

---

### 2.6 Relative Strength Index (RSI)
Calculated using classic Wilder's smoothing over period $L = 14$:
$$\Delta_t = Close_t - Close_{t-1}$$
$$Gain_t = \max(\Delta_t, 0), \quad Loss_t = \max(-\Delta_t, 0)$$

For $t = L$:
$$AvgGain_L = \frac{1}{L} \sum_{i=1}^{L} Gain_i, \quad AvgLoss_L = \frac{1}{L} \sum_{i=1}^{L} Loss_i$$

For $t > L$:
$$AvgGain_t = \frac{AvgGain_{t-1} \cdot (L - 1) + Gain_t}{L}$$
$$AvgLoss_t = \frac{AvgLoss_{t-1} \cdot (L - 1) + Loss_t}{L}$$

$$RS_t = \frac{AvgGain_t}{\max(AvgLoss_t, 10^{-9})}$$
$$RSI_t = 100 - \frac{100}{1 + RS_t}$$

For $t < L$, initialize $RSI_t = 50.0$ or $RSI_L$.

---

### 2.7 Volume Weighted Average Price (VWAP)
Intraday reset at session open (09:15 IST):
$$TypicalPrice_t = \frac{High_t + Low_t + Close_t}{3}$$
$$VWAP_t = \frac{\sum_{k=0}^{t} TypicalPrice_k \cdot Volume_k}{\sum_{k=0}^{t} Volume_k}$$

---

### 2.8 Extension Ratio (Ext)
The extension ratio quantifies how overextended the price is from the baseline in multiples of ATR:
$$Ext_t = \frac{|Close_t - bMain_t|}{\max(ATR_t, 1.0)}$$

Values above $2.5$ indicate exhaustion risk where chasing market orders is strictly prohibited.

---

## 3. Regime Detection & Multi-Bar Confirmation Engine

### 3.1 Volatility Buffer Thresholds
At each closed bar $t$:
- **Above Buffer Zone**:
  $$\text{isAbove}_t \iff Close_t > bMain_t + Buffer_t$$
- **Below Buffer Zone**:
  $$\text{isBelow}_t \iff Close_t < bMain_t - Buffer_t$$

### 3.2 Consecutive Close Streak Counters
Track consecutive candle closes outside the buffer:
```python
if isBelow:
    belowCount += 1
    aboveCount = 0
elif isAbove:
    aboveCount += 1
    belowCount = 0
else:
    # Price closed inside the neutral cloud buffer zone
    belowCount = 0
    aboveCount = 0
```

### 3.3 Regime Transition & Signal Trigger Rules
Let $Regime_t \in \{+1, -1\}$ (where $+1$ = Bullish Trend, $-1$ = Bearish Trend).
- Initial State ($t=0$):
  $$Regime_0 = \begin{cases} +1 & \text{if } Close_0 \ge bMain_0 \\ -1 & \text{if } Close_0 < bMain_0 \end{cases}$$

- **Confirmed Flip to Bearish (Downswitch / EXIT Signal)**:
  Fires if and only if:
  $$Regime_{t-1} == +1 \quad \text{AND} \quad belowCount \ge reqBars$$
  When confirmed:
  $$Regime_t = -1, \quad belowCount = 0 \quad (\text{streak resets})$$

- **Confirmed Flip to Bullish (Upswitch / BUY Signal)**:
  Fires if and only if:
  $$Regime_{t-1} == -1 \quad \text{AND} \quad aboveCount \ge reqBars$$
  When confirmed:
  $$Regime_t = +1, \quad aboveCount = 0 \quad (\text{streak resets})$$

- **Hold State**:
  Otherwise, $Regime_t = Regime_{t-1}$.

> [!IMPORTANT]
> A transition signal fires **ONLY on the exact bar where $Regime_t \neq Regime_{t-1}$**.
> - $isSwitchUp_t \iff Regime_t == 1 \land Regime_{t-1} == -1$
> - $isSwitchDown_t \iff Regime_t == -1 \land Regime_{t-1} == 1$

---

### 3.4 Retest Signals (✦) & Cooldown Engine
Retest dots highlight high-probability trend pullback continuation entries when price bounces off the baseline without breaching the opposite buffer:

1. **Bullish Retest Dot (✦ below bar)**:
   - Condition: $Regime_t == 1 \land Low_t \le bMain_t \land Close_t \ge bMain_t - Buffer_t$
   - Cooldown Constraint: $t - \text{lastBullDotBar} \ge \text{dotCooldown}$ (default 15 bars)

2. **Bearish Retest Dot (✦ above bar)**:
   - Condition: $Regime_t == -1 \land High_t \ge bMain_t \land Close_t \le bMain_t + Buffer_t$
   - Cooldown Constraint: $t - \text{lastBearDotBar} \ge \text{dotCooldown}$ (default 15 bars)

---

## 4. Institutional Limit Order & Target Price Predictor

When a Bullish Switch (`isSwitchUp`) occurs, the engine executes `calculateSMFLimitAndTarget()` to identify the institutional order block rather than entering blindly at market high.

### 4.1 Multi-Window Swing Support Analysis
At signal bar $i$:
- $Low_9 = \min_{k=\max(0, i-8)}^{i} Low_k$
- $Low_{21} = \min_{k=\max(0, i-20)}^{i} Low_k$
- $Low_{50} = \min_{k=\max(0, i-49)}^{i} Low_k$
- $RecentDipLow = \min_{k=\max(0, i-4)}^{i} Low_k$ (5-bar swing low)

---

### 4.2 Order Flow & Wick Physics (Buyer vs. Seller Pressure)
Calculate the candle anatomy dynamics:
$$CandleSpan_i = \max(High_i - Low_i, \; 0.05)$$
$$UpperWick_i = High_i - \max(Open_i, Close_i)$$
$$LowerWick_i = \min(Open_i, Close_i) - Low_i$$

$$\text{SellerPressure}_i = \frac{UpperWick_i}{CandleSpan_i} \quad \in [0.0, 1.0]$$
$$\text{BuyerPressure}_i = \frac{LowerWick_i}{CandleSpan_i} \quad \in [0.0, 1.0]$$

Check 3-bar momentum drop:
$$Prev3Close = \begin{cases} Close_{i-3} & \text{if } i \ge 3 \\ Open_i & \text{otherwise} \end{cases}$$
$$\text{isDroppingRecently} \iff (Close_i < Prev3Close) \lor (\text{SellerPressure}_i > 0.42)$$

---

### 4.3 Dynamic Limit Price Decision Tree
Determine the optimal limit price (`suggestedLimit`):

```mermaid
graph TD
    A[Evaluate Signal Candle] --> B{isDroppingRecently?}
    B -- No --> C{RVOL >= 1.15 AND BuyerPressure >= SellerPressure?}
    C -- Yes (Strong Thrust) --> D[Shallow Dip: Close - 0.20 * ATR]
    D --> G[Limit = max(ShallowDip, RecentDipLow, Close * 0.96)]
    C -- No (Balanced) --> E[Cluster Support: max(RecentDipLow, bMain, Close - 0.35 * ATR)]
    E --> H[Limit = max(ClusterSupport, Close - 0.30 * ATR)]
    B -- Yes (Sellers Active) --> F[Drop Support: max(Low9, bMain, Close - 0.55 * ATR)]
    F --> I[Limit = (Close + DropSupport) / 2]
    G --> J[Apply Safety Clamps & Tick Rounding]
    H --> J
    I --> J
```

1. **Case 1: Strong Thrust (Explosive Buyer Dominance)**
   $$\text{Condition: } \neg \text{isDroppingRecently} \land RVOL_i \ge 1.15 \land BuyerPressure_i \ge SellerPressure_i$$
   $$ShallowDip = Close_i - (0.20 \times ATR_i)$$
   $$LimitPrice = \max(ShallowDip, \; RecentDipLow, \; Close_i \times 0.96)$$

2. **Case 2: Dropping Recently / Overhead Resistance**
   $$\text{Condition: } \text{isDroppingRecently} == \text{True}$$
   $$DropSupport = \max(Low_9, \; bMain_i, \; Close_i - 0.55 \times ATR_i)$$
   $$LimitPrice = \frac{Close_i + DropSupport}{2.0}$$

3. **Case 3: Balanced Regime**
   $$\text{Condition: Default}$$
   $$ClusterSupport = \max(RecentDipLow, \; bMain_i, \; Close_i - 0.35 \times ATR_i)$$
   $$LimitPrice = \max(ClusterSupport, \; Close_i - 0.30 \times ATR_i)$$

---

### 4.4 Order Type Classification (MARKET vs. LIMIT)
The system prevents "missing the move" on runaway explosive rallies:
- **Rocket Momentum Condition**:
  $$RVOL_i \ge 1.35 \quad \text{AND} \quad (Close_i - Open_i) \ge 0.65 \times CandleSpan_i \quad \text{AND} \quad SellerPressure_i \le 0.25$$
- **Execution Rule**:
  - If **Rocket Momentum**: Send immediate **MARKET ORDER** at $Close_i$.
  - Otherwise: Submit passive **LIMIT ORDER** at $LimitPrice$ valid for up to 3 bars.

---

### 4.5 Safety Clamps, Support Floors & Tick Rounding
To guarantee valid order entry on Indian exchanges (NSE tick = 0.05):
1. **Ceiling Clamp**: $LimitPrice = \min(LimitPrice, \; Close_i)$ (Never bid higher than current close).
2. **Baseline Support Floor**:
   $$\text{If } bMain_i > 0 \land LimitPrice < bMain_i \implies LimitPrice = \max(bMain_i, \; Close_i - ATR_i)$$
3. **Tick Rounding (0.05 step)**:
   $$LimitPrice = \frac{\text{round}(LimitPrice \times 20)}{20}$$
   If $LimitPrice \le 0 \implies LimitPrice = Close_i$.
4. **Max Limit Ceiling**:
   $$MaxLimitCeiling = \frac{\text{round}\Big(\min(Close_i + 0.30 \times ATR_i, \; Close_i \times 1.02) \times 20\Big)}{20}$$

---

## 5. Risk Management & Trade Lifecycle Engine

### 5.1 Strict 5-Trade Daily Discipline
- A persistent counter `dailyTradesCount` tracks total trades executed per calendar day.
- Resets to `0` at 00:00 (or first bar of the day).
- When `dailyTradesCount >= 5`, all subsequent buy signals are discarded for that trading session. This protects capital against erratic whipsaw days.

---

### 5.2 Initial Stop Loss Formulation
For option buyers, stop loss must account for underlying premium volatility:

#### Production Formula (`contract.js`):
$$SL\_Risk = \max(3.0, \; \min(10.0, \; 1.2 \times ATR_i))$$
$$StopLoss = \frac{\text{round}\Big(\max(LimitPrice - SL\_Risk, \; bMain_i - 0.75 \times ATR_i) \times 20\Big)}{20}$$
$$Risk = \max(3.0, \; LimitPrice - StopLoss)$$

#### Backtest Conservative Formula (`backtest_smf_limit_order_6m.py`):
$$StopLoss = \frac{\text{round}\Big(\max(bMain_i - 0.35 \times ATR_i, \; Close_i - 1.5 \times ATR_i) \times 20\Big)}{20}$$
$$Risk = \max(1.5, \; Close_i - StopLoss)$$

*(Both formulas clamp risk strictly to prevent catastrophic drawdown on extreme IV crush).*

---

### 5.3 Scalp Target 1 (T1) & Partial Profit Booking (50%)
Option buyer hit rates decay significantly when holding for large moves. Therefore, 50% of the position is secured quickly at Target 1:

#### Production Formula:
$$ScalpPts = \max(6.0, \; \min(12.0, \; 1.0 \times Risk))$$
$$Target_1 = \frac{\text{round}((LimitPrice + ScalpPts) \times 20)}{20}$$

#### Backtest Multiplier Formula:
$$Target_1 = \frac{\text{round}((Close_i + 1.6 \times Risk) \times 20)}{20}$$

- **Execution**: When $High_t \ge Target_1$, book 50% profit.

---

### 5.4 Break-Even Lock (BE-Lock) Trailing Mechanism
- **Trigger**: Once price crosses $LimitPrice + 6.0$ points OR candle $High_t \ge Target_1$:
  $$StopLoss = \max(StopLoss, \; EntryPrice)$$
- **Effect**: Eliminates downside risk. The remaining 50% runner position is mathematically free of risk.

---

### 5.5 Runner Target 2 (T2) & Max Target Exhaustion
The runner position targets extended expansion:

#### Production Formulas:
$$Target_2 = \frac{\text{round}\Big((LimitPrice + \max(18.0, \; 2.5 \times Risk)) \times 20\Big)}{20}$$
$$MaxTarget = \frac{\text{round}\Big((LimitPrice + \max(28.0, \; 3.5 \times Risk)) \times 20\Big)}{20}$$

#### Backtest Multiplier Formulas:
$$Target_2 = \frac{\text{round}((Close_i + 3.0 \times Risk) \times 20)}{20}$$
$$MaxTarget = \frac{\text{round}((Close_i + 4.5 \times Risk) \times 20)}{20}$$

- When $High_t \ge Target_2$, liquidate the remaining runner or trail stop strictly to $bMain_t$.

---

### 5.6 Trend Continuation & Re-Buy Triggers (🚀 CONT)
If the market continues into a high-momentum runner after Target 1 is achieved, the algorithm triggers a **Continuation Buy**:
- **Conditions**:
  1. Position is active ($inLongPosition == True$).
  2. Current regime remains bullish ($Regime_t == 1$).
  3. $Target_1$ has been achieved ($target1Achieved == True$).
  4. At least 4 bars have elapsed since last entry ($t - lastBuyBar \ge 4$).
  5. Maximum 2 continuations per trade ($continuationCount < 2$).
  6. Volume is healthy: $Volume_t \ge 0.8 \times VSMA_t$.
  7. Momentum is healthy: $RSI_t \in [50, 82]$ (not overbought exhaustion).
  8. Price holds above cloud baseline: $Close_t \ge bMain_t$.
  9. Seller pressure is low: $SellerPressure_t < 0.40$.

- **Action on CONT Trigger**:
  1. Scale in / re-buy with updated `limitPrice`.
  2. Set target to $Target_2$ or $MaxTarget$.
  3. Ratchet the trailing stop loss up to $bMain_t$.

---

### 5.7 Exit & Square-Off Conditions
A long position is liquidated immediately upon any of the following events:
1. **Stop Loss / BE-Lock Hit**: Candle $Low_t \le StopLoss$.
2. **Target 1 / Target 2 Hit**: Candle $High_t \ge Target$.
3. **Regime Flip to Bearish**: Candle closes with confirmed bearish flip ($isSwitchDown$).
   - Never short sell options naked; square off all call options immediately.
4. **Intraday EOD Cutoff (15:15 IST)**: Mandatory square-off to avoid overnight gap risk.

---

### 5.8 Pending Limit Order Fill Simulation Physics
When a Limit order is placed:
1. **Validity Window**: Active for up to 3 bars ($bar \le signalBar + 3$).
2. **Fill Condition**:
   $$\text{If } Low_t \le LimitPrice \implies \text{FILLED}$$
   $$\text{Execution Fill Price} = \min(LimitPrice, \; Open_t)$$
3. **Slippage Savings**:
   $$Savings = Close_{signalBar} - FillPrice$$
4. **Invalidation**:
   If the regime flips to Bearish ($Regime_t == -1$) before fill, the pending limit order is canceled immediately.

---

## 6. Strike Selection & Ranking (Multi-Contract Scanner)

When scanning multiple option strikes (e.g., Nifty ATM-100 to ATM+100):
1. **Filter by Volume**: Must have $Volume_t > 0$ and tight bid-ask spread.
2. **Signal Filtering**: Only select strikes that have confirmed $isSwitchUp == True$.
3. **Volatility Ranking**: If multiple strikes produce simultaneous signals on the same candle, rank candidates by ATR descending:
   $$\text{Best Strike} = \arg\max_{s} (ATR_{s, t})$$
   Selecting the strike with the highest ATR captures maximum delta acceleration and gamma expansion.

---

## 7. Intraday Session Windows (Timing Discipline)

Option theta and institutional participation vary sharply by time of day. The engine classifies each candle into 4 operational regimes:

| Window (IST) | Time Range | Label | Strategy Protocol |
| :--- | :--- | :--- | :--- |
| **Morning Ignition** | 09:30 – 10:45 | `MORNING IGNITION` | **Aggressive Buy**: Prime directional breakouts, institutional order flow. |
| **Midday Chop** | 11:00 – 13:15 | `MIDDAY CHOP (AVOID)` | **Filter On**: High theta decay, range-bound false breakouts. Discard non-exceptional setups. |
| **Afternoon Runner** | 13:15 – 14:30 | `AFTERNOON RUNNER` | **High Momentum**: Secondary trend continuation, aggressive scaling. |
| **Session Boundary** | 14:30 – 15:15 | `SESSION BOUNDARY` | **Trail Tight**: No new entries; trail active positions to lock gains. |
| **EOD Square-Off** | 15:15 | `EOD EXIT` | Force square-off all active positions. |

---

## 8. Complete Production Algorithmic Pseudocode

```python
class SMFCloudStrategy:
    def __init__(self, fast_len=9, slow_len=21, atr_len=14, confirm_bars=2, atr_mult=0.5):
        self.fast_len = fast_len
        self.slow_len = slow_len
        self.atr_len = atr_len
        self.confirm_bars = confirm_bars
        self.atr_mult = atr_mult
        
        # State variables
        self.prev_regime = 1
        self.below_count = 0
        self.above_count = 0
        self.daily_trades = 0
        self.current_date = None
        self.in_position = False
        self.active_trade = None
        self.pending_limit_order = None # (limit_price, sl, t1, t2, expiry_bar, signal_close)
        self.last_buy_bar = -9999
        self.continuation_count = 0
        self.last_bull_dot = -9999
        self.last_bear_dot = -9999

    def on_bar_update(self, bar_idx, candles):
        c = candles[bar_idx]
        date_str = get_date(c.timestamp)
        time_str = get_time_hhmm(c.timestamp)
        
        # 1. Daily Session Reset
        if date_str != self.current_date:
            self.current_date = date_str
            self.daily_trades = 0
            self.in_position = False
            self.active_trade = None
            self.pending_limit_order = None
            self.continuation_count = 0
            self.last_buy_bar = -9999

        # 2. Compute Indicators
        f_ema = calculate_ema(candles, self.fast_len, bar_idx)
        s_ema = calculate_ema(candles, self.slow_len, bar_idx)
        b_main = (f_ema + s_ema) / 2.0
        atr = calculate_wilder_atr(candles, self.atr_len, bar_idx)
        buf = atr * self.atr_mult
        vsma = calculate_volume_sma(candles, 20, bar_idx)
        rsi = calculate_rsi(candles, 14, bar_idx)

        # 3. Check Pending Limit Order Fill
        if not self.in_position and self.pending_limit_order is not None:
            p_lim, p_sl, p_t1, p_t2, p_exp_bar, p_sig_close = self.pending_limit_order
            if c.low <= p_lim:
                # Order filled!
                fill_price = min(p_lim, c.open)
                self.in_position = True
                self.active_trade = ActiveTrade(
                    entry_price=fill_price,
                    stop_loss=p_sl,
                    target_1=p_t1,
                    target_2=p_t2,
                    t1_achieved=False
                )
                self.pending_limit_order = None
                self.last_buy_bar = bar_idx
            elif bar_idx >= p_exp_bar:
                # Expired after 3 bars
                self.pending_limit_order = None

        # 4. Volatility Buffer Streak Counter
        if c.close < b_main - buf:
            self.below_count += 1
            self.above_count = 0
        elif c.close > b_main + buf:
            self.above_count += 1
            self.below_count = 0
        else:
            self.below_count = 0
            self.above_count = 0

        # 5. Multi-Bar Confirmation Regime State Machine
        curr_regime = self.prev_regime
        if self.prev_regime == 1 and self.below_count >= self.confirm_bars:
            curr_regime = -1
            self.below_count = 0
        elif self.prev_regime == -1 and self.above_count >= self.confirm_bars:
            curr_regime = 1
            self.above_count = 0

        is_switch_up = (curr_regime == 1 and self.prev_regime == -1)
        is_switch_down = (curr_regime == -1 and self.prev_regime == 1)
        self.prev_regime = curr_regime

        # Cancel pending limit order if regime flips to Bearish
        if curr_regime == -1 and self.pending_limit_order is not None:
            self.pending_limit_order = None

        # 6. Active Trade Management
        if self.in_position:
            t = self.active_trade

            # Exit A: Stop Loss Hit
            if c.low <= t.stop_loss:
                self.close_position(t.stop_loss, "STOP_LOSS")
                return

            # Exit B: Target 1 Hit (Book 50% Scalp & Lock Break-Even)
            if not t.t1_achieved and c.high >= t.target_1:
                t.t1_achieved = True
                t.stop_loss = max(t.stop_loss, t.entry_price) # Lock BE!
                self.book_partial_profit(50, t.target_1)

            # Exit C: Target 2 Hit (Liquidate Runner)
            if t.t1_achieved and c.high >= t.target_2:
                self.close_position(t.target_2, "TARGET_2_RUNNER")
                return

            # Exit D: Regime Flip to Bearish (Downswitch)
            if is_switch_down:
                self.close_position(c.close, "REGIME_EXIT")
                return

            # Exit E: EOD 15:15 Square-Off
            if time_str >= "15:15":
                self.close_position(c.close, "EOD_EXIT")
                return

            # Trend Continuation Re-Buy (🚀 CONT)
            if t.t1_achieved and (bar_idx - self.last_buy_bar >= 4) and self.continuation_count < 2:
                flow = calculate_order_flow(c, candles, bar_idx, vsma)
                if (c.volume >= 0.8 * vsma and 50 <= rsi <= 82 and 
                    c.close >= b_main and flow.seller_pressure < 0.40):
                    self.continuation_count += 1
                    self.last_buy_bar = bar_idx
                    t.stop_loss = max(t.stop_loss, b_main) # Trail SL to cloud baseline
                    t.target_2 = round((c.close + 3.0 * t.risk) * 20.0) / 20.0

        # 7. New Buy Signal Generation
        if is_switch_up and self.daily_trades < 5:
            # Skip Midday Chop
            if "11:00" <= time_str <= "13:15":
                return

            metrics = calculate_smf_limit_and_target(candles, bar_idx, atr, b_main, vsma, rsi)
            self.daily_trades += 1

            if metrics.order_type == "MARKET":
                # Execute Immediate Market Buy
                self.in_position = True
                self.active_trade = ActiveTrade(
                    entry_price=c.close,
                    stop_loss=metrics.stop_loss,
                    target_1=metrics.target1,
                    target_2=metrics.target2,
                    t1_achieved=False
                )
                self.last_buy_bar = bar_idx
                self.continuation_count = 0
            else:
                # Place Passive Limit Dip Order valid for 3 bars
                self.pending_limit_order = (
                    metrics.limit_price,
                    metrics.stop_loss,
                    metrics.target1,
                    metrics.target2,
                    bar_idx + 3,
                    c.close
                )
```

---

## 9. Reference Implementations

### 9.1 Reference Python Implementation

```python
import numpy as np

def calculate_smf_indicators(candles):
    closes = np.array([c['close'] for c in candles], dtype=float)
    highs = np.array([c['high'] for c in candles], dtype=float)
    lows = np.array([c['low'] for c in candles], dtype=float)
    volumes = np.array([c.get('volume', 1) for c in candles], dtype=float)
    n = len(closes)

    # 1. EMA
    def ema(src, length):
        res = np.zeros(n)
        k = 2.0 / (length + 1.0)
        res[0] = src[0]
        for i in range(1, n):
            res[i] = src[i] * k + res[i - 1] * (1.0 - k)
        return res

    f_ema = ema(closes, 9)
    s_ema = ema(closes, 21)
    b_main = (f_ema + s_ema) / 2.0

    # 2. Wilder ATR
    tr = np.zeros(n)
    tr[0] = highs[0] - lows[0]
    for i in range(1, n):
        tr[i] = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
    atr = np.zeros(n)
    init = min(14, n)
    atr[init - 1] = np.mean(tr[:init])
    for i in range(init, n):
        atr[i] = (atr[i - 1] * 13.0 + tr[i]) / 14.0
    for i in range(init - 1):
        atr[i] = atr[init - 1]

    # 3. Volume SMA & RSI
    vsma = np.zeros(n)
    for i in range(n):
        s = max(0, i - 19)
        vsma[i] = np.mean(volumes[s:i + 1])

    rsi = np.full(n, 50.0)
    if n > 14:
        gains = np.maximum(closes[1:] - closes[:-1], 0.0)
        losses = np.maximum(closes[:-1] - closes[1:], 0.0)
        ag = np.mean(gains[:14])
        al = np.mean(losses[:14])
        rsi[14] = 100.0 if al == 0 else 100.0 - (100.0 / (1.0 + ag / al))
        for i in range(15, n):
            ag = (ag * 13.0 + gains[i - 1]) / 14.0
            al = (al * 13.0 + losses[i - 1]) / 14.0
            rsi[i] = 100.0 if al == 0 else 100.0 - (100.0 / (1.0 + ag / al))
        for i in range(14):
            rsi[i] = rsi[14]

    return {
        'b_main': b_main,
        'atr': atr,
        'vsma': vsma,
        'rsi': rsi
    }
```

---

### 9.2 Reference TypeScript / JavaScript Implementation

```typescript
export interface Candle {
    time: number;
    open: number;
    high: number;
    low: number;
    close: number;
    volume: number;
}

export interface SMFMetrics {
    limitPrice: number;
    maxLimitCeiling: number;
    stopLoss: number;
    risk: number;
    target1: number;
    target2: number;
    maxTarget: number;
    recommendedOrder: 'MARKET' | 'LIMIT';
}

export function calculateSMFLimitAndTarget(
    candles: Candle[],
    i: number,
    atr: number[],
    bMain: number[],
    vsma: number[],
    rsi: number[]
): SMFMetrics {
    const c = candles[i];
    const curClose = c.close;
    const curOpen = c.open;
    const curHigh = c.high;
    const curLow = c.low;
    const curAtr = atr[i] || 2.0;
    const curBm = bMain[i];

    const getLowest = (len: number) => {
        const start = Math.max(0, i - len + 1);
        let minL = Infinity;
        for (let k = start; k <= i; k++) {
            if (candles[k].low < minL) minL = candles[k].low;
        }
        return minL === Infinity ? curLow : minL;
    };

    const low9 = getLowest(9);
    const recentDipLow = getLowest(5);

    const vol = c.volume || 1;
    const avgVol = vsma[i] || 1;
    const rvol = vol / Math.max(avgVol, 1);

    const candleSpan = Math.max(curHigh - curLow, 0.05);
    const upperWick = curHigh - Math.max(curOpen, curClose);
    const lowerWick = Math.min(curOpen, curClose) - curLow;
    const sellerPressure = upperWick / candleSpan;
    const buyerPressure = lowerWick / candleSpan;

    const prev3Close = i >= 3 ? candles[i - 3].close : curOpen;
    const isDroppingRecently = curClose < prev3Close || sellerPressure > 0.42;

    let suggestedLimit: number;
    if (!isDroppingRecently && rvol >= 1.15 && buyerPressure >= sellerPressure) {
        const shallowDip = curClose - (0.20 * curAtr);
        suggestedLimit = Math.max(shallowDip, recentDipLow, curClose * 0.96);
    } else if (isDroppingRecently) {
        const dropSupport = Math.max(low9, curBm, curClose - (0.55 * curAtr));
        suggestedLimit = (curClose + dropSupport) / 2;
    } else {
        const clusterSupport = Math.max(recentDipLow, curBm, curClose - (0.35 * curAtr));
        suggestedLimit = Math.max(clusterSupport, curClose - (0.30 * curAtr));
    }

    // Safety rules & tick rounding (NSE 0.05)
    suggestedLimit = Math.min(suggestedLimit, curClose);
    if (curBm && suggestedLimit < curBm) {
        suggestedLimit = Math.max(curBm, curClose - curAtr);
    }
    suggestedLimit = Math.round(suggestedLimit * 20) / 20;
    if (suggestedLimit <= 0) suggestedLimit = curClose;

    const maxLimitCeiling = Math.round(Math.min(curClose + 0.30 * curAtr, curClose * 1.02) * 20) / 20;

    const slRisk = Math.max(3.0, Math.min(10.0, curAtr * 1.2));
    const stopLoss = Math.round(Math.max(suggestedLimit - slRisk, curBm - 0.75 * curAtr) * 20) / 20;
    const risk = Math.max(3.0, suggestedLimit - stopLoss);

    const scalpPts = Math.max(6.0, Math.min(12.0, risk * 1.0));
    const target1 = Math.round((suggestedLimit + scalpPts) * 20) / 20;
    const target2 = Math.round((suggestedLimit + Math.max(18.0, risk * 2.5)) * 20) / 20;
    const maxTarget = Math.round((suggestedLimit + Math.max(28.0, risk * 3.5)) * 20) / 20;

    const isRocketMomentum = rvol >= 1.35 && (curClose - curOpen) >= (0.65 * candleSpan) && sellerPressure <= 0.25;
    const recommendedOrder = isRocketMomentum ? 'MARKET' : 'LIMIT';

    return {
        limitPrice: suggestedLimit,
        maxLimitCeiling,
        stopLoss,
        risk,
        target1,
        target2,
        maxTarget,
        recommendedOrder
    };
}
```

---

## 10. Edge Cases & Verification Checklist

Before running SMF Cloud in production, ensure your implementation satisfies all 10 integrity points:

- [x] **Streak Reset**: `belowCount` and `aboveCount` MUST reset to `0` whenever price closes inside the neutral buffer, and also reset to `0` immediately upon confirming a regime flip.
- [x] **Signal Singularity**: Buy signals (`isSwitchUp`) and Exit signals (`isSwitchDown`) fire **ONLY on the exact bar of the flip**, never continuously while remaining in trend.
- [x] **Option Asymmetry**: Long positions only. Never naked short sell options on downswitches.
- [x] **NSE Tick Sizing**: Every calculated limit, stop loss, and target MUST pass through `Math.round(p * 20) / 20` (0.05 precision).
- [x] **Limit Floor Invariant**: A limit order must never bid below `bMain` without an ATR constraint floor ($Limit \ge \max(bMain, Close - ATR)$).
- [x] **Limit Order Expiry**: Limit orders unfilled after 3 bars MUST be cancelled automatically.
- [x] **Limit Invalidation**: If the regime flips to Bearish while a Limit order is pending, cancel the order immediately.
- [x] **Strict 5-Trade Cap**: Stop placing orders once 5 trades are logged for the calendar day.
- [x] **Break-Even Ratchet**: Once $High \ge Target_1$, stop loss is non-decreasing ($\text{SL} = \max(\text{SL}, \text{EntryPrice})$).
- [x] **Mandatory 15:15 EOD Exit**: Never carry intraday option buyer positions overnight due to gap risk and time decay.

---

*Document Author: Institutional Trading Engine & Quantitative Research Core*  
*Verified Against: `trading_engine/web/static/contract.js`, `trading_engine/scripts/backtest_smf_limit_order_6m.py`, `trading_engine/scripts/backtest_smf_exact_6m.py`*
