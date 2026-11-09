"""
Crude Oil Macro Model Trainer
─────────────────────────────
Autonomous Machine Learning Pipeline for MCX CRUDEOIL Futures.
Trains an institutional XGBoost Classifier using at least 89,500 1-minute candles
and walk-forward validation (TimeSeriesSplit).

Architecture:
- Reference: /Users/admin/Documents/Nimesh/Projects/ai-trade/AI-trader/models/train_model.py
- Target: 3-Class Multiclass (0: Chop/Neutral, 1: Bullish Breakout, 2: Bearish Breakdown)
- Evaluates out-of-fold Accuracy, Precision, Recall, F1, and AUC-ROC.
- Outputs serialized model: models/saved/crude_macro_model.pkl
- Outputs manifest: models/saved/crude_model_manifest.json
"""

import os
import sys
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Any

import joblib
import numpy as np
import pandas as pd
from sklearn.metrics import accuracy_score, precision_score, recall_score, f1_score, roc_auc_score
from sklearn.model_selection import TimeSeriesSplit
import xgboost as xgb

# Set up project root
ROOT_DIR = Path(__file__).resolve().parent.parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from trading_engine.models.crude_features import compute_crude_features, CRUDE_FEATURE_COLUMNS

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s - %(message)s",
    datefmt="%H:%M:%S"
)
logger = logging.getLogger("crude_trainer")

MODEL_DIR = ROOT_DIR / "models" / "saved"
CRUDE_MODEL_PATH = MODEL_DIR / "crude_macro_model.pkl"
CRUDE_MANIFEST_PATH = MODEL_DIR / "crude_model_manifest.json"


def generate_crude_labels(
    df: pd.DataFrame,
    forward_periods: int = 15,
    threshold: float = 0.0020  # ~0.20% move (~17 points on Crude @ 8500)
) -> pd.DataFrame:
    """
    Generates 3-class target labels for Crude Oil:
      0: Chop / Range-bound (|return| <= threshold)
      1: Bullish Breakout (future_return > threshold)
      2: Bearish Breakdown (future_return < -threshold)
    """
    df = df.copy()
    future_return = df["close"].shift(-forward_periods) / (df["close"] + 1e-9) - 1.0

    df["target"] = 0
    df.loc[future_return > threshold, "target"] = 1
    df.loc[future_return < -threshold, "target"] = 2

    # Drop end rows where future_return is NaN
    df = df.dropna(subset=["target"])
    df["target"] = df["target"].astype(int)

    n_total = len(df)
    chop = int((df["target"] == 0).sum())
    bull = int((df["target"] == 1).sum())
    bear = int((df["target"] == 2).sum())

    logger.info(
        f"Crude Target Labels (forward={forward_periods}m, threshold={threshold*100:.2f}%): "
        f"Total={n_total:,} | Chop(0)={chop:,} ({chop/n_total*100:.1f}%), "
        f"Bull(1)={bull:,} ({bull/n_total*100:.1f}%), "
        f"Bear(2)={bear:,} ({bear/n_total*100:.1f}%)"
    )
    return df


