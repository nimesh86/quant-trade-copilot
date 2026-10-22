import os
import sys

# Prevent macOS Intel OpenMP threadpool suspension crash (__kmp_suspend_64 SIGSEGV)
os.environ["OMP_NUM_THREADS"] = "1"
os.environ["OPENBLAS_NUM_THREADS"] = "1"
os.environ["MKL_NUM_THREADS"] = "1"
os.environ["VECLIB_MAXIMUM_THREADS"] = "1"
os.environ["NUMEXPR_NUM_THREADS"] = "1"

import logging
import math
import numpy as np
import pandas as pd
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Any, Optional
from scipy.stats import norm

logger = logging.getLogger("trading_engine.ai.kronos")

# ── Black-Scholes Greek & Option Pricing Helpers ──────────────────────────────

def black_scholes_price(S: float, K: float, T: float, r: float, sigma: float, is_call: bool) -> float:
    """Calculate European option price under Black-Scholes-Merton model."""
    if T <= 1e-6:
        intr = (S - K) if is_call else (K - S)
        return max(0.05, float(intr))
    if sigma <= 1e-4:
        intr = (S - K) if is_call else (K - S)
        return max(0.05, float(intr))

    d1 = (math.log(max(1e-4, S / K)) + (r + 0.5 * sigma ** 2) * T) / (sigma * math.sqrt(T))
    d2 = d1 - sigma * math.sqrt(T)

    if is_call:
        price = S * norm.cdf(d1) - K * math.exp(-r * T) * norm.cdf(d2)
    else:
        price = K * math.exp(-r * T) * norm.cdf(-d2) - S * norm.cdf(-d1)
    return max(0.05, float(price))


def black_scholes_delta(S: float, K: float, T: float, r: float, sigma: float, is_call: bool) -> float:
    """Calculate option Delta (rate of change of option price w.r.t. spot price)."""
    if T <= 1e-6:
        if is_call:
            return 1.0 if S > K else (0.5 if S == K else 0.0)
        else:
            return -1.0 if S < K else (-0.5 if S == K else 0.0)
    d1 = (math.log(max(1e-4, S / K)) + (r + 0.5 * sigma ** 2) * T) / (sigma * math.sqrt(T))
    return float(norm.cdf(d1)) if is_call else float(norm.cdf(d1) - 1.0)


def implied_volatility(
    S: float,
    K: float,
    T: float,
    r: float,
    market_price: float,
    is_call: bool,
    default_sigma: float = 0.18,
) -> float:
    """
    Calibrate implied volatility from market price using bisection.
    Ensures projection starts seamlessly from exact current contract market price.
    """
    if T <= 1e-6 or market_price <= 0.05:
        return default_sigma

    low, high = 0.01, 3.0
    for _ in range(35):
        mid = (low + high) / 2.0
        p = black_scholes_price(S, K, T, r, mid, is_call)
        if abs(p - market_price) < 0.01:
            return mid
        if p < market_price:
            low = mid
        else:
            high = mid
    return mid


# Configure torch threads to prevent macOS threadpool deadlocks in async loops
try:
    import torch
    torch.set_num_threads(1)
    torch.set_num_interop_threads(1)
except Exception:
    pass

# Ensure kronos_src is in path
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
KRONOS_SRC = os.path.join(CURRENT_DIR, "kronos_src")
if KRONOS_SRC not in sys.path:
    sys.path.insert(0, KRONOS_SRC)


def calculate_atr(df: pd.DataFrame, length: int = 14) -> float:
    """Calculate Average True Range on a dataframe containing high, low, close."""
    if len(df) < 2:
        return 1.0
    h = df["high"].values
    l = df["low"].values
    c = df["close"].values
    n = len(df)
    tr = np.zeros(n)
    tr[0] = h[0] - l[0]
    for i in range(1, n):
        tr[i] = max(h[i] - l[i], abs(h[i] - c[i - 1]), abs(l[i] - c[i - 1]))
    atr_series = pd.Series(tr).rolling(min(length, n), min_periods=1).mean()
    return max(0.5, float(atr_series.iloc[-1]))


