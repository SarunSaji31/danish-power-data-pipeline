# Danish Power Data Pipeline

[![CI](https://github.com/SarunSaji31/danish-power-data-pipeline/actions/workflows/ci.yml/badge.svg)](https://github.com/SarunSaji31/danish-power-data-pipeline/actions/workflows/ci.yml)

A production-style data pipeline for the Danish electricity market: **31.8 million rows**
of spot prices, wind/solar forecasts, CO₂ intensity and household consumption, ingested
from [Energi Data Service](https://www.energidataservice.dk/) (Energinet's open data API),
orchestrated with **Dagster** and stored in **TimescaleDB**.

```
energidataservice.dk API ──► Dagster (partitioned assets) ──► TimescaleDB
                                                                 │
                                          compression (10.7×) + continuous aggregates
                                                                 │
                                              ┌──────────────────┴───────┐
                                              ▼                          ▼
                                     Telegram daily briefing      analytics / dashboards
```

## Headline numbers (measured)

| Metric | Result |
|---|---|
| Rows ingested (2021 → today, 4 datasets) | **31.8M** |
| Storage with TimescaleDB columnar compression | **6 GB → 563 MB (10.7×)** |
| "Daily consumption per municipality, 12 months" | **2,195 ms raw → 18.9 ms via continuous aggregate (116×)** |
| Full 5.5-year backfill | 85 idempotent runs, resumable, survived a mid-run network outage |

## Datasets

| Table | Source dataset | Grain | Rows |
|---|---|---|---|
| `spot_prices` | `DayAheadPrices` (15-min, since Oct 2025) + `Elspotprices` (hourly, before) | 15-min / hourly, DK1+DK2 | 138k |
| `production_forecasts` | `Forecasts_Hour` | hourly wind on/offshore + solar, DK1+DK2 | 97k |
| `co2_emissions` | `CO2Emis` | 5-min CO₂ g/kWh, DK1+DK2 | 1.16M |
| `private_consumption` | `PrivateConsumptionHeatingHour` | hourly × 98 municipalities × housing × heating | **30.4M** |

## Design decisions

- **Monthly partitioned assets** (`MonthlyPartitionsDefinition`, 2021 → now). The nightly
  schedule re-materializes the current month; combined with idempotent upserts
  (`INSERT … ON CONFLICT DO UPDATE`) a missed night self-heals on the next run.
- **`BackfillPolicy.single_run()`** on the small assets lets a whole year collapse into one
  run/one API call — 12× fewer requests against a rate-limited API. The 30M-row consumption
  asset deliberately stays monthly (a year would be a ~1 GB JSON response).
- **Dual-source prices**: the Nordic market switched from hourly to 15-minute settlement on
  2025-10-01; the prices asset splits its fetch window across `Elspotprices` /
  `DayAheadPrices` and merges.
- **Rate-limit + network resilience**: fetches retry on HTTP 429 and transient connection
  errors with generous backoff; the backfill script records completed partitions and skips
  them on rerun.
- **Raw values preserved** (`spot_price_dkk_mwh`) alongside derived consumer prices
  (`price_dkk_kwh` = raw/1000 × 1.25 VAT) — derived data can always be recomputed.
- **UTC everywhere in storage**; conversion to Europe/Copenhagen happens only at the edges
  (the Telegram briefing, analytics).
- **Compression policy** converts chunks older than 30 days to columnstore (new chunks stay
  row-based for fast upserts); **continuous aggregates** (`prices_daily`, `co2_daily`,
  `consumption_daily_municipality`) refresh incrementally every hour.

## Daily operation

Two Dagster schedules (Europe/Copenhagen):

- **21:45** — ingest: re-materialize the current month of all four assets
  (day-ahead prices publish ~13:00; wind forecasts publish in the evening — hence the late run)
- **22:15** — `telegram_briefing`: tomorrow's DK1 hourly prices + wind/solar, cheapest
  3-hour window, sent via a Telegram bot

## Running it

```bash
# TimescaleDB
docker run -d --name timescale_local -p 5433:5432 \
  -e POSTGRES_USER=... -e POSTGRES_PASSWORD=... -e POSTGRES_DB=energy_etl_db \
  -v timescale_local_data:/var/lib/postgresql/data timescale/timescaledb:latest-pg16

cd energy_etl
python -m venv ../.venv && source ../.venv/bin/activate
pip install -e ".[dev]"

# schema + aggregates
docker exec -i timescale_local psql -U ... -d energy_etl_db < db/schema.sql
docker exec -i timescale_local psql -U ... -d energy_etl_db < db/analytics.sql

# config: create energy_etl/.env with ENERGY_DB_* and TELEGRAM_* variables (never committed)

# UI + schedules
DAGSTER_HOME=$HOME/.dagster_home dagster dev   # http://localhost:3000

# 5.5-year backfill (resumable)
bash scripts/backfill.sh
```

## Tests & CI

```bash
python -m pytest energy_etl_tests/
```

GitHub Actions runs on every push: install → validate Dagster definitions load → pytest.
