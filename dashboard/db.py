"""Single place the dashboard talks to TimescaleDB.

- Connection settings come from ENERGY_DB_* env vars (same contract as the
  pipeline). For local dev they're loaded from energy_etl/.env; in a container
  they arrive as real env vars and load_dotenv is a no-op.
- Results are cached in-process with a TTL: data changes once per day (nightly
  ingest), so there is no reason to hit Postgres on every page load.
"""

import os
import time
from pathlib import Path

import pandas as pd
import psycopg2
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / "energy_etl" / ".env")

CACHE_TTL_SECONDS = 900
_cache: dict[tuple, tuple[float, pd.DataFrame]] = {}


def _connect():
    return psycopg2.connect(
        host=os.environ["ENERGY_DB_HOST"],
        port=os.environ["ENERGY_DB_PORT"],
        user=os.environ["ENERGY_DB_USER"],
        password=os.environ["ENERGY_DB_PASSWORD"],
        dbname=os.environ["ENERGY_DB_NAME"],
    )


def query_df(sql: str, params: tuple = ()) -> pd.DataFrame:
    """Run a SELECT and return a DataFrame, serving repeats from the TTL cache."""
    key = (sql, params)
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < CACHE_TTL_SECONDS:
        return hit[1]

    with _connect() as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        columns = [col.name for col in cur.description]
        df = pd.DataFrame(cur.fetchall(), columns=columns)

    _cache[key] = (time.time(), df)
    return df
