import json
import os
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

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


def consumer_price(raw_dkk_mwh: float | None) -> float | None:
    """Consumer price DKK/kWh incl. 25% VAT from the raw market DKK/MWh price."""
    if raw_dkk_mwh is None:
        return None
    return raw_dkk_mwh / 1000 * VAT_FACTOR


def build_upsert_sql(table: str, columns: list[str], conflict_cols: list[str]) -> str:
    update_cols = [c for c in columns if c not in conflict_cols]
    return (
        f"INSERT INTO {table} ({', '.join(columns)}) VALUES %s "
        f"ON CONFLICT ({', '.join(conflict_cols)}) DO UPDATE SET "
        + ", ".join(f"{c} = EXCLUDED.{c}" for c in update_cols)
    )


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
        try:
            response = requests.get(f"{API_BASE}/{dataset}", params=params, timeout=300)
        except (requests.exceptions.ConnectionError, requests.exceptions.Timeout):
            # transient network drop (wifi blip, DNS failure) — wait and retry
            time.sleep(60)
            continue
        if response.status_code == 429:
            # message says "Try again in N seconds" but doesn't always parse; be generous
            time.sleep(70)
            continue
        response.raise_for_status()
        return response.json()["records"]
    raise RuntimeError(f"{dataset}: gave up after {attempt + 1} attempts (rate limit or network)")


def upsert(table: str, columns: list[str], rows: list[tuple], conflict_cols: list[str]) -> None:
    """Idempotent batch upsert: re-running with the same data changes nothing."""
    if not rows:
        return
    sql = build_upsert_sql(table, columns, conflict_cols)
    with get_connection() as conn:
        with conn.cursor() as cur:
            execute_values(cur, sql, rows, page_size=1000)


@dg.asset(partitions_def=MONTHLY, backfill_policy=dg.BackfillPolicy.single_run())
def spot_prices(context: dg.AssetExecutionContext) -> dg.MaterializeResult:
    """Day-ahead spot prices (DK1+DK2). 15-min from DayAheadPrices since Oct 2025,
    hourly from Elspotprices before that; a window can span both sources."""
    window = context.partition_time_window
    area_filter = {"filter": json.dumps({"PriceArea": PRICE_AREAS})}

    raw: list[tuple] = []  # (time_string, area, price_dkk_mwh)
    if window.start < FIFTEEN_MIN_SWITCH:
        hourly_end = min(window.end, FIFTEEN_MIN_SWITCH)
        for r in fetch_records("Elspotprices", window.start, hourly_end, area_filter):
            raw.append((r["HourUTC"], r["PriceArea"], r["SpotPriceDKK"]))
    if window.end > FIFTEEN_MIN_SWITCH:
        quarter_start = max(window.start, FIFTEEN_MIN_SWITCH)
        for r in fetch_records("DayAheadPrices", quarter_start, window.end, area_filter):
            raw.append((r["TimeUTC"], r["PriceArea"], r["DayAheadPriceDKK"]))
    context.log.info(f"Fetched {len(raw)} price records for {context.partition_key_range}")

    rows = [
        (parse_utc(time_string), area, price, consumer_price(price))
        for time_string, area, price in raw
    ]
    upsert(
        "spot_prices",
        ["ts", "price_area", "spot_price_dkk_mwh", "price_dkk_kwh"],
        rows,
        ["ts", "price_area"],
    )
    return dg.MaterializeResult(metadata={"rows_upserted": len(rows)})


@dg.asset(partitions_def=MONTHLY, backfill_policy=dg.BackfillPolicy.single_run())
def production_forecasts(context: dg.AssetExecutionContext) -> dg.MaterializeResult:
    """Day-ahead wind/solar forecasts (DK1+DK2), pivoted from rows to columns."""
    window = context.partition_time_window
    records = fetch_records(
        "Forecasts_Hour",
        window.start,
        window.end,
        {"filter": json.dumps({"PriceArea": PRICE_AREAS})},
    )
    context.log.info(f"Fetched {len(records)} forecast records for {context.partition_key_range}")

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


@dg.asset(partitions_def=MONTHLY, backfill_policy=dg.BackfillPolicy.single_run())
def co2_emissions(context: dg.AssetExecutionContext) -> dg.MaterializeResult:
    """CO2 intensity of consumed power (DK1+DK2), 5-minute granularity."""
    window = context.partition_time_window
    records = fetch_records("CO2Emis", window.start, window.end)
    context.log.info(f"Fetched {len(records)} CO2 records for {context.partition_key_range}")

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


# --- Telegram briefing -------------------------------------------------------

COPENHAGEN = ZoneInfo("Europe/Copenhagen")


def cheapest_window(hourly_avg: dict[int, float], size: int = 3) -> tuple[list[int] | None, float | None]:
    """Cheapest run of `size` consecutive hours; (None, None) if no such run exists."""
    hours_sorted = sorted(hourly_avg)
    best_window, best_cost = None, None
    for i in range(len(hours_sorted) - size + 1):
        window = hours_sorted[i : i + size]
        if window[-1] - window[0] == size - 1:  # consecutive
            cost = sum(hourly_avg[h] for h in window) / size
            if best_cost is None or cost < best_cost:
                best_window, best_cost = window, cost
    return best_window, best_cost


