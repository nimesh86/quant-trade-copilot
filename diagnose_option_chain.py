"""Shows which filter in load_options() removes every option_chain row."""
import pandas as pd
from sqlalchemy import create_engine, text

URL = "postgresql+psycopg2://postgres:postgres@localhost:5432/trading"
UNDERLYING = "NIFTY"   # same value as UNDERLYING in self_improve_engine.py

pd.set_option("display.width", 200)
pd.set_option("display.max_columns", 20)
eng = create_engine(URL)


def q(sql, **params):
    return pd.read_sql(text(sql), eng, params=params)


print("\n1) Underlying values present in option_chain:")
print(q("SELECT underlying, COUNT(*) AS n, MIN(timestamp) AS first_ts, MAX(timestamp) AS last_ts "
        "FROM option_chain GROUP BY underlying ORDER BY n DESC"))

ist = "(timestamp AT TIME ZONE 'Asia/Kolkata')"
steps = [
    ("underlying matches", "underlying = :u"),
    ("+ relative_strike not null", "relative_strike IS NOT NULL"),
    ("+ relative_strike within -3..3", "relative_strike BETWEEN -3 AND 3"),
    ("+ ltp > 0", "ltp > 0"),
    ("+ on a 5-minute boundary", f"MOD(CAST(EXTRACT(MINUTE FROM {ist}) AS integer), 5) = 0"),
    ("+ expiry >= trade date", f"expiry >= CAST({ist} AS date)"),
    ("+ expiry <= trade date + 7", f"expiry <= CAST({ist} AS date) + 7"),
]
print(f"\n2) Rows left after each filter (underlying = {UNDERLYING!r}):")
conds = []
for label, cond in steps:
    conds.append(cond)
    n = q("SELECT COUNT(*) AS n FROM option_chain WHERE " + " AND ".join(conds), u=UNDERLYING)["n"][0]
    print(f"   {label:34s} {n}")

print("\n3) relative_strike values (top 10):")
print(q("SELECT relative_strike, COUNT(*) AS n FROM option_chain WHERE underlying = :u "
        "GROUP BY relative_strike ORDER BY n DESC LIMIT 10", u=UNDERLYING))

print("\n4) Minute-of-hour values (IST), top 6:")
print(q(f"SELECT CAST(EXTRACT(MINUTE FROM {ist}) AS integer) AS minute, COUNT(*) AS n "
        "FROM option_chain WHERE underlying = :u GROUP BY 1 ORDER BY n DESC LIMIT 6", u=UNDERLYING))

print("\n5) Latest 5 rows:")
print(q("SELECT timestamp, symbol, expiry, option_type, relative_strike, ltp FROM option_chain "
        "WHERE underlying = :u ORDER BY timestamp DESC LIMIT 5", u=UNDERLYING))