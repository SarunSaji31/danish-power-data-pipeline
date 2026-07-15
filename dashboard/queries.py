"""All dashboard SQL in one place — one function per chart.

Every query reads a continuous aggregate, never a raw hypertable (the raw
tables hold 31.8M rows; the caggs make these queries milliseconds). The only
exception is production_forecasts, which is small enough (~97k rows) to read
directly.
"""

from db import query_df


def kpi_summary():
    """One-row frame powering the KPI tiles on the home page.

    Prices use max(day): day-ahead data is complete the moment it exists.
    CO2 uses the last day BEFORE max(day): measured 5-min data, newest day
    is partial and would fake a drop. Row count is approximate_row_count()
    (catalog stats, instant on 30M compressed rows; a real count(*) is not)."""
    return query_df(
        "WITH price_latest AS ("
        "    SELECT day, avg_price FROM prices_daily"
        "    WHERE price_area = 'DK1' ORDER BY day DESC LIMIT 1"
        "), price_prev AS ("
        "    SELECT p.avg_price FROM prices_daily p, price_latest l"
        "    WHERE p.price_area = 'DK1' AND p.day = l.day - interval '1 day'"
        "), co2_latest AS ("
        "    SELECT day, avg_co2 FROM co2_daily"
        "    WHERE price_area = 'DK1' AND day < (SELECT max(day) FROM co2_daily)"
        "    ORDER BY day DESC LIMIT 1"
        "), co2_prev AS ("
        "    SELECT c.avg_co2 FROM co2_daily c, co2_latest l"
        "    WHERE c.price_area = 'DK1' AND c.day = l.day - interval '1 day'"
        "), neg AS ("
        "    SELECT count(*) FILTER (WHERE hour >= now() - interval '30 days') AS last30,"
        "           count(*) FILTER (WHERE hour <  now() - interval '30 days') AS prev30"
        "    FROM prices_hourly"
        "    WHERE price_area = 'DK1' AND avg_spot_mwh < 0"
        "      AND hour >= now() - interval '60 days'"
        "), total AS ("
        "    SELECT approximate_row_count('spot_prices')"
        "         + approximate_row_count('production_forecasts')"
        "         + approximate_row_count('co2_emissions')"
        "         + approximate_row_count('private_consumption') AS rows"
        ") "
        "SELECT (SELECT day       FROM price_latest) AS price_day,"
        "       (SELECT avg_price FROM price_latest) AS price_now,"
        "       (SELECT avg_price FROM price_prev)   AS price_prev,"
        "       (SELECT day       FROM co2_latest)   AS co2_day,"
        "       (SELECT avg_co2   FROM co2_latest)   AS co2_now,"
        "       (SELECT avg_co2   FROM co2_prev)     AS co2_prev,"
        "       (SELECT last30 FROM neg)             AS neg_last30,"
        "       (SELECT prev30 FROM neg)             AS neg_prev30,"
        "       (SELECT rows FROM total)             AS total_rows"
    )


def daily_prices():
    """Daily average consumer price per zone, full history."""
    return query_df(
        "SELECT day, price_area, avg_price "
        "FROM prices_daily ORDER BY day, price_area"
    )


def price_heatmap_dk1():
    """Avg DK1 price by hour-of-day (Copenhagen time) x month, full history."""
    return query_df(
        "SELECT date_trunc('month', hour) AS month, "
        "       extract(hour FROM hour AT TIME ZONE 'Europe/Copenhagen')::int AS hour_of_day, "
        "       avg(avg_price) AS avg_price "
        "FROM prices_hourly WHERE price_area = 'DK1' "
        "GROUP BY 1, 2 ORDER BY 1, 2"
    )


def negative_price_hours():
    """Hours per month where the raw market price went below zero, per zone."""
    return query_df(
        "SELECT date_trunc('month', hour) AS month, price_area, "
        "       count(*) AS negative_hours "
        "FROM prices_hourly WHERE avg_spot_mwh < 0 "
        "GROUP BY 1, 2 ORDER BY 1, 2"
    )


def latest_day_ahead():
    """Hourly prices for the newest full Copenhagen day in the data —
    the day-ahead curve consumers actually face."""
    return query_df(
        "WITH latest AS ("
        "    SELECT date_trunc('day', max(hour) AT TIME ZONE 'Europe/Copenhagen') AS d"
        "    FROM prices_hourly"
        ") "
        "SELECT hour, price_area, avg_price FROM prices_hourly, latest "
        "WHERE date_trunc('day', hour AT TIME ZONE 'Europe/Copenhagen') = latest.d "
        "ORDER BY hour, price_area"
    )


