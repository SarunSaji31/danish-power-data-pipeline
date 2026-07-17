"""Feature contract for the DK1 day-ahead price model.

Single source of truth shared by training (ml/ scripts, local) and the
price_forecast asset (production). The saved model file stores only trees —
prediction inputs must match training exactly (columns, units, lag semantics,
category encoding), so everything that defines a feature lives HERE.

TRAINING_SQL and PREDICTION_SQL must stay semantically identical: same hourly
price series, same lag definitions, same weather pivot, same CPH calendar.
Training anchors on realized prices over full history; prediction generates
tomorrow's hour series (whose prices don't exist yet) and joins the same lags.
"""

from pathlib import Path

import pandas as pd

MODELS_DIR = Path(__file__).resolve().parent / "models"

FEATURE_COLUMNS = [
    "price_lag_24h", "price_lag_168h", "price_avg_prev24h",
    "wind_jutland_ms", "wind_northsea_ms", "wind_zealand_ms",
    "solar_rad_wm2", "temp_c",
    "gas_eur_mwh",
    "hour_cph", "weekday", "month",
]

# Fixed category sets: pandas infers category codes from the values present,
# and a single prediction day contains only one weekday — inferred codes would
# silently scramble the encoding the trees were built on.
CALENDAR_CATEGORIES = {
    "hour_cph": list(range(24)),
    "weekday": list(range(1, 8)),   # isodow: 1=Monday
    "month": list(range(1, 13)),
}


def encode_calendar(df: pd.DataFrame) -> pd.DataFrame:
    for column, categories in CALENDAR_CATEGORIES.items():
        df[column] = pd.Categorical(df[column], categories=categories)
    return df


def latest_model_path() -> Path:
    """Newest model file by version date. Versions are YYYY-MM-DD; the first
    model used YYYY-MM, which must sort as day 01 — plain lexical order would
    rank 'model_2026-07.txt' after 'model_2026-07-17.txt' ('.' > '-')."""
    def version_date(path: Path) -> str:
        version = model_version(path)
        return version + "-01" if len(version) == 7 else version
    return max(MODELS_DIR.glob("model_*.txt"), key=version_date)


def model_version(path: Path) -> str:
    return path.stem.removeprefix("model_")


TRAINING_SQL = """
WITH hourly AS (
    -- target series: hourly DK1 consumer price (15-min rows since 2025-10
    -- average up to hours; hourly rows before pass through unchanged)
    SELECT date_trunc('hour', ts) AS ts,
           avg(price_dkk_kwh)     AS price
    FROM spot_prices
    WHERE price_area = 'DK1'
    GROUP BY 1
),
lagged AS (
    -- UTC hours are gap-free (verified full coverage), so row-offset lags are
    -- exact time lags: 24 rows = 24 hours, 168 rows = 1 week
    SELECT ts,
           price,
           lag(price, 24)  OVER (ORDER BY ts) AS price_lag_24h,
           lag(price, 168) OVER (ORDER BY ts) AS price_lag_168h,
           avg(price) OVER (ORDER BY ts ROWS BETWEEN 47 PRECEDING AND 24 PRECEDING)
               AS price_avg_prev24h
    FROM hourly
),
weather AS (
    SELECT ts,
           max(wind_speed_100m_ms) FILTER (WHERE location = 'jutland_west') AS wind_jutland_ms,
           max(wind_speed_100m_ms) FILTER (WHERE location = 'north_sea')    AS wind_northsea_ms,
           max(wind_speed_100m_ms) FILTER (WHERE location = 'zealand')      AS wind_zealand_ms,
           avg(shortwave_radiation_wm2)                                     AS solar_rad_wm2,
           avg(temperature_2m_c)                                            AS temp_c
    FROM weather_forecasts
    GROUP BY ts
)
SELECT l.ts,
       extract(hour   FROM l.ts AT TIME ZONE 'Europe/Copenhagen')::int AS hour_cph,
       extract(isodow FROM l.ts AT TIME ZONE 'Europe/Copenhagen')::int AS weekday,
       extract(month  FROM l.ts AT TIME ZONE 'Europe/Copenhagen')::int AS month,
       l.price_lag_24h,
       l.price_lag_168h,
       l.price_avg_prev24h,
       w.wind_jutland_ms,
       w.wind_northsea_ms,
       w.wind_zealand_ms,
       w.solar_rad_wm2,
       w.temp_c,
       g.gas_eur_mwh,
       l.price AS target_price
FROM lagged l
JOIN weather w USING (ts)
-- gas vintage rule: for target day T use the last settlement dated <= T-2 —
-- the newest one that existed at the 08:15 prediction run on T-1 (T-1's own
-- close happens that evening); "<=" forward-fills weekends and holidays
LEFT JOIN LATERAL (
    SELECT close_eur_mwh AS gas_eur_mwh
    FROM gas_prices
    WHERE date <= (l.ts AT TIME ZONE 'Europe/Copenhagen')::date - 2
    ORDER BY date DESC LIMIT 1
) g ON true
WHERE l.price_lag_168h IS NOT NULL
ORDER BY l.ts
"""

