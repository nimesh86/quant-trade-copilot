"""
Test Meta-Labeling (Lopez de Prado Two-Stage Architecture):
Stage 1: Base Signal Generator (4-5 trades/day, ~50-55% win rate)
Stage 2: Meta-Classifier (Trained on which signals actually hit target vs stopped out)
Inference: Only take trades where Meta-Classifier P(win) > meta_threshold
"""

import numpy as np
import pandas as pd
import lightgbm as lgb
from xgboost import XGBClassifier
from sqlalchemy import create_engine, text

from test_unrealistic_approaches import (
    cand_call, cand_put, call_win, put_win, 
    f, df, day_to_indices, test_start_idx, test_indices,
    LOOKBACK, feature_cols, MAX_TRADES_PER_DAY
)

n = len(df)
train_pool_start = max(0, test_start_idx - LOOKBACK)
train_idx_all = np.arange(train_pool_start, test_start_idx)

# Stage 1: Train Base Classifiers
print("Training Stage 1 Base Models...")
m_c_base = lgb.LGBMClassifier(n_estimators=250, learning_rate=0.03, num_leaves=31, random_state=42, verbose=-1, n_jobs=-1)
m_p_base = lgb.LGBMClassifier(n_estimators=250, learning_rate=0.03, num_leaves=31, random_state=42, verbose=-1, n_jobs=-1)

clean_train = train_idx_all[f.iloc[train_idx_all][feature_cols].notna().all(axis=1).values]
m_c_base.fit(f.iloc[clean_train][feature_cols].values, call_win[clean_train])
m_p_base.fit(f.iloc[clean_train][feature_cols].values, put_win[clean_train])

pred_c_all = np.zeros(n)
pred_p_all = np.zeros(n)
valid_all = f[feature_cols].notna().all(axis=1).values
pred_c_all[valid_all] = m_c_base.predict_proba(f.iloc[valid_all][feature_cols].values)[:, 1]
pred_p_all[valid_all] = m_p_base.predict_proba(f.iloc[valid_all][feature_cols].values)[:, 1]

# Extract all simulated trade events in the TRAINING lookback period
print("Generating Stage 2 Meta-Training Data from lookback...")
meta_samples = []
# Pre-index training days
train_days = {}
for day, grp in pd.Series(clean_train).groupby(df.index[clean_train].date):
    train_days[day] = grp.values

for day, indices in train_days.items():
    t_today = 0
    blocked = -1
    for idx in indices:
        if t_today >= MAX_TRADES_PER_DAY:
            break
        if idx <= blocked:
            continue
        pc = pred_c_all[idx]
        pp = pred_p_all[idx]
        take = False
        side = None
        win = False
        if cand_call[idx] and pc >= 0.52 and pc > pp:
            take = True
            side = 'CE'
            win = bool(call_win[idx] == 1)
        elif cand_put[idx] and pp >= 0.52 and pp > pc:
            take = True
            side = 'PE'
            win = bool(put_win[idx] == 1)
        if take:
            meta_samples.append({
                'idx': idx,
                'win': int(win),
                'base_prob': pc if side == 'CE' else pp,
                **{col: f.iloc[idx][col] for col in feature_cols}
            })
            t_today += 1
            blocked = idx + 10

meta_tr_df = pd.DataFrame(meta_samples)
print(f"Meta-training trades generated: {len(meta_tr_df):,} | Base win rate: {meta_tr_df['win'].mean()*100:.2f}%")

# Train Stage 2 Meta-Classifier
meta_features = ['base_prob'] + feature_cols
meta_model = XGBClassifier(n_estimators=200, learning_rate=0.03, max_depth=4, subsample=0.8, colsample_bytree=0.8, random_state=42, n_jobs=-1)
meta_model.fit(meta_tr_df[meta_features].values, meta_tr_df['win'].values)

# Now evaluate on Test Set with Meta-Filtering!
print("\nEvaluating on Test Set across Meta-Confidence Thresholds...")
meta_te_feats = f[feature_cols].copy()

for meta_thr in [0.50, 0.52, 0.55, 0.58, 0.60, 0.62, 0.65]:
    total_trades = 0
    total_wins = 0
    daily = []
    for day, indices in day_to_indices.items():
        t_today = 0
        blocked = -1
        for idx in indices:
            if t_today >= MAX_TRADES_PER_DAY:
                break
            if idx <= blocked:
                continue
            pc = pred_c_all[idx]
            pp = pred_p_all[idx]
            take = False
            win = False
            base_prob = 0.0

            if cand_call[idx] and pc >= 0.52 and pc > pp:
                take = True
                base_prob = pc
                win = bool(call_win[idx] == 1)
            elif cand_put[idx] and pp >= 0.52 and pp > pc:
                take = True
                base_prob = pp
                win = bool(put_win[idx] == 1)

            if take:
                # Meta-Evaluation
                feat_vec = np.array([base_prob] + [f.iloc[idx][c] for c in feature_cols]).reshape(1, -1)
                meta_p = meta_model.predict_proba(feat_vec)[0, 1]
                if meta_p >= meta_thr:
                    t_today += 1
                    total_trades += 1
                    if win:
                        total_wins += 1
                    blocked = idx + 10

        daily.append(t_today)
    wr = (total_wins / total_trades * 100.0) if total_trades > 0 else 0.0
    print(f"Meta-Threshold {meta_thr:.2f} | Trades: {total_trades:3d} ({np.mean(daily):.2f}/d) | Wins: {total_wins:3d} | WinRate: {wr:.2f}%")