class CrudeMacroModelTrainer:
    """
    Trains the XGBoost Macro Model on MCX CRUDEOIL 1-minute historical data.
    Enforces training on at least 89,500 candles with walk-forward validation.
    """

    def __init__(self, min_training_candles: int = 89500):
        self.min_training_candles = min_training_candles
        self.feature_cols = CRUDE_FEATURE_COLUMNS
        self.model: Optional[xgb.XGBClassifier] = None
        self.metrics: Dict[str, Any] = {}
        self.manifest: Dict[str, Any] = {}

    def prepare_data(
        self,
        raw_df: pd.DataFrame,
        lookback_candles: Optional[int] = None,
        forward_periods: int = 15,
        threshold: float = 0.0020
    ) -> pd.DataFrame:
        """
        Extracts features, trims to requested candle lookback (>= 89,500),
        and applies forward target labeling.
        """
        logger.info(f"Computing technical features for {len(raw_df):,} raw Crude candles...")
        featured_df = compute_crude_features(raw_df)

        # Slice to requested lookback or ensure at least min_training_candles
        if lookback_candles is not None:
            n_slice = max(lookback_candles, self.min_training_candles)
            if len(featured_df) > n_slice:
                featured_df = featured_df.iloc[-n_slice:].copy()
        elif len(featured_df) < self.min_training_candles:
            raise ValueError(
                f"Insufficient candles for training: {len(featured_df)} available, "
                f"minimum required is {self.min_training_candles}."
            )

        logger.info(f"Generating forward labels on {len(featured_df):,} candles...")
        df_labeled = generate_crude_labels(featured_df, forward_periods=forward_periods, threshold=threshold)

        # Drop any remaining NaNs
        clean_df = df_labeled.dropna(subset=self.feature_cols + ["target"]).reset_index(drop=True)
        logger.info(f"Final training-ready dataset: {len(clean_df):,} candles.")
        return clean_df

    def train_walk_forward(
        self,
        df: pd.DataFrame,
        n_splits: int = 5,
        **xgb_kwargs
    ) -> Dict[str, Any]:
        """
        Executes time-series walk-forward validation across n_splits,
        then trains the final model on the full lookback dataset.
        """
        X = df[self.feature_cols]
        y = df["target"]

        tscv = TimeSeriesSplit(n_splits=n_splits)
        fold_metrics = []

        model_params = {
            "n_estimators": 300,
            "max_depth": 6,
            "learning_rate": 0.05,
            "subsample": 0.8,
            "colsample_bytree": 0.8,
            "objective": "multi:softprob",
            "num_class": 3,
            "eval_metric": "mlogloss",
            "random_state": 42,
            "n_jobs": -1
        }
        model_params.update(xgb_kwargs)

        logger.info(f"Starting {n_splits}-fold Walk-Forward Validation...")
        for fold, (train_idx, test_idx) in enumerate(tscv.split(X)):
            X_train, y_train = X.iloc[train_idx], y.iloc[train_idx]
            X_test, y_test = X.iloc[test_idx], y.iloc[test_idx]

            fold_clf = xgb.XGBClassifier(**model_params)
            fold_clf.fit(X_train, y_train)

            y_proba = fold_clf.predict_proba(X_test)
            y_pred = np.argmax(y_proba, axis=1)

            acc = accuracy_score(y_test, y_pred)
            prec = precision_score(y_test, y_pred, average="weighted", zero_division=0)
            rec = recall_score(y_test, y_pred, average="weighted", zero_division=0)
            f1 = f1_score(y_test, y_pred, average="weighted", zero_division=0)
            try:
                auc = roc_auc_score(y_test, y_proba, multi_class="ovr")
            except Exception:
                auc = 0.0

            fold_metrics.append({
                "fold": fold + 1,
                "train_size": len(train_idx),
                "test_size": len(test_idx),
                "accuracy": round(acc, 4),
                "precision": round(prec, 4),
                "recall": round(rec, 4),
                "f1": round(f1, 4),
                "auc_roc": round(auc, 4),
            })
            logger.info(f"  Fold {fold+1}: Acc={acc:.4f}, F1={f1:.4f}, AUC={auc:.4f} (Test: {len(test_idx):,} samples)")

        # Aggregate metrics
        avg_acc = float(np.mean([m["accuracy"] for m in fold_metrics]))
        avg_f1 = float(np.mean([m["f1"] for m in fold_metrics]))
        avg_auc = float(np.mean([m["auc_roc"] for m in fold_metrics]))
        avg_prec = float(np.mean([m["precision"] for m in fold_metrics]))
        avg_rec = float(np.mean([m["recall"] for m in fold_metrics]))

        logger.info("=" * 60)
        logger.info(f"Walk-Forward Results: Accuracy={avg_acc*100:.2f}%, F1={avg_f1:.4f}, AUC-ROC={avg_auc:.4f}")
        logger.info("=" * 60)

        # Train final production model on the complete dataset
        logger.info(f"Training final production XGBoost model on all {len(X):,} candles...")
        final_model = xgb.XGBClassifier(**model_params)
        final_model.fit(X, y)
        self.model = final_model

        # Extract top feature importances
        importances = final_model.feature_importances_
        feature_importance_map = {
            col: round(float(imp), 4) for col, imp in sorted(
                zip(self.feature_cols, importances), key=lambda x: x[1], reverse=True
            )
        }

        # Build metadata & manifest
        start_date = str(df["datetime"].iloc[0])
        end_date = str(df["datetime"].iloc[-1])
        class_balance = {
            "chop_0": int((y == 0).sum()),
            "bull_1": int((y == 1).sum()),
            "bear_2": int((y == 2).sum()),
        }

        self.metrics = {
            "accuracy": round(avg_acc, 4),
            "precision": round(avg_prec, 4),
            "recall": round(avg_rec, 4),
            "f1": round(avg_f1, 4),
            "auc_roc": round(avg_auc, 4),
            "fold_details": fold_metrics,
        }

        self.manifest = {
            "model_name": "crude_macro_model",
            "symbol": "CRUDEOIL",
            "market": "MCX Commodity Futures",
            "sample_count": len(df),
            "min_required_candles": self.min_training_candles,
            "start_date": start_date,
            "end_date": end_date,
            "class_balance": class_balance,
            "validation_metrics": self.metrics,
            "top_features": dict(list(feature_importance_map.items())[:15]),
            "feature_list": self.feature_cols,
            "library_version": f"xgboost-{xgb.__version__}",
            "created_at": datetime.now().isoformat(),
        }

        return self.manifest

    def save(self, model_path: Path = CRUDE_MODEL_PATH, manifest_path: Path = CRUDE_MANIFEST_PATH):
        """Persists trained model and manifest to disk."""
        if not self.model:
            raise ValueError("No trained model to save. Run train_walk_forward() first.")

        model_path.parent.mkdir(parents=True, exist_ok=True)
        package = {
            "model": self.model,
            "feature_cols": self.feature_cols,
            "manifest": self.manifest,
            "metrics": self.metrics,
            "trained_at": datetime.now().isoformat(),
        }

        joblib.dump(package, model_path)
        logger.info(f"Saved model package to: {model_path} ({model_path.stat().st_size / 1024 / 1024:.2f} MB)")

        with open(manifest_path, "w") as f:
            json.dump(self.manifest, f, indent=2)
        logger.info(f"Saved manifest to: {manifest_path}")


