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

-- 4. Consumption per municipality x industry group, hourly (the volume driver)
CREATE TABLE IF NOT EXISTS consumption_municipality (
    ts               timestamptz NOT NULL,
    municipality_no  text        NOT NULL,
    industry_group   text        NOT NULL,
    consumption_kwh  double precision,
    created_at       timestamptz DEFAULT now(),
    PRIMARY KEY (ts, municipality_no, industry_group)
);
SELECT create_hypertable('consumption_municipality', 'ts',
    chunk_time_interval => interval '1 month', if_not_exists => TRUE);