def duration_curve(months: int = 12):
    """Hourly prices sorted high->low per zone (classic energy-market chart:
    how many hours of the year are expensive/cheap/negative)."""
    return query_df(
        "SELECT price_area, avg_price FROM prices_hourly "
        "WHERE hour >= now() - make_interval(months => %s) "
        "ORDER BY price_area, avg_price DESC",
        (months,),
    )


def hourly_profile_by_year():
    """Avg DK1 price per hour of day (Copenhagen time), one row set per year —
    shows the midday solar dip deepening year over year."""
    return query_df(
        "SELECT extract(year FROM hour AT TIME ZONE 'Europe/Copenhagen')::int AS year, "
        "       extract(hour FROM hour AT TIME ZONE 'Europe/Copenhagen')::int AS hour_of_day, "
        "       avg(avg_price) AS avg_price "
        "FROM prices_hourly WHERE price_area = 'DK1' "
        "GROUP BY 1, 2 ORDER BY 1, 2"
    )


def consumption_all_municipalities(months: int = 12):
    """Total consumption (GWh) for every municipality, trailing N months —
    feeds the choropleth map."""
    return query_df(
        "SELECT municipality_code, sum(consumption_kwh) / 1e6 AS gwh "
        "FROM consumption_daily_municipality "
        "WHERE day >= now() - make_interval(months => %s) "
        "GROUP BY 1 ORDER BY 1",
        (months,),
    )


def municipality_monthly(code: int):
    """Monthly consumption (GWh) for one municipality, full history
    (current incomplete month excluded)."""
    return query_df(
        "SELECT date_trunc('month', day) AS month, "
        "       sum(consumption_kwh) / 1e6 AS gwh "
        "FROM consumption_daily_municipality "
        "WHERE municipality_code = %s AND day < date_trunc('month', now()) "
        "GROUP BY 1 ORDER BY 1",
        (code,),
    )


def daily_wind_solar():
    """Daily average forecast MW per source, DK1+DK2 combined."""
    return query_df(
        "SELECT date_trunc('day', ts) AS day, "
        "       avg(wind_onshore_mw)  AS onshore_mw, "
        "       avg(wind_offshore_mw) AS offshore_mw, "
        "       avg(solar_mw)         AS solar_mw "
        "FROM production_forecasts GROUP BY 1 ORDER BY 1"
    )


def daily_co2():
    """Daily average CO2 intensity per zone."""
    return query_df(
        "SELECT day, price_area, avg_co2 FROM co2_daily ORDER BY day, price_area"
    )


def top_municipalities(months: int = 12, limit: int = 15):
    """Total consumption (GWh) per municipality over the trailing N months."""
    return query_df(
        "SELECT municipality_code, sum(consumption_kwh) / 1e6 AS gwh "
        "FROM consumption_daily_municipality "
        "WHERE day >= now() - make_interval(months => %s) "
        "GROUP BY 1 ORDER BY 2 DESC LIMIT %s",
        (months, limit),
    )


def monthly_consumption_by_heating():
    """Monthly national consumption (GWh) split by heating category.
    Excludes the current month — it is incomplete and would plot as a crash."""
    return query_df(
        "SELECT date_trunc('month', day) AS month, heating_category, "
        "       sum(consumption_kwh) / 1e6 AS gwh "
        "FROM consumption_daily_heating "
        "WHERE day >= '2021-01-01' AND day < date_trunc('month', now()) "
        "GROUP BY 1, 2 ORDER BY 1, 2"
    )


def wind_vs_price_dk1():
    """Daily total wind forecast vs daily avg price, DK1 — the correlation story."""
    return query_df(
        "SELECT p.day, "
        "       w.wind_mw, "
        "       p.avg_price "
        "FROM prices_daily p "
        "JOIN (SELECT date_trunc('day', ts) AS day, "
        "             avg(wind_onshore_mw + wind_offshore_mw) AS wind_mw "
        "      FROM production_forecasts WHERE price_area = 'DK1' "
        "      GROUP BY 1) w USING (day) "
        "WHERE p.price_area = 'DK1' ORDER BY p.day"
    )
