import json
import os
import time
from datetime import datetime, timezone

import psycopg2
import requests
from psycopg2.extras import execute_values

import dagster as dg

API_BASE = "https://api.energidataservice.dk/dataset"
PRICE_AREAS = ["DK1", "DK2"]
VAT_FACTOR = 1.25

# One partition per month since 2021; end_offset=1 includes the current
# (incomplete) month, so the daily schedule can re-materialize it and self-heal.
MONTHLY = dg.MonthlyPartitionsDefinition(start_date="2021-01-01", end_offset=1)

# Nordic day-ahead market switched from hourly to 15-min on this date;
# prices before it live in Elspotprices, after it in DayAheadPrices.
FIFTEEN_MIN_SWITCH = datetime(2025, 10, 1, tzinfo=timezone.utc)

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


def api_time(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M")


def fetch_records(
    dataset: str, start: datetime, end: datetime, extra_params: dict | None = None
) -> list[dict]:
    """Fetch [start, end) from energidataservice, retrying on rate limits."""
    params = {"start": api_time(start), "end": api_time(end), "limit": 0}
    if extra_params:
        params.update(extra_params)

    for attempt in range(6):
        response = requests.get(f"{API_BASE}/{dataset}", params=params, timeout=300)
        if response.status_code == 429:
            # message says "Try again in N seconds" but doesn't always parse; be generous
            wait_seconds = 70
            time.sleep(wait_seconds)
            continue
        response.raise_for_status()
        return response.json()["records"]
    raise RuntimeError(f"{dataset}: still rate-limited after {attempt + 1} attempts")


def upsert(table: str, columns: list[str], rows: list[tuple], conflict_cols: list[str]) -> None:
    """Idempotent batch upsert: re-running with the same data changes nothing."""
    if not rows:
        return
    update_cols = [c for c in columns if c not in conflict_cols]
    sql = (
        f"INSERT INTO {table} ({', '.join(columns)}) VALUES %s "
        f"ON CONFLICT ({', '.join(conflict_cols)}) DO UPDATE SET "
        + ", ".join(f"{c} = EXCLUDED.{c}" for c in update_cols)
    )
    with get_connection() as conn:
        with conn.cursor() as cur:
            execute_values(cur, sql, rows, page_size=1000)


@dg.asset(partitions_def=MONTHLY)
def spot_prices(context: dg.AssetExecutionContext) -> dg.MaterializeResult:
    """Day-ahead spot prices (DK1+DK2). 15-min from DayAheadPrices since Oct 2025,
    hourly from Elspotprices before that."""
    window = context.partition_time_window
    area_filter = {"filter": json.dumps({"PriceArea": PRICE_AREAS})}

    if window.start >= FIFTEEN_MIN_SWITCH:
        records = fetch_records("DayAheadPrices", window.start, window.end, area_filter)
        time_field, price_field = "TimeUTC", "DayAheadPriceDKK"
    else:
        records = fetch_records("Elspotprices", window.start, window.end, area_filter)
        time_field, price_field = "HourUTC", "SpotPriceDKK"
    context.log.info(f"Fetched {len(records)} price records for {context.partition_key}")

    rows = [
        (
            parse_utc(r[time_field]),
            r["PriceArea"],
            r[price_field],
            r[price_field] / 1000 * VAT_FACTOR if r[price_field] is not None else None,
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


@dg.asset(partitions_def=MONTHLY)
def production_forecasts(context: dg.AssetExecutionContext) -> dg.MaterializeResult:
    """Day-ahead wind/solar forecasts (DK1+DK2), pivoted from rows to columns."""
    window = context.partition_time_window
    records = fetch_records(
        "Forecasts_Hour",
        window.start,
        window.end,
        {"filter": json.dumps({"PriceArea": PRICE_AREAS})},
    )
    context.log.info(f"Fetched {len(records)} forecast records for {context.partition_key}")

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


@dg.asset(partitions_def=MONTHLY)
def co2_emissions(context: dg.AssetExecutionContext) -> dg.MaterializeResult:
    """CO2 intensity of consumed power (DK1+DK2), 5-minute granularity."""
    window = context.partition_time_window
    records = fetch_records("CO2Emis", window.start, window.end)
    context.log.info(f"Fetched {len(records)} CO2 records for {context.partition_key}")

    rows = [(parse_utc(r["Minutes5UTC"]), r["PriceArea"], r["CO2Emission"]) for r in records]
    upsert("co2_emissions", ["ts", "price_area", "co2_g_per_kwh"], rows, ["ts", "price_area"])
    return dg.MaterializeResult(metadata={"rows_upserted": len(rows)})


@dg.asset(partitions_def=MONTHLY)
def private_consumption(context: dg.AssetExecutionContext) -> dg.MaterializeResult:
    """Hourly consumption per municipality/housing/heating (~470k rows/month).
    Publishes ~1 week late, so the newest partition is always partially filled."""
    window = context.partition_time_window
    records = fetch_records("PrivateConsumptionHeatingHour", window.start, window.end)
    context.log.info(f"Fetched {len(records)} consumption records for {context.partition_key}")

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
