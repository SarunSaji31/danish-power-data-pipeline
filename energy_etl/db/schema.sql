-- Energy ETL schema — idempotent, safe to re-run.

-- 1. Day-ahead spot prices, all zones (15-min; hourly before Oct 2025)
CREATE TABLE IF NOT EXISTS spot_prices (
    ts                  timestamptz NOT NULL,
    price_area          text        NOT NULL,
    spot_price_dkk_mwh  double precision,   -- raw SpotPriceDKK from the API (per MWh)
    price_dkk_kwh       double precision,   -- consumer price incl 25% VAT = raw/1000*1.25
    created_at          timestamptz DEFAULT now(),
    PRIMARY KEY (ts, price_area)
);
SELECT create_hypertable('spot_prices', 'ts',
    chunk_time_interval => interval '1 month', if_not_exists => TRUE);

-- 2. Day-ahead production forecasts, hourly
CREATE TABLE IF NOT EXISTS production_forecasts (
    ts                timestamptz NOT NULL,
    price_area        text        NOT NULL,
    wind_onshore_mw   double precision,
    wind_offshore_mw  double precision,
    solar_mw          double precision,
    created_at        timestamptz DEFAULT now(),
    PRIMARY KEY (ts, price_area)
);
SELECT create_hypertable('production_forecasts', 'ts',
    chunk_time_interval => interval '1 month', if_not_exists => TRUE);

-- 3. CO2 intensity of consumed power, 5-min
CREATE TABLE IF NOT EXISTS co2_emissions (
    ts             timestamptz NOT NULL,
    price_area     text        NOT NULL,
    co2_g_per_kwh  double precision,
    created_at     timestamptz DEFAULT now(),
    PRIMARY KEY (ts, price_area)
);
SELECT create_hypertable('co2_emissions', 'ts',
    chunk_time_interval => interval '1 month', if_not_exists => TRUE);

-- 4. Private consumption per municipality x housing x heating, hourly (the volume driver)
-- Source: PrivateConsumptionHeatingHour (~647 rows/hour, history to 2021)
CREATE TABLE IF NOT EXISTS private_consumption (
    ts                 timestamptz NOT NULL,
    municipality_code  integer     NOT NULL,
    housing_category   text        NOT NULL,
    heating_category   text        NOT NULL,
    consumption_kwh    double precision,
    created_at         timestamptz DEFAULT now(),
    PRIMARY KEY (ts, municipality_code, housing_category, heating_category)
);
SELECT create_hypertable('private_consumption', 'ts',
    chunk_time_interval => interval '1 month', if_not_exists => TRUE);

-- 5. Weather forecasts at fixed DK points, hourly (Open-Meteo, ML feature source)
-- Point-in-time forecasts: backfill from the historical-forecast archive,
-- daily rows from the live forecast API. Wind is at 100m hub height, in m/s
-- (the API default is km/h — the ingest requests wind_speed_unit=ms).
CREATE TABLE IF NOT EXISTS weather_forecasts (
    ts                       timestamptz NOT NULL,
    location                 text        NOT NULL,
    wind_speed_100m_ms       double precision,
    shortwave_radiation_wm2  double precision,
    temperature_2m_c         double precision,
    created_at               timestamptz DEFAULT now(),
    PRIMARY KEY (ts, location)
);
SELECT create_hypertable('weather_forecasts', 'ts',
    chunk_time_interval => interval '1 month', if_not_exists => TRUE);

-- 6. Model predictions of DK1 day-ahead prices (written each morning BEFORE the
-- 12:00 auction; actuals publish ~13:00). Rows are receipts: never re-predicted,
-- keyed by model_version so every prediction traces to the exact model file.
CREATE TABLE IF NOT EXISTS price_predictions (
    ts               timestamptz NOT NULL,   -- the hour being predicted
    predicted_price  double precision,       -- DK1 kr/kWh incl VAT (same unit as spot_prices.price_dkk_kwh)
    model_version    text        NOT NULL,   -- e.g. '2026-07' -> ml model file
    predicted_at     timestamptz DEFAULT now(),
    PRIMARY KEY (ts, model_version)
);
SELECT create_hypertable('price_predictions', 'ts',
    chunk_time_interval => interval '1 month', if_not_exists => TRUE);
