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

# Forecasts_Hour has one row per (hour, area, type); we pivot types into columns
FORECAST_TYPE_COLUMN = {
    "Onshore Wind": "wind_onshore_mw",
    "Offshore Wind": "wind_offshore_mw",
    "Solar": "solar_mw",
}


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


def fetch_records(dataset: str, window_days: int, extra_params: dict | None = None) -> list[dict]:
    """Fetch a rolling window of records from energidataservice."""
    window_start = datetime.now(timezone.utc) - timedelta(days=window_days)
    params = {
        "start": window_start.strftime("%Y-%m-%dT%H:%M"),
        "limit": 0,  # 0 = no limit; the start param bounds the window
    }
    if extra_params:
        params.update(extra_params)
    response = requests.get(f"{API_BASE}/{dataset}", params=params, timeout=120)
    response.raise_for_status()
    return response.json()["records"]


def upsert(table: str, columns: list[str], rows: list[tuple], conflict_cols: list[str]) -> None:
    """Idempotent batch upsert: re-running with the same data changes nothing."""
    update_cols = [c for c in columns if c not in conflict_cols]
    sql = (
        f"INSERT INTO {table} ({', '.join(columns)}) VALUES %s "
        f"ON CONFLICT ({', '.join(conflict_cols)}) DO UPDATE SET "
        + ", ".join(f"{c} = EXCLUDED.{c}" for c in update_cols)
    )
    with get_connection() as conn:
        with conn.cursor() as cur:
            execute_values(cur, sql, rows, page_size=1000)


@dg.asset
def spot_prices(context: dg.AssetExecutionContext) -> dg.MaterializeResult:
    """Day-ahead spot prices (DK1+DK2) from energidataservice, idempotent upsert."""
    records = fetch_records(
        "DayAheadPrices",
        ROLLING_WINDOW_DAYS,
        {"filter": json.dumps({"PriceArea": PRICE_AREAS}), "sort": "TimeUTC asc"},
    )
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
    upsert(
        "spot_prices",
        ["ts", "price_area", "spot_price_dkk_mwh", "price_dkk_kwh"],
        rows,
        ["ts", "price_area"],
    )
    return dg.MaterializeResult(metadata={"rows_upserted": len(rows)})


@dg.asset
def production_forecasts(context: dg.AssetExecutionContext) -> dg.MaterializeResult:
    """Day-ahead wind/solar forecasts (DK1+DK2), pivoted from rows to columns."""
    records = fetch_records(
        "Forecasts_Hour",
        ROLLING_WINDOW_DAYS,
        {"filter": json.dumps({"PriceArea": PRICE_AREAS})},
    )
    context.log.info(f"Fetched {len(records)} forecast records")

    merged: dict[tuple, dict] = {}
    for r in records:
        column = FORECAST_TYPE_COLUMN.get(r["ForecastType"])
        if column is None:
            continue  # other forecast types we don't store
        merged.setdefault((r["HourUTC"], r["PriceArea"]), {})[column] = r["ForecastDayAhead"]

    rows = [
        (
            parse_utc(hour_utc),
            area,
            values.get("wind_onshore_mw"),
            values.get("wind_offshore_mw"),
            values.get("solar_mw"),
        )
        for (hour_utc, area), values in merged.items()
    ]
    upsert(
        "production_forecasts",
        ["ts", "price_area", "wind_onshore_mw", "wind_offshore_mw", "solar_mw"],
        rows,
        ["ts", "price_area"],
    )
    return dg.MaterializeResult(metadata={"rows_upserted": len(rows)})


@dg.asset
def co2_emissions(context: dg.AssetExecutionContext) -> dg.MaterializeResult:
    """CO2 intensity of consumed power (DK1+DK2), 5-minute granularity."""
    records = fetch_records("CO2Emis", ROLLING_WINDOW_DAYS)
    context.log.info(f"Fetched {len(records)} CO2 records")

    rows = [(parse_utc(r["Minutes5UTC"]), r["PriceArea"], r["CO2Emission"]) for r in records]
    upsert("co2_emissions", ["ts", "price_area", "co2_g_per_kwh"], rows, ["ts", "price_area"])
    return dg.MaterializeResult(metadata={"rows_upserted": len(rows)})


@dg.asset
def private_consumption(context: dg.AssetExecutionContext) -> dg.MaterializeResult:
    """Hourly consumption per municipality/housing/heating. Publishes ~1 week late,
    so the rolling window is wider than the other assets."""
    records = fetch_records("PrivateConsumptionHeatingHour", window_days=14)
    context.log.info(f"Fetched {len(records)} consumption records")

    rows = [
        (
            parse_utc(r["TimeUTC"]),
            r["MunicipalityCode"],
            r["HousingCategory"],
            r["HeatingCategory"],
            r["ConsumptionkWh"],
        )
        for r in records
    ]
    upsert(
        "private_consumption",
        ["ts", "municipality_code", "housing_category", "heating_category", "consumption_kwh"],
        rows,
        ["ts", "municipality_code", "housing_category", "heating_category"],
    )
    return dg.MaterializeResult(metadata={"rows_upserted": len(rows)})
