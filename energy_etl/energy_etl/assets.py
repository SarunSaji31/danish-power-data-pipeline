import json
import os
from datetime import datetime, timedelta, timezone

import psycopg2
import requests
from psycopg2.extras import execute_values

import dagster as dg

API_BASE = "https://api.energidataservice.dk/dataset"
PRICE_AREAS = ["DK1", "DK2"]
VAT_FACTOR = 1.25
ROLLING_WINDOW_DAYS = 8  # each run re-upserts ~8 days, so a missed run self-heals


def get_connection():
    return psycopg2.connect(
        host=os.environ["ENERGY_DB_HOST"],
        port=os.environ["ENERGY_DB_PORT"],
        user=os.environ["ENERGY_DB_USER"],
        password=os.environ["ENERGY_DB_PASSWORD"],
        dbname=os.environ["ENERGY_DB_NAME"],
    )


def parse_utc(ts_string: str) -> datetime:
    # API returns naive strings like "2026-07-12T21:45:00" that are UTC
    return datetime.fromisoformat(ts_string).replace(tzinfo=timezone.utc)


@dg.asset
def spot_prices(context: dg.AssetExecutionContext) -> dg.MaterializeResult:
    """Day-ahead spot prices (DK1+DK2) from energidataservice, idempotent upsert."""
    window_start = datetime.now(timezone.utc) - timedelta(days=ROLLING_WINDOW_DAYS)

    response = requests.get(
        f"{API_BASE}/DayAheadPrices",
        params={
            "start": window_start.strftime("%Y-%m-%dT%H:%M"),
            "filter": json.dumps({"PriceArea": PRICE_AREAS}),
            "sort": "TimeUTC asc",
            "limit": 0,  # 0 = no limit; the start param bounds the window
        },
        timeout=60,
    )
    response.raise_for_status()
    records = response.json()["records"]
    context.log.info(f"Fetched {len(records)} price records")

    rows = [
        (
            parse_utc(r["TimeUTC"]),
            r["PriceArea"],
            r["DayAheadPriceDKK"],
            r["DayAheadPriceDKK"] / 1000 * VAT_FACTOR
            if r["DayAheadPriceDKK"] is not None
            else None,
        )
        for r in records
    ]

    with get_connection() as conn:
        with conn.cursor() as cur:
            execute_values(
                cur,
                """
                INSERT INTO spot_prices (ts, price_area, spot_price_dkk_mwh, price_dkk_kwh)
                VALUES %s
                ON CONFLICT (ts, price_area) DO UPDATE SET
                    spot_price_dkk_mwh = EXCLUDED.spot_price_dkk_mwh,
                    price_dkk_kwh = EXCLUDED.price_dkk_kwh
                """,
                rows,
                page_size=1000,
            )

    return dg.MaterializeResult(
        metadata={
            "rows_upserted": len(rows),
            "window_start": window_start.isoformat(),
            "price_areas": ", ".join(PRICE_AREAS),
        }
    )