def to_ist_naive(dt_val) -> pd.Series:
    """
    Safely converts timestamps/datetime objects to naive Asia/Kolkata (IST) timestamps.
    Eliminates OOD errors in Kronos temporal embeddings (hour, minute, weekday).
    """
    s = pd.Series(dt_val)
    if pd.api.types.is_numeric_dtype(s):
        first = s.iloc[0] if len(s) > 0 else 0
        unit = "ms" if first > 1e11 else "s"
        return pd.to_datetime(s, unit=unit, utc=True).dt.tz_convert("Asia/Kolkata").dt.tz_localize(None)

    dt_parsed = pd.to_datetime(s)
    if dt_parsed.dt.tz is not None:
        return dt_parsed.dt.tz_convert("Asia/Kolkata").dt.tz_localize(None)
    else:
        # If naive hour is below 6 (e.g. 03:45 UTC), convert to IST (+5:30)
        mean_hour = dt_parsed.dt.hour.mean()
        if mean_hour < 6.0:
            return dt_parsed.dt.tz_localize("UTC").dt.tz_convert("Asia/Kolkata").dt.tz_localize(None)
        return dt_parsed


def resample_candles(candles: List[Dict[str, Any]], timeframe_minutes: int = 3) -> List[Dict[str, Any]]:
    """
    Resample 1-minute candles into higher timeframe (e.g. 3-minute) to eliminate micro-tick jitter.
    """
    if not candles or timeframe_minutes <= 1 or len(candles) < 6:
        return candles

    df = pd.DataFrame(candles)
    if "time" in df.columns:
        df["dt"] = to_ist_naive(df["time"])
    elif "timestamp" in df.columns:
        df["dt"] = to_ist_naive(df["timestamp"])
    else:
        return candles

    df = df.set_index("dt").sort_index()
    resampled = df.resample(f"{timeframe_minutes}min", closed="left", label="left").agg({
        "open": "first",
        "high": "max",
        "low": "min",
        "close": "last",
        "volume": "sum" if "volume" in df.columns else "count",
    }).dropna()

    out = []
    for dt, row in resampled.iterrows():
        out.append({
            "time": int(dt.timestamp()),
            "open": float(row["open"]),
            "high": float(row["high"]),
            "low": float(row["low"]),
            "close": float(row["close"]),
            "volume": float(row.get("volume", 1.0)),
        })
    return out