def send_telegram(text: str) -> None:
    response = requests.post(
        f"https://api.telegram.org/bot{os.environ['TELEGRAM_BOT_TOKEN']}/sendMessage",
        json={"chat_id": os.environ["TELEGRAM_CHAT_ID"], "text": text},
        timeout=30,
    )
    response.raise_for_status()


@dg.asset(deps=[spot_prices, production_forecasts])
def telegram_briefing(context: dg.AssetExecutionContext) -> dg.MaterializeResult:
    """Evening briefing: tomorrow's DK1 hourly prices + wind/solar, sent via Telegram."""
    tomorrow_start = datetime.combine(
        (datetime.now(COPENHAGEN) + timedelta(days=1)).date(),
        datetime.min.time(),
        tzinfo=COPENHAGEN,
    )
    tomorrow_end = tomorrow_start + timedelta(days=1)

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT ts, price_dkk_kwh FROM spot_prices "
                "WHERE price_area = 'DK1' AND ts >= %s AND ts < %s",
                (tomorrow_start, tomorrow_end),
            )
            price_rows = cur.fetchall()
            cur.execute(
                "SELECT ts, coalesce(wind_onshore_mw,0) + coalesce(wind_offshore_mw,0), "
                "coalesce(solar_mw,0) FROM production_forecasts "
                "WHERE price_area = 'DK1' AND ts >= %s AND ts < %s",
                (tomorrow_start, tomorrow_end),
            )
            forecast_rows = cur.fetchall()

    day_label = tomorrow_start.strftime("%a %d %b")
    if not price_rows:
        send_telegram(f"⚠️ DK Energy Brief: no day-ahead prices for {day_label} yet.")
        return dg.MaterializeResult(metadata={"sent": "no-data alert"})

    # average 15-min prices into local hours
    hourly_prices: dict[int, list] = defaultdict(list)
    for ts, price in price_rows:
        if price is not None:
            hourly_prices[ts.astimezone(COPENHAGEN).hour].append(price)
    hourly_avg = {h: sum(v) / len(v) for h, v in hourly_prices.items()}

    wind = {ts.astimezone(COPENHAGEN).hour: mw for ts, mw, _ in forecast_rows}
    solar = {ts.astimezone(COPENHAGEN).hour: mw for ts, _, mw in forecast_rows}

    day_avg = sum(hourly_avg.values()) / len(hourly_avg)
    cheapest_hour = min(hourly_avg, key=hourly_avg.get)
    priciest_hour = max(hourly_avg, key=hourly_avg.get)

    # cheapest consecutive 3-hour window (for EV charging / appliances)
    hours_sorted = sorted(hourly_avg)
    best_window, best_cost = cheapest_window(hourly_avg)

    lines = [
        f"⚡ DK1 Energy Brief — {day_label}",
        f"Avg {day_avg:.2f} kr/kWh · Low {hourly_avg[cheapest_hour]:.2f} @{cheapest_hour:02d} "
        f"· High {hourly_avg[priciest_hour]:.2f} @{priciest_hour:02d}",
    ]
    if best_window:
        lines.append(
            f"🔌 Cheapest 3h: {best_window[0]:02d}–{best_window[2] + 1:02d} ({best_cost:.2f} kr/kWh)"
        )
    if wind:
        lines.append(
            f"💨 Wind avg {sum(wind.values()) / len(wind):,.0f} MW · "
            f"☀️ Solar peak {max(solar.values()):,.0f} MW"
        )
    lines.append("")
    lines.append("Hour  kr/kWh  wind MW")
    for h in hours_sorted:
        lines.append(f"{h:02d}    {hourly_avg[h]:5.2f}   {wind.get(h, 0):6,.0f}")

    send_telegram("\n".join(lines))
    return dg.MaterializeResult(
        metadata={"sent": "briefing", "hours": len(hourly_avg), "avg_price": round(day_avg, 3)}
    )


# --- Jobs & schedules --------------------------------------------------------

ingest_job = dg.define_asset_job(
    "ingest_job",
    selection=dg.AssetSelection.assets(
        "spot_prices", "production_forecasts", "co2_emissions", "private_consumption"
    ),
)

briefing_job = dg.define_asset_job(
    "briefing_job", selection=dg.AssetSelection.assets("telegram_briefing")
)


@dg.schedule(
    job=ingest_job,
    cron_schedule="45 21 * * *",
    execution_timezone="Europe/Copenhagen",
    default_status=dg.DefaultScheduleStatus.RUNNING,
)
def daily_ingest_schedule(context: dg.ScheduleEvaluationContext):
    """Nightly at 21:45 CPH: re-materialize the current month partition of all ingest
    assets (idempotent upsert = self-healing). Late enough that tomorrow's prices
    (~13:00) AND wind forecast (evening) are both published."""
    return dg.RunRequest(partition_key=context.scheduled_execution_time.strftime("%Y-%m-01"))


daily_briefing_schedule = dg.ScheduleDefinition(
    job=briefing_job,
    cron_schedule="15 22 * * *",
    execution_timezone="Europe/Copenhagen",
    default_status=dg.DefaultScheduleStatus.RUNNING,
)
