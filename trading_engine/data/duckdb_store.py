"""
Local Embedded Storage Engine for quant_lib indicators using DuckDB.
Provides columnar in-process caching of synchronized Spot + Option historical candles.
Enables sub-5ms queries and 24/7 off-hours offline backtesting.
"""
from pathlib import Path
from typing import Optional, List, Dict, Any, Union
import duckdb
import pandas as pd


class LocalDataStore:
    """
    Embedded DuckDB storage manager for high-speed local candle caching.
    Operates without server daemons or network socket overhead.
    Uses dedicated cursors per operation to prevent ResourceInUse exceptions.
    """
    def __init__(self, db_path: Optional[str] = None):
        if not db_path:
            db_dir = Path.home() / ".quant_studio" / "data"
            db_dir.mkdir(parents=True, exist_ok=True)
            db_path = str(db_dir / "market_cache.duckdb")
        self.db_path = db_path
        try:
            self.con = duckdb.connect(self.db_path)
            self._init_schema()
        except duckdb.IOException:
            # If another process (e.g. uvicorn server or worker) holds write lock, connect read_only
            try:
                self.con = duckdb.connect(self.db_path, read_only=True)
            except Exception:
                self.con = duckdb.connect(":memory:")
                self._init_schema()

    def _init_schema(self):
        """Initializes tables and composite primary key if not already present."""
        cur = self.con.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS synchronized_candles (
                timestamp TIMESTAMP,
                symbol VARCHAR,
                contract VARCHAR,
                timeframe VARCHAR,
                spot_open DOUBLE,
                spot_high DOUBLE,
                spot_low DOUBLE,
                spot_close DOUBLE,
                spot_volume BIGINT,
                opt_open DOUBLE,
                opt_high DOUBLE,
                opt_low DOUBLE,
                opt_close DOUBLE,
                opt_volume BIGINT,
                opt_oi BIGINT,
                PRIMARY KEY (symbol, contract, timeframe, timestamp)
            );
        """)
        cur.close()

    def insert_candles(self, df: pd.DataFrame) -> int:
        """
        Inserts synchronized DataFrame into DuckDB with conflict upsert resolution.
        Returns the number of rows inserted.
        """
        if df.empty:
            return 0
        df_clean = df.copy()
        if "timestamp" in df_clean.columns:
            df_clean["timestamp"] = pd.to_datetime(df_clean["timestamp"])

        cur = self.con.cursor()
        try:
            cur.register("df_temp", df_clean)
            cur.execute("""
                INSERT OR REPLACE INTO synchronized_candles 
                SELECT 
                    timestamp, symbol, contract, timeframe,
                    spot_open, spot_high, spot_low, spot_close, spot_volume,
                    opt_open, opt_high, opt_low, opt_close, opt_volume, opt_oi
                FROM df_temp;
            """)
            try:
                cur.unregister("df_temp")
            except Exception:
                pass
        finally:
            cur.close()
        return len(df_clean)

    def query_candles(
        self,
        symbol: str,
        contract: str,
        from_date: Union[str, pd.Timestamp],
        to_date: Union[str, pd.Timestamp],
        timeframe: str = "1m"
    ) -> pd.DataFrame:
        """
        High-speed columnar query returning synchronized Spot + Option DataFrame.
        """
        from_ts = pd.to_datetime(from_date)
        to_ts = pd.to_datetime(to_date)

        query = """
            SELECT 
                timestamp, symbol, contract, timeframe,
                spot_open, spot_high, spot_low, spot_close, spot_volume,
                opt_open, opt_high, opt_low, opt_close, opt_volume, opt_oi
            FROM synchronized_candles
            WHERE symbol = ? AND contract = ? AND timeframe = ?
              AND timestamp >= ? AND timestamp <= ?
            ORDER BY timestamp ASC;
        """
        cur = self.con.cursor()
        try:
            result_df = cur.execute(query, [symbol, contract, timeframe, from_ts, to_ts]).df()
        finally:
            cur.close()
        return result_df

    def has_date_range(
        self,
        symbol: str,
        contract: str,
        from_date: Union[str, pd.Timestamp],
        to_date: Union[str, pd.Timestamp],
        timeframe: str = "1m"
    ) -> bool:
        """Checks if a specified date range already exists in the local cache."""
        from_ts = pd.to_datetime(from_date)
        to_ts = pd.to_datetime(to_date)

        query = """
            SELECT COUNT(*) AS cnt FROM synchronized_candles
            WHERE symbol = ? AND contract = ? AND timeframe = ?
              AND timestamp >= ? AND timestamp <= ?;
        """
        cur = self.con.cursor()
        try:
            res = cur.execute(query, [symbol, contract, timeframe, from_ts, to_ts]).fetchone()
            return (res[0] > 0) if res else False
        finally:
            cur.close()

    def get_cached_symbols(self) -> List[str]:
        """Returns distinct symbols available in local storage."""
        cur = self.con.cursor()
        try:
            res = cur.execute("SELECT DISTINCT symbol FROM synchronized_candles;").fetchall()
            return [r[0] for r in res]
        finally:
            cur.close()

    def close(self):
        """Closes the DuckDB connection."""
        try:
            self.con.close()
        except Exception:
            pass