# Same features for hours whose prices DON'T exist yet (tomorrow's): generate
# the hour series and join each lag by explicit time offset. Time-offset joins
# equal the training window-function lags because the hourly series is gap-free.
PREDICTION_SQL = """
WITH hourly AS (
    SELECT date_trunc('hour', ts) AS ts,
           avg(price_dkk_kwh)     AS price
    FROM spot_prices
    WHERE price_area = 'DK1'
    GROUP BY 1
),
hours AS (
    SELECT generate_series(%(start)s::timestamptz,
                           %(end)s::timestamptz - interval '1 hour',
                           interval '1 hour') AS ts
),
weather AS (
    SELECT ts,
           max(wind_speed_100m_ms) FILTER (WHERE location = 'jutland_west') AS wind_jutland_ms,
           max(wind_speed_100m_ms) FILTER (WHERE location = 'north_sea')    AS wind_northsea_ms,
           max(wind_speed_100m_ms) FILTER (WHERE location = 'zealand')      AS wind_zealand_ms,
           avg(shortwave_radiation_wm2)                                     AS solar_rad_wm2,
           avg(temperature_2m_c)                                            AS temp_c
    FROM weather_forecasts
    GROUP BY ts
)
SELECT h.ts,
       extract(hour   FROM h.ts AT TIME ZONE 'Europe/Copenhagen')::int AS hour_cph,
       extract(isodow FROM h.ts AT TIME ZONE 'Europe/Copenhagen')::int AS weekday,
       extract(month  FROM h.ts AT TIME ZONE 'Europe/Copenhagen')::int AS month,
       l24.price  AS price_lag_24h,
       l168.price AS price_lag_168h,
       (SELECT avg(price) FROM hourly a
        WHERE a.ts >= h.ts - interval '47 hours'
          AND a.ts <= h.ts - interval '24 hours') AS price_avg_prev24h,
       w.wind_jutland_ms,
       w.wind_northsea_ms,
       w.wind_zealand_ms,
       w.solar_rad_wm2,
       w.temp_c,
       g.gas_eur_mwh
FROM hours h
JOIN hourly  l24  ON l24.ts  = h.ts - interval '24 hours'
JOIN hourly  l168 ON l168.ts = h.ts - interval '168 hours'
JOIN weather w    ON w.ts    = h.ts
-- same gas vintage rule as TRAINING_SQL: last settlement dated <= target day - 2
LEFT JOIN LATERAL (
    SELECT close_eur_mwh AS gas_eur_mwh
    FROM gas_prices
    WHERE date <= (h.ts AT TIME ZONE 'Europe/Copenhagen')::date - 2
    ORDER BY date DESC LIMIT 1
) g ON true
ORDER BY h.ts
"""