def train_crude_model(
    csv_path: Optional[Path] = None,
    lookback_candles: int = 89500,
    save: bool = True
) -> Dict[str, Any]:
    """Convenience function to load CSV, extract features, and train the Crude XGBoost model."""
    if csv_path is None:
        csv_path = ROOT_DIR / "data" / "historical" / "CRUDEOIL_1min_3years.csv"

    if not csv_path.exists():
        raise FileNotFoundError(f"Crude historical CSV not found at: {csv_path}")

    logger.info(f"Loading Crude historical data from {csv_path}...")
    df = pd.read_csv(csv_path)
    logger.info(f"Loaded {len(df):,} candles.")

    trainer = CrudeMacroModelTrainer(min_training_candles=89500)
    clean_df = trainer.prepare_data(df, lookback_candles=lookback_candles)
    manifest = trainer.train_walk_forward(clean_df, n_splits=5)

    if save:
        trainer.save()

    return manifest


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Train Crude Macro XGBoost Model")
    parser.add_argument("--csv", type=str, default="", help="Path to CRUDEOIL historical CSV")
    parser.add_argument("--candles", type=int, default=89500, help="Candle lookback count (default: 89500)")
    args = parser.parse_args()

    csv_p = Path(args.csv) if args.csv else None
    train_crude_model(csv_path=csv_p, lookback_candles=args.candles)

