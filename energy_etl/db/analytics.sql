-- Continuous aggregates + compression. Idempotent, safe to re-run.
-- Caggs = incrementally-maintained materialized views: dashboards read these
-- instead of aggregating millions of raw rows on every page load.

-- 1. Daily price summary per zone
CREATE MATERIALIZED VIEW IF NOT EXISTS prices_daily
WITH (timescaledb.continuous) AS
SELECT time_bucket('1 day', ts) AS day,
       price_area,
       avg(price_dkk_kwh) AS avg_price,
       min(price_dkk_kwh) AS min_price,
       max(price_dkk_kwh) AS max_price
FROM spot_prices
GROUP BY 1, 2
WITH NO DATA;

-- 2. Daily CO2 intensity per zone
CREATE MATERIALIZED VIEW IF NOT EXISTS co2_daily
WITH (timescaledb.continuous) AS
SELECT time_bucket('1 day', ts) AS day,
       price_area,
       avg(co2_g_per_kwh) AS avg_co2,
       min(co2_g_per_kwh) AS min_co2,
       max(co2_g_per_kwh) AS max_co2
FROM co2_emissions
GROUP BY 1, 2
WITH NO DATA;

-- 3. Daily consumption per municipality (collapses housing/heating dims)
CREATE MATERIALIZED VIEW IF NOT EXISTS consumption_daily_municipality
WITH (timescaledb.continuous) AS
SELECT time_bucket('1 day', ts) AS day,
       municipality_code,
       sum(consumption_kwh) AS consumption_kwh
FROM private_consumption
GROUP BY 1, 2
WITH NO DATA;

-- 4. Hourly price summary per zone (dashboard: hour-of-day heatmap,
--    negative-price-hour counts). Also normalizes the 15-min grain post-2025-10.
CREATE MATERIALIZED VIEW IF NOT EXISTS prices_hourly
WITH (timescaledb.continuous) AS
SELECT time_bucket('1 hour', ts) AS hour,
       price_area,
       avg(price_dkk_kwh) AS avg_price,
       avg(spot_price_dkk_mwh) AS avg_spot_mwh
FROM spot_prices
GROUP BY 1, 2
WITH NO DATA;

-- 5. Daily consumption per heating category (dashboard: heat-pump vs other;
--    the municipality cagg collapses this dimension away)
CREATE MATERIALIZED VIEW IF NOT EXISTS consumption_daily_heating
WITH (timescaledb.continuous) AS
SELECT time_bucket('1 day', ts) AS day,
       heating_category,
       sum(consumption_kwh) AS consumption_kwh
FROM private_consumption
GROUP BY 1, 2
WITH NO DATA;

-- Keep the aggregates fresh automatically: every hour, re-materialize the
-- window [3 days ago, 1 hour ago] — covers late-arriving upserts.
SELECT add_continuous_aggregate_policy('prices_daily',
    start_offset => INTERVAL '3 days', end_offset => INTERVAL '1 hour',
    schedule_interval => INTERVAL '1 hour', if_not_exists => TRUE);
SELECT add_continuous_aggregate_policy('co2_daily',
    start_offset => INTERVAL '3 days', end_offset => INTERVAL '1 hour',
    schedule_interval => INTERVAL '1 hour', if_not_exists => TRUE);
SELECT add_continuous_aggregate_policy('consumption_daily_municipality',
    start_offset => INTERVAL '10 days', end_offset => INTERVAL '1 hour',
    schedule_interval => INTERVAL '1 hour', if_not_exists => TRUE);
SELECT add_continuous_aggregate_policy('prices_hourly',
    start_offset => INTERVAL '3 days', end_offset => INTERVAL '1 hour',
    schedule_interval => INTERVAL '1 hour', if_not_exists => TRUE);
SELECT add_continuous_aggregate_policy('consumption_daily_heating',
    start_offset => INTERVAL '10 days', end_offset => INTERVAL '1 hour',
    schedule_interval => INTERVAL '1 hour', if_not_exists => TRUE);

-- Compression: old chunks become columnar (~10-20x smaller). segmentby = the
-- column queries filter/group on; orderby = time for maximal compression.
ALTER TABLE spot_prices SET (timescaledb.compress,
    timescaledb.compress_segmentby = 'price_area', timescaledb.compress_orderby = 'ts');
ALTER TABLE production_forecasts SET (timescaledb.compress,
    timescaledb.compress_segmentby = 'price_area', timescaledb.compress_orderby = 'ts');
ALTER TABLE co2_emissions SET (timescaledb.compress,
    timescaledb.compress_segmentby = 'price_area', timescaledb.compress_orderby = 'ts');
ALTER TABLE private_consumption SET (timescaledb.compress,
    timescaledb.compress_segmentby = 'municipality_code', timescaledb.compress_orderby = 'ts');

-- Compress chunks once they're older than 30 days (background job)
SELECT add_compression_policy('spot_prices', INTERVAL '30 days', if_not_exists => TRUE);
SELECT add_compression_policy('production_forecasts', INTERVAL '30 days', if_not_exists => TRUE);
SELECT add_compression_policy('co2_emissions', INTERVAL '30 days', if_not_exists => TRUE);
SELECT add_compression_policy('private_consumption', INTERVAL '30 days', if_not_exists => TRUE);
