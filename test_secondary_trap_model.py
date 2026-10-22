"""
Secondary Strategy Model: Trained Specifically on Primary Model Failure Modes.
Forensic Ground Truth:
- 154 out of 156 losing trades (98.7%) were DIRECT REVERSAL TRAPS where the opposite side WON!
- 79 out of 156 (50.6%) reached >= +0.5x to +1.0x ATR before dying.

The Secondary AI Architecture:
1. In the Training Period (89,505 bars), identify every trade where the Primary Model signaled a direction but suffered a TRAP (failed and opposite side hit target).
2. Train the Secondary Model (Agent B) specifically on these Trap Scenarios using:
   - IV Skew Delta (Institutional Option Flow Warning)
   - Candle Upper/Lower Wick Rejection
   - Order Flow / Momentum Velocity
3. During Test Period:
   - Primary Model generates Candidate Trade.
   - Secondary Model evaluates: Is this a Genuine Trend (Execute Primary) or a Reversal Trap?
   - If Reversal Trap -> FLIP DIRECTION to the winning opposite side!
4. Measure the new win rate and trade count!
"""

import sys
import numpy as np
import pandas as pd
import lightgbm as lgb
from xgboost import XGBClassifier
from sqlalchemy import create_engine, text

# Load cached autopsy trades and data
from forensic_failure_audit import (
    df, f, c_arr, h_arr, l_arr, atr_arr, day_ends,
    cand_call, cand_put, call_win, put_win,
    m_c, m_p, feature_cols,
    test_indices, test_start_idx, day_to_indices, LOOKBACK, MAX_TRADES_PER_DAY
)

n = len(df)
train_pool_start = max(0, test_start_idx - LOOKBACK)
train_idx_all = np.arange(train_pool_start, test_start_idx)
clean_tr = train_idx_all[f.iloc[train_idx_all][feature_cols].notna().all(axis=1).values]

print("1. Mining Historical Trap & Reversal Data from 89,505 training bars...", flush=True)

# Generate Primary Model predictions on training data to find its training errors
pred_c_tr = m_c.predict_proba(f.iloc[clean_tr][feature_cols].values)[:, 1]
pred_p_tr = m_p.predict_proba(f.iloc[clean_tr][feature_cols].values)[:, 1]

# Identify all historical trades where Primary Model failed and opposite side won
trap_samples = []
train_days = {}
for day, grp in pd.Series(clean_tr).groupby(df.index[clean_tr].date):
    train_days[day] = grp.values

for day, indices in train_days.items():
    t_today = 0
    blocked = -1
    for k, idx in enumerate(indices):
        if t_today >= MAX_TRADES_PER_DAY:
            break
        if idx <= blocked:
            continue
        pc = pred_c_tr[k]
        pp = pred_p_tr[k]
        take = False
        side = None
        win = False
        if cand_call[idx] and pc >= 0.55 and pc > pp:
            take = True
            side = "CE"
            win = bool(call_win[idx] == 1)
        elif cand_put[idx] and pp >= 0.55 and pp > pc:
            take = True
            side = "PE"
            win = bool(put_win[idx] == 1)

        if take:
            # Did this trade suffer a reversal trap?
            is_trap = int(not win and (put_win[idx] == 1 if side == "CE" else call_win[idx] == 1))
            trap_samples.append({
                "idx": idx,
                "side": side,
                "is_trap": is_trap,
                "primary_prob": pc if side == "CE" else pp,
                **{col: f.iloc[idx][col] for col in feature_cols}
            })
            t_today += 1
            blocked = idx + 10

trap_df = pd.DataFrame(trap_samples)
print(f"Historical trades analyzed: {len(trap_df)} | Traps identified: {trap_df['is_trap'].sum()} ({trap_df['is_trap'].mean()*100:.1f}%)", flush=True)

# 2. Train Secondary Model (Trap Inversion AI)
print("2. Training Secondary Model (Agent B - Trap Detector)...", flush=True)
sec_features = ["primary_prob"] + feature_cols

sec_model = lgb.LGBMClassifier(
    n_estimators=300, learning_rate=0.03, num_leaves=31,
    min_child_samples=25, subsample=0.8, colsample_bytree=0.8,
    random_state=42, verbose=-1, n_jobs=-1
)
sec_model.fit(trap_df[sec_features].values, trap_df["is_trap"].values)

# 3. Evaluate on Test Period with Dynamic Conversion & Inversion
print("\n3. Evaluating Dual-Model Engine on Test Period (2026-04-01 to 2026-09-30)...", flush=True)
clean_te = test_indices[f.iloc[test_indices][feature_cols].notna().all(axis=1).values]
pred_c_te = m_c.predict_proba(f.iloc[clean_te][feature_cols].values)[:, 1]
pred_p_te = m_p.predict_proba(f.iloc[clean_te][feature_cols].values)[:, 1]

idx_to_pos = {idx: i for i, idx in enumerate(clean_te)}

for trap_threshold in [0.45, 0.48, 0.50, 0.52, 0.55]:
    total_trades = 0
    total_wins = 0
    flipped_count = 0
    flipped_wins = 0
    daily = []

    for day, indices in day_to_indices.items():
        t_today = 0
        blocked = -1
        for idx in indices:
            if t_today >= MAX_TRADES_PER_DAY:
                break
            if idx <= blocked:
                continue
            pos = idx_to_pos.get(idx)
            if pos is None:
                continue
            pc = pred_c_te[pos]
            pp = pred_p_te[pos]
            take = False
            side = None

            if cand_call[idx] and pc >= 0.55 and pc > pp:
                take = True
                side = "CE"
                primary_prob = pc
            elif cand_put[idx] and pp >= 0.55 and pp > pc:
                take = True
                side = "PE"
                primary_prob = pp

            if take:
                # Consult Secondary Model
                feat_vec = np.array([primary_prob] + [f.iloc[idx][c] for c in feature_cols]).reshape(1, -1)
                p_trap = sec_model.predict_proba(feat_vec)[0, 1]

                final_side = side
                is_flipped = False

                if p_trap >= trap_threshold:
                    # Invert trade from losing trap into profitable reversal
                    final_side = "PE" if side == "CE" else "CE"
                    is_flipped = True
                    flipped_count += 1

                # Determine Win
                if final_side == "CE":
                    won = bool(call_win[idx] == 1)
                else:
                    won = bool(put_win[idx] == 1)

                if is_flipped and won:
                    flipped_wins += 1

                t_today += 1
                total_trades += 1
                if won:
                    total_wins += 1
                blocked = idx + 10

        daily.append(t_today)

    wr = (total_wins / total_trades * 100.0) if total_trades > 0 else 0.0
    flip_wr = (flipped_wins / flipped_count * 100.0) if flipped_count > 0 else 0.0
    print(f"Trap Thr={trap_threshold:.2f} | Trades={total_trades:3d} ({np.mean(daily):.2f}/d) | Wins={total_wins:3d} | WinRate={wr:.2f}% | Flipped={flipped_count} (Flip WinRate={flip_wr:.1f}%)", flush=True)

print("\nDUAL-MODEL INVERSION TEST COMPLETE!")
