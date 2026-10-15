"""
Historical Database Feeder.
Reads 1-minute spot candles, futures volume/VWAP, and Option Chain Greeks from PostgreSQL / TimescaleDB.
"""
from typing import Generator, Dict, Any, Tuple
import numpy as np
import pandas as pd
from sqlalchemy import create_engine, text
try:
    from .base import BaseDataFeeder
    from ..config import EngineConfig
except ImportError:
    from feeders.base import BaseDataFeeder
    from config import EngineConfig

class HistoricalDBFeeder(BaseDataFeeder):
    def __init__(self, cfg: EngineConfig):
        self.cfg = cfg
        self.engine = create_engine(cfg.db_url)
        self.spot_df = None
        self.feature_df = None
        self.test_indices = None
        self.test_start_idx = None

    def initialize(self):
        print("HistoricalDBFeeder: Loading candles from TimescaleDB...", flush=True)
        q_spot = text("""
            SELECT timestamp, open, high, low, close, volume
            FROM minute_candles
            WHERE symbol = :s
            ORDER BY timestamp ASC
        """)
        spot = pd.read_sql(q_spot, self.engine, params={"s": self.cfg.symbol})
        spot["timestamp"] = pd.to_datetime(spot["timestamp"], utc=True).dt.tz_convert(self.cfg.tz)
        spot = spot.drop_duplicates("timestamp").sort_values("timestamp").set_index("timestamp").between_time("09:15", "15:29")

        q_fut = text("""
            SELECT timestamp, volume, vwap
            FROM minute_candles
            WHERE symbol = :s
            ORDER BY timestamp ASC
        """)
        fut = pd.read_sql(q_fut, self.engine, params={"s": self.cfg.futures_symbol})
        if len(fut) > 0:
            fut["timestamp"] = pd.to_datetime(fut["timestamp"], utc=True).dt.tz_convert(self.cfg.tz)
            fut = fut.drop_duplicates("timestamp").sort_values("timestamp").set_index("timestamp").between_time("09:15", "15:29")
            spot["fut_volume"] = fut["volume"].reindex(spot.index).fillna(0.0)
            spot["fut_vwap"] = fut["vwap"].reindex(spot.index).ffill()
        else:
            spot["fut_volume"] = spot["volume"]
            spot["fut_vwap"] = spot["close"]

        # Option Chain
        q_opt = text("""
            SELECT timestamp, option_type, relative_strike, iv, delta
            FROM option_chain
            WHERE underlying = :u AND relative_strike = 0 AND ltp > 0
        """)
        opt = pd.read_sql(q_opt, self.engine, params={"u": self.cfg.option_prefix})
        opt["timestamp"] = pd.to_datetime(opt["timestamp"], utc=True).dt.tz_convert(self.cfg.tz)
        ce = opt[opt["option_type"] == "CE"].drop_duplicates("timestamp").set_index("timestamp")
        pe = opt[opt["option_type"] == "PE"].drop_duplicates("timestamp").set_index("timestamp")

        iv_ce = ce["iv"].reindex(spot.index).ffill().fillna(15.0)
        iv_pe = pe["iv"].reindex(spot.index).ffill().fillna(15.0)
        delta_ce = ce["delta"].reindex(spot.index).ffill().fillna(0.50)
        delta_pe = pe["delta"].reindex(spot.index).ffill().fillna(-0.50)

        c, h, l, v = spot["close"], spot["high"], spot["low"], spot["fut_volume"]
        f = pd.DataFrame(index=spot.index)

        for w in [3, 5, 10, 15, 30]:
            f[f"ret_{w}"] = c.pct_change(w)
            f[f"vol_{w}"] = c.pct_change().rolling(w).std()

        ema9 = c.ewm(span=9).mean()
        ema21 = c.ewm(span=21).mean()
        ema50 = c.ewm(span=50).mean()
        f["ema_spread_9_21"] = (ema9 - ema21) / c
        f["ema_spread_21_50"] = (ema21 - ema50) / c
        f["ema9_slope"] = ema9.pct_change(3)

        f["htf_trend"] = (c.ewm(span=9*15).mean() - c.ewm(span=21*15).mean()) / c

        d = c.diff()
        up = d.clip(lower=0).rolling(14).mean()
        dn = (-d.clip(upper=0)).rolling(14).mean()
        f["rsi"] = 100.0 - (100.0 / (1.0 + up / (dn + 1e-9)))
        f["rsi_slope"] = f["rsi"].diff(3)

        tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
        f["atr"] = tr.rolling(14).mean()
        f["atr_pct"] = f["atr"] / c

        days = spot.index.date
        pv = (c * v).groupby(days).cumsum()
        cum_v = v.groupby(days).cumsum()
        vwap = np.where(cum_v > 0, pv / np.maximum(cum_v, 1e-9), c)
        f["vwap_dist"] = (c - vwap) / c

        g = spot.groupby(days)
        or_hi = g["high"].transform(lambda s: s.iloc[:30].max())
        or_lo = g["low"].transform(lambda s: s.iloc[:30].min())
        f["or_pos"] = np.where(or_hi > or_lo, (c - or_lo) / np.maximum(or_hi - or_lo, 1e-9), 0.5)

        f["time_min"] = spot.index.hour * 60 + spot.index.minute
        t_time = spot.index.time
        f["is_morning"] = ((t_time >= pd.to_datetime("10:05").time()) & (t_time <= pd.to_datetime("11:30").time())).astype(float)
        f["is_afternoon"] = ((t_time >= pd.to_datetime("13:15").time()) & (t_time <= pd.to_datetime("15:00").time())).astype(float)

        f["atm_iv"] = (iv_ce + iv_pe) / 2.0
        f["iv_skew"] = iv_pe - iv_ce
        f["delta_skew"] = delta_ce.abs() - delta_pe.abs()
        f["iv_skew_diff"] = f["iv_skew"].diff(5)

        self.spot_df = spot
        self.feature_df = f.replace([np.inf, -np.inf], np.nan)

        test_mask = (spot.index >= pd.to_datetime(self.cfg.start_date).tz_localize(self.cfg.tz)) & \
                    (spot.index <= pd.to_datetime(self.cfg.end_date).tz_localize(self.cfg.tz))
        self.test_indices = np.flatnonzero(test_mask)
        self.test_start_idx = self.test_indices[0]
        print(f"HistoricalDBFeeder ready: {len(spot):,} total bars, test period has {len(self.test_indices):,} bars.", flush=True)

    def get_training_data(self) -> Tuple[pd.DataFrame, pd.DataFrame, np.ndarray]:
        train_start = max(0, self.test_start_idx - self.cfg.lookback_candles)
        train_idx = np.arange(train_start, self.test_start_idx)
        return self.spot_df.iloc[train_idx], self.feature_df.iloc[train_idx], train_idx

    def get_training_data_up_to(self, end_idx: int) -> Tuple[pd.DataFrame, pd.DataFrame, np.ndarray]:
        train_start = max(0, end_idx - self.cfg.lookback_candles)
        train_idx = np.arange(train_start, end_idx)
        return self.spot_df.iloc[train_idx], self.feature_df.iloc[train_idx], train_idx

    def stream_bars(self) -> Generator[Dict[str, Any], None, None]:
        ts_arr = self.spot_df.index
        o_arr = self.spot_df["open"].values
        h_arr = self.spot_df["high"].values
        l_arr = self.spot_df["low"].values
        c_arr = self.spot_df["close"].values
        v_arr = self.spot_df["volume"].values
        vwap_arr = self.spot_df["fut_vwap"].values
        atr_arr = self.feature_df["atr"].values
        rsi_arr = self.feature_df["rsi"].values
        ema9_21_arr = self.feature_df["ema_spread_9_21"].values
        ret5_arr = self.feature_df["ret_5"].values
        or_pos_arr = self.feature_df["or_pos"].values
        htf_trend_arr = self.feature_df["htf_trend"].values

        for idx in self.test_indices:
            row = {
                "idx": idx,
                "timestamp": ts_arr[idx],
                "open": o_arr[idx],
                "high": h_arr[idx],
                "low": l_arr[idx],
                "close": c_arr[idx],
                "volume": v_arr[idx],
                "fut_vwap": vwap_arr[idx],
                "atr": atr_arr[idx],
                "rsi": rsi_arr[idx],
                "ema_spread_9_21": ema9_21_arr[idx],
                "ret_5": ret5_arr[idx],
                "or_pos": or_pos_arr[idx],
                "htf_trend": htf_trend_arr[idx],
                "features": {}
            }
            yield row