class KronosEngine:
    _instance = None

    def __init__(
        self,
        model_name: str = "NeoQuasar/Kronos-mini",
        tokenizer_name: str = "NeoQuasar/Kronos-Tokenizer-2k",
        device: str = "auto",
        max_context: int = 128,
    ):
        self.model_name = model_name
        self.tokenizer_name = tokenizer_name
        self.max_context = max_context
        if device == "auto":
            try:
                import torch
                if torch.backends.mps.is_available():
                    self.device = "mps"
                elif torch.cuda.is_available():
                    self.device = "cuda"
                else:
                    self.device = "cpu"
            except Exception:
                self.device = "cpu"
        else:
            self.device = device
        self.model = None
        self.tokenizer = None
        self.predictor = None
        self.is_loaded = False
        self.is_finetuned = False

    @classmethod
    def get_instance(cls):
        if cls._instance is None:
            cls._instance = KronosEngine()
        return cls._instance

    def load_model(self):
        """Loads fine-tuned Kronos model or falls back to pretrained foundation model."""
        if self.is_loaded:
            return True

        try:
            from model import Kronos, KronosTokenizer, KronosPredictor
            from huggingface_hub import snapshot_download

            # Check for fine-tuned Indian NIFTY checkpoint and its matching tokenizer
            finetuned_path = os.path.join(KRONOS_SRC, "finetune_csv", "finetuned", "nifty_5min", "basemodel", "best_model")
            finetuned_tok_path = os.path.join(KRONOS_SRC, "finetune_csv", "finetuned", "nifty_5min", "tokenizer", "best_model")

            if os.path.isdir(finetuned_path) and os.path.isdir(finetuned_tok_path) and (
                os.path.exists(os.path.join(finetuned_path, "config.json")) or
                os.path.exists(os.path.join(finetuned_path, "model.safetensors")) or
                os.path.exists(os.path.join(finetuned_path, "pytorch_model.bin"))
            ):
                logger.info(f"Loading MATCHED fine-tuned tokenizer from: {finetuned_tok_path}...")
                self.tokenizer = KronosTokenizer.from_pretrained(finetuned_tok_path)
                logger.info(f"Loading FINE-TUNED Indian NIFTY Kronos model from: {finetuned_path}...")
                self.model = Kronos.from_pretrained(finetuned_path)
                self.is_finetuned = True
                active_model_desc = f"Fine-Tuned NIFTY ({finetuned_path})"
            else:
                logger.info(f"Loading Base Kronos foundation model: {self.model_name}...")
                try:
                    tok_path = snapshot_download(self.tokenizer_name, local_files_only=True)
                except Exception:
                    tok_path = snapshot_download(self.tokenizer_name)
                self.tokenizer = KronosTokenizer.from_pretrained(tok_path)

                try:
                    model_path = snapshot_download(self.model_name, local_files_only=True)
                except Exception:
                    model_path = snapshot_download(self.model_name)
                self.model = Kronos.from_pretrained(model_path)
                self.is_finetuned = False
                active_model_desc = f"Base Model ({self.model_name})"

            self.predictor = KronosPredictor(
                self.model,
                self.tokenizer,
                device=self.device,
                max_context=self.max_context,
            )
            self.is_loaded = True
            logger.info(f"Kronos Predictor ({active_model_desc}) successfully loaded on device: {self.predictor.device}.")
            return True
        except Exception as e:
            logger.error(f"Failed to load Kronos model: {e}", exc_info=True)
            return False

    def predict_trajectory(
        self,
        candles: List[Dict[str, Any]],
        pred_len: int = 10,
        temperature: float = 0.15,
        top_p: float = 0.95,
        sample_count: int = 2,
    ) -> Dict[str, Any]:
        """
        Takes historical candles (list of dicts with open, high, low, close, volume, time)
        and predicts the next `pred_len` candles using the foundation model.
        Timestamps are strictly normalized to IST (Asia/Kolkata) to eliminate temporal OOD errors.
        """
        if not self.is_loaded:
            if not self.load_model():
                return {"error": "Kronos model failed to load"}

        if not candles or len(candles) < 10:
            return {"error": "Need at least 10 historical candles for prediction"}

        # Prepare DataFrame
        df = pd.DataFrame(candles)

        # Ensure timestamp column normalized to Indian Standard Time (IST)
        if "time" in df.columns:
            df["timestamps"] = to_ist_naive(df["time"])
        elif "timestamp" in df.columns:
            df["timestamps"] = to_ist_naive(df["timestamp"])
        else:
            now = datetime.now()
            df["timestamps"] = [now - timedelta(minutes=len(df) - 1 - i) for i in range(len(df))]

        # Ensure required columns
        for col in ["open", "high", "low", "close"]:
            if col not in df.columns:
                return {"error": f"Missing price column: {col}"}
            df[col] = df[col].astype(float)

        if "volume" not in df.columns:
            df["volume"] = 1.0
        else:
            df["volume"] = df["volume"].fillna(1.0).astype(float)

        if "amount" not in df.columns:
            df["amount"] = df["volume"] * df[["open", "high", "low", "close"]].mean(axis=1)

        # Truncate context if longer than max_context
        lookback = min(len(df), self.max_context)
        df_context = df.iloc[-lookback:].reset_index(drop=True)

        x_df = df_context[["open", "high", "low", "close", "volume", "amount"]]
        x_timestamp = df_context["timestamps"]

        # Calculate future timestamps
        last_ts = x_timestamp.iloc[-1]
        step = pd.Timedelta(minutes=1)
        if len(x_timestamp) >= 2:
            diff = x_timestamp.iloc[-1] - x_timestamp.iloc[-2]
            if diff.total_seconds() > 0:
                step = diff

        y_timestamp = pd.Series(pd.date_range(start=last_ts + step, periods=pred_len, freq=step))
        x_timestamp = pd.Series(x_timestamp).reset_index(drop=True)

        try:
            pred_df = self.predictor.predict(
                df=x_df,
                x_timestamp=x_timestamp,
                y_timestamp=y_timestamp,
                pred_len=pred_len,
                T=temperature,
                top_p=top_p,
                sample_count=sample_count,
                verbose=False,
            )

            # Compute ATR on context candles
            atr = calculate_atr(df_context, length=14)
            current_close = float(df["close"].iloc[-1])
            final_pred_close = max(0.05, float(pred_df["close"].iloc[-1]))
            max_pred_high = max(0.05, float(pred_df["high"].max()))
            min_pred_low = max(0.05, float(pred_df["low"].min()))
            if min_pred_low > max_pred_high:
                max_pred_high = min_pred_low

            # Floor return to -100%
            if current_close > 0.05:
                return_pct = round(((final_pred_close - current_close) / current_close) * 100, 2)
                return_pct = max(-100.0, min(500.0, return_pct))
                volatility_pct = round(((max_pred_high - min_pred_low) / current_close) * 100, 2)
            else:
                return_pct = 0.0
                volatility_pct = 0.0

            # Dynamic ATR-based conviction threshold:
            # Scaled to 0.30 of ATR (e.g. ~8-10 points on NIFTY)
            min_move_pct = max(0.035, round((0.30 * atr / max(1.0, current_close)) * 100, 3))

            if return_pct >= min_move_pct:
                direction = "BULLISH"
                conviction = "HIGH"
                confidence_score = min(95, max(68, int(65 + (return_pct / min_move_pct) * 12)))
            elif return_pct <= -min_move_pct:
                direction = "BEARISH"
                conviction = "HIGH"
                confidence_score = min(95, max(68, int(65 + (abs(return_pct) / min_move_pct) * 12)))
            elif abs(return_pct) >= 0.5 * min_move_pct:
                direction = "BULLISH" if return_pct > 0 else "BEARISH"
                conviction = "MODERATE"
                confidence_score = 60
            else:
                direction = "NEUTRAL"
                conviction = "RANGEBOUND"
                confidence_score = 50

            # Format forecast points with 80% confidence corridor cone
            forecast_candles = []
            for step_idx, (ts, row) in enumerate(pred_df.iterrows(), 1):
                c_close = max(0.05, round(float(row["close"]), 2))
                c_open = max(0.05, round(float(row["open"]), 2))
                c_high = max(max(c_open, c_close), round(float(row["high"]), 2))
                c_low = max(0.05, min(min(c_open, c_close), round(float(row["low"]), 2)))

                # 80% confidence corridor (1.28 * ATR * sqrt(t))
                corridor_width = round(1.28 * atr * math.sqrt(step_idx / 3.0), 2)
                upper_bound = round(c_close + corridor_width, 2)
                lower_bound = max(0.05, round(c_close - corridor_width, 2))

                forecast_candles.append({
                    "time": int(ts.timestamp()),
                    "open": c_open,
                    "high": c_high,
                    "low": c_low,
                    "close": c_close,
                    "upper": upper_bound,
                    "lower": lower_bound,
                    "volume": max(0.0, round(float(row.get("volume", 0)), 1)),
                })

            return {
                "success": True,
                "is_finetuned": getattr(self, "is_finetuned", False),
                "model_type": "finetuned_nifty_5min" if getattr(self, "is_finetuned", False) else "base_foundation",
                "current_price": round(current_close, 2),
                "direction": direction,
                "conviction": conviction,
                "confidence_score": confidence_score,
                "expected_return_pct": return_pct,
                "expected_volatility_pct": volatility_pct,
                "atr": round(atr, 2),
                "max_high": round(max_pred_high, 2),
                "min_low": round(min_pred_low, 2),
                "forecast_candles": forecast_candles,
            }
        except Exception as e:
            logger.error(f"Kronos prediction error: {e}", exc_info=True)
            return {"error": str(e)}

    def predict_option_trajectory(
        self,
        option_candles: List[Dict[str, Any]],
        underlying_candles: List[Dict[str, Any]],
        strike: float,
        option_type: str,
        expiry: Optional[str] = None,
        pred_len: int = 10,
        temperature: float = 0.2,
        top_p: float = 0.9,
    ) -> Dict[str, Any]:
        """
        Option A: Dual-layer forecast.
        1. Forecasts the underlying spot/future path using Kronos Foundation Model with low temp (0.2).
        2. Translates spot path to option contract trajectory using Black-Scholes Greeks (Delta, Gamma, Theta decay).
        Calibrates implied volatility to current contract price for seamless zero-gap projection.
        """
        if not option_candles or len(option_candles) < 5:
            return {"error": "Need at least 5 option candles for projection"}
        if not underlying_candles or len(underlying_candles) < 10:
            return {"error": "Need at least 10 underlying candles for projection"}

        # Step 1: Resample underlying to 3m candles if sufficient context is available
        use_3m = len(underlying_candles) >= 30
        und_input = resample_candles(underlying_candles, 3) if use_3m else underlying_candles
        und_pred_len = max(4, pred_len // 3 + 1) if use_3m else pred_len

        # 1. Run Kronos on the underlying asset
        spot_res = self.predict_trajectory(
            und_input,
            pred_len=und_pred_len,
            temperature=temperature,
            top_p=top_p,
        )
        if not spot_res.get("success"):
            return spot_res

        try:
            current_spot = float(spot_res.get("current_price") or underlying_candles[-1]["close"])
            current_opt_close = max(0.05, float(option_candles[-1]["close"]))
            is_call = option_type.upper().startswith("CE") or option_type.upper().startswith("C")

            # 2. Compute time to expiry T in annualized trading years
            # Trading year = 250 days * 375 minutes = 93,750 minutes
            last_opt = option_candles[-1]
            last_ts = last_opt.get("timestamp") or last_opt.get("time")
            if isinstance(last_ts, (int, float)):
                cur_dt = datetime.fromtimestamp(last_ts, tz=timezone.utc)
            else:
                cur_dt = pd.to_datetime(last_ts).to_pydatetime()

            # Indian market close is 15:30 IST (10:00 UTC)
            rem_minutes_today = max(5.0, min(375.0, float(10 * 60 - (cur_dt.hour * 60 + cur_dt.minute))))
            rem_days = 0.0
            if expiry:
                try:
                    exp_dt = datetime.strptime(expiry[:10], "%Y-%m-%d").date()
                    cur_d = cur_dt.date()
                    days_diff = (exp_dt - cur_d).days
                    if days_diff > 0:
                        rem_days = float(days_diff)
                except Exception:
                    pass

            total_rem_minutes = rem_days * 375.0 + rem_minutes_today
            T_years = max(1e-5, total_rem_minutes / (250.0 * 375.0))
            r = 0.07

            # 3. Calibrate Implied Volatility (IV) to match exact current contract price
            sigma = implied_volatility(current_spot, strike, T_years, r, current_opt_close, is_call)
            current_delta = black_scholes_delta(current_spot, strike, T_years, r, sigma, is_call)

            # Convert last_ts safely to integer UNIX timestamp
            if hasattr(last_ts, "timestamp"):
                last_ts_int = int(last_ts.timestamp())
            elif isinstance(last_ts, (int, float)):
                last_ts_int = int(last_ts)
            else:
                try:
                    last_ts_int = int(pd.to_datetime(last_ts).timestamp())
                except Exception:
                    last_ts_int = int(time.time())

            # 4. Generate spot minute-by-minute trajectory (interpolating 3m bars if used)
            raw_spot_candles = spot_res.get("forecast_candles", [])
            spot_minute_candles = []

            if use_3m and len(raw_spot_candles) >= 2:
                # Interpolate 3m forecast anchors into 1m steps
                anchor_times = [last_ts_int] + [c["time"] for c in raw_spot_candles]
                anchor_closes = [current_spot] + [c["close"] for c in raw_spot_candles]
                anchor_uppers = [current_spot] + [c.get("upper", c["close"] + 10.0) for c in raw_spot_candles]
                anchor_lowers = [current_spot] + [c.get("lower", c["close"] - 10.0) for c in raw_spot_candles]

                for m in range(1, pred_len + 1):
                    t_m = last_ts_int + (m * 60)
                    # Linear interpolation between anchors
                    s_close_m = float(np.interp(t_m, anchor_times, anchor_closes))
                    s_upper_m = float(np.interp(t_m, anchor_times, anchor_uppers))
                    s_lower_m = float(np.interp(t_m, anchor_times, anchor_lowers))
                    prev_c = current_spot if m == 1 else spot_minute_candles[-1]["close"]

                    s_open_m = prev_c
                    s_high_m = max(s_open_m, s_close_m) + abs(s_upper_m - s_close_m) * 0.25
                    s_low_m = min(s_open_m, s_close_m) - abs(s_close_m - s_lower_m) * 0.25

                    spot_minute_candles.append({
                        "time": t_m,
                        "open": round(s_open_m, 2),
                        "high": round(s_high_m, 2),
                        "low": round(s_low_m, 2),
                        "close": round(s_close_m, 2),
                        "upper": round(s_upper_m, 2),
                        "lower": round(s_lower_m, 2),
                        "volume": raw_spot_candles[0].get("volume", 0) / 3.0,
                    })
            else:
                spot_minute_candles = raw_spot_candles[:pred_len]

            # 5. Project option path candle-by-candle from predicted spot candles
            forecast_candles = []
            max_pred_high = 0.05
            min_pred_low = 999999.0

            # In a short 10-minute intraday forecast, artificial Black-Scholes theta decay
            # on 0-DTE options corrupts delta momentum. We keep T constant at snapshot T,
            # allowing spot delta and gamma to drive option trajectory.
            T_eval = max(1e-4, T_years)

            for i, s_candle in enumerate(spot_minute_candles):
                s_open = s_candle["open"]
                s_high = s_candle["high"]
                s_low = s_candle["low"]
                s_close = s_candle["close"]
                s_upper = s_candle.get("upper", s_high)
                s_lower = s_candle.get("lower", s_low)

                opt_open = black_scholes_price(s_open, strike, T_eval, r, sigma, is_call)
                opt_close = black_scholes_price(s_close, strike, T_eval, r, sigma, is_call)

                if is_call:
                    opt_high = black_scholes_price(s_high, strike, T_eval, r, sigma, is_call)
                    opt_low = black_scholes_price(s_low, strike, T_eval, r, sigma, is_call)
                    opt_upper = black_scholes_price(s_upper, strike, T_eval, r, sigma, is_call)
                    opt_lower = black_scholes_price(s_lower, strike, T_eval, r, sigma, is_call)
                else:
                    opt_high = black_scholes_price(s_low, strike, T_eval, r, sigma, is_call)
                    opt_low = black_scholes_price(s_high, strike, T_eval, r, sigma, is_call)
                    opt_upper = black_scholes_price(s_lower, strike, T_eval, r, sigma, is_call)
                    opt_lower = black_scholes_price(s_upper, strike, T_eval, r, sigma, is_call)

                # Ensure standard OHLC structural validity and tick minimum
                opt_high = max(opt_high, opt_open, opt_close, 0.05)
                opt_low = max(0.05, min(opt_low, opt_open, opt_close))
                opt_open = max(0.05, opt_open)
                opt_close = max(0.05, opt_close)
                opt_upper = max(opt_upper, opt_high)
                opt_lower = max(0.05, min(opt_lower, opt_low))

                max_pred_high = max(max_pred_high, opt_high)
                min_pred_low = min(min_pred_low, opt_low)

                forecast_candles.append({
                    "time": s_candle["time"],
                    "open": round(opt_open, 2),
                    "high": round(opt_high, 2),
                    "low": round(opt_low, 2),
                    "close": round(opt_close, 2),
                    "upper": round(opt_upper, 2),
                    "lower": round(opt_lower, 2),
                    "volume": s_candle.get("volume", 0),
                    "spot_close": round(s_close, 2),
                })

            if not forecast_candles:
                return {"error": "Failed to generate option forecast candles"}

            final_opt_close = forecast_candles[-1]["close"]
            return_pct = round(((final_opt_close - current_opt_close) / current_opt_close) * 100, 2)
            return_pct = max(-100.0, min(500.0, return_pct))

            # Dynamic conviction threshold from option ATR
            opt_df = pd.DataFrame(option_candles)
            opt_atr = calculate_atr(opt_df, length=10)
            opt_min_move_pct = max(1.2, round((0.40 * opt_atr / max(1.0, current_opt_close)) * 100, 2))

            if return_pct >= opt_min_move_pct:
                direction = "BULLISH"
                conviction = "HIGH"
                confidence_score = min(95, max(68, int(65 + (return_pct / opt_min_move_pct) * 12)))
            elif return_pct <= -opt_min_move_pct:
                direction = "BEARISH"
                conviction = "HIGH"
                confidence_score = min(95, max(68, int(65 + (abs(return_pct) / opt_min_move_pct) * 12)))
            elif abs(return_pct) >= 0.5 * opt_min_move_pct:
                direction = "BULLISH" if return_pct > 0 else "BEARISH"
                conviction = "MODERATE"
                confidence_score = 60
            else:
                direction = "NEUTRAL"
                conviction = "RANGEBOUND"
                confidence_score = 50

            return {
                "success": True,
                "mode": "dual_layer_spot_projection",
                "is_finetuned": getattr(self, "is_finetuned", False),
                "model_type": "finetuned_nifty_5min" if getattr(self, "is_finetuned", False) else "base_foundation",
                "current_price": round(current_opt_close, 2),
                "direction": direction,
                "conviction": conviction,
                "confidence_score": confidence_score,
                "expected_return_pct": return_pct,
                "expected_volatility_pct": round(((max_pred_high - min_pred_low) / current_opt_close) * 100, 2) if current_opt_close > 0 else 0.0,
                "max_high": round(max_pred_high, 2),
                "min_low": round(min_pred_low, 2),
                "option_delta": round(current_delta, 2),
                "option_iv": round(sigma * 100, 1),
                "option_atr": round(opt_atr, 2),
                "underlying_price": round(current_spot, 2),
                "underlying_direction": spot_res.get("direction", "NEUTRAL"),
                "underlying_return_pct": spot_res.get("expected_return_pct", 0.0),
                "forecast_candles": forecast_candles,
            }
        except Exception as e:
            logger.error(f"Option trajectory projection error: {e}", exc_info=True)
            return {"error": str(e)}

    def evaluate_smf_confirmation(
        self,
        candles: List[Dict[str, Any]],
        signal_type: str = "BUY",
        pred_len: int = 8,
    ) -> Dict[str, Any]:
        """
        Gatekeeper for SMF Signals. Evaluates whether Kronos confirms the trade.
        """
        res = self.predict_trajectory(candles, pred_len=pred_len)
        if not res.get("success"):
            return {
                "confirmed": True,
                "confidence": 0.5,
                "reason": f"Fallback (inference skipped: {res.get('error', 'unknown')})",
                "prediction": None,
            }

        ret = res["expected_return_pct"]
        vol = res["expected_volatility_pct"]
        direction = res["direction"]
        conviction = res.get("conviction", "MODERATE")
        conf_score = res.get("confidence_score", 60)

        if signal_type == "BUY":
            if direction == "BULLISH" and conviction in ("HIGH", "MODERATE"):
                confirmed = True
                confidence = round(conf_score / 100.0, 2)
                reason = f"Confirmed Bullish Expansion (+{ret}% return, {vol}% vol)"
            elif direction == "NEUTRAL":
                confirmed = True
                confidence = 0.60
                reason = "Neutral consolidation (allowed with standard risk)"
            elif direction == "BEARISH" and conviction == "HIGH":
                confirmed = False
                confidence = 0.15
                reason = f"VETOED by AI: High-conviction downward move predicted ({ret:+.2f}%)"
            elif direction == "BEARISH" and ret <= -0.10:
                confirmed = False
                confidence = 0.25
                reason = f"VETOED by AI: Downward drift predicted ({ret:+.2f}%)"
            else:
                confirmed = True
                confidence = 0.55
                reason = "Marginal confirmation"
        else: # SELL
            if direction == "BEARISH" and conviction in ("HIGH", "MODERATE"):
                confirmed = True
                confidence = round(conf_score / 100.0, 2)
                reason = f"Confirmed Bearish Drop ({ret}% return)"
            elif direction == "NEUTRAL":
                confirmed = True
                confidence = 0.60
                reason = "Neutral consolidation (allowed with standard risk)"
            elif direction == "BULLISH" and conviction == "HIGH":
                confirmed = False
                confidence = 0.15
                reason = f"VETOED by AI: High-conviction upward breakout predicted ({ret:+.2f}%)"
            elif direction == "BULLISH" and ret >= 0.10:
                confirmed = False
                confidence = 0.25
                reason = f"VETOED by AI: Upward drift predicted ({ret:+.2f}%)"
            else:
                confirmed = True
                confidence = 0.55
                reason = "Marginal confirmation"

        return {
            "confirmed": confirmed,
            "confidence": confidence,
            "conviction": conviction,
            "reason": reason,
            "prediction": res,
        }
