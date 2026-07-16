-- Baseline forecasts every model must beat (MAE, DKK/kWh incl. VAT).
--
--   naive:          tomorrow's price at hour h = today's price at hour h  (lag 24h)
--   seasonal naive: = last week's same weekday at hour h                  (lag 168h)
--
-- Scored over the last 24 full months. MAE (not MAPE: negative prices break
-- percentage errors; not RMSE as headline: spike days would dominate it).

WITH hourly AS (
    SELECT date_trunc('hour', ts) AS ts,
           avg(price_dkk_kwh)     AS price
    FROM spot_prices
    WHERE price_area = 'DK1'
    GROUP BY 1
),
lagged AS (
    SELECT ts, price,
           lag(price, 24)  OVER (ORDER BY ts) AS naive,
           lag(price, 168) OVER (ORDER BY ts) AS seasonal
    FROM hourly
)
SELECT count(*)                                        AS hours_scored,
       round(avg(abs(price - naive))::numeric,    4)   AS naive_mae,
       round(avg(abs(price - seasonal))::numeric, 4)   AS seasonal_naive_mae
FROM lagged
WHERE ts >= date_trunc('month', now()) - interval '24 months'
  AND ts <  date_trunc('month', now());
