"""Error analysis for the day-ahead backtest (run after backtest.py).

1. WHERE the model fails: error distribution, MAE by hour-of-day, spike hours
   vs normal hours, the worst individual misses with their context.
2. CEILING model: same LightGBM walk-forward but WITH the leaky EDS day-ahead
   wind/solar MW features (published after the auction — not usable in reality).
   The honest-vs-ceiling gap = the price of leak-freedom.
3. VINTAGE gap (Option C promise): our training weather comes from the
   historical-forecast archive (short-lead values). Compare it, on 2024+, with
   the strictly pre-auction previous_day1 vintage (ECMWF) to measure how
   optimistic the archive really is.

Run:  python ml/error_analysis.py
"""

import sys
from pathlib import Path

import lightgbm as lgb
import pandas as pd
import requests

sys.path.append(str(Path(__file__).resolve().parent))
from backtest import (  # noqa: E402
    CALENDAR_FEATURES, LGBM_PARAMS, NUMERIC_FEATURES, REPO, TARGET,
    load_training_frame, month_starts,
)


# --- 1. where does the model fail? -------------------------------------------

def where_it_fails(preds: pd.DataFrame, frame: pd.DataFrame) -> None:
    p = preds.merge(frame[["ts", "hour_cph", "wind_northsea_ms", "price_lag_24h"]], on="ts")
    p["err"] = (p["lightgbm"] - p["actual"]).abs()

    print("=== 1a. absolute error distribution (kr/kWh) ===")
    q = p["err"].quantile([0.5, 0.75, 0.9, 0.99]).round(3)
    print(f"  median {q[0.5]}   p75 {q[0.75]}   p90 {q[0.9]}   p99 {q[0.99]}   max {p['err'].max():.3f}")
    print(f"  -> half of all hours are off by <= {q[0.5]} kr; the tail carries the MAE")

    print("\n=== 1b. MAE by hour of day (CPH) ===")
    by_hour = p.groupby("hour_cph", observed=True)["err"].mean().round(3)
    worst = by_hour.sort_values(ascending=False)
    print("  worst hours:", ", ".join(f"{h:02d}h={v}" for h, v in worst.head(4).items()))
    print("  best hours: ", ", ".join(f"{h:02d}h={v}" for h, v in worst.tail(4).items()))

    print("\n=== 1c. spike hours (top 1% actual price) vs the rest ===")
    cut = p["actual"].quantile(0.99)
    spike = p["actual"] >= cut
    for label, mask in [("spike (>= {:.2f} kr)".format(cut), spike), ("normal", ~spike)]:
        naive_mae = (p.loc[mask, "naive"] - p.loc[mask, "actual"]).abs().mean()
        print(f"  {label:<22} n={int(mask.sum()):>6}  lgbm MAE {p.loc[mask,'err'].mean():.3f}"
              f"  naive MAE {naive_mae:.3f}")

    print("\n=== 1d. ten worst misses ===")
    cols = ["ts", "actual", "lightgbm", "naive", "price_lag_24h", "wind_northsea_ms"]
    print(p.nlargest(10, "err")[cols].round(2).to_string(index=False))


# --- 2. the leaky EDS ceiling model -------------------------------------------

def ceiling_model(frame: pd.DataFrame) -> None:
    import os
    import psycopg2
    with psycopg2.connect(
        host=os.environ["ENERGY_DB_HOST"], port=os.environ["ENERGY_DB_PORT"],
        user=os.environ["ENERGY_DB_USER"], password=os.environ["ENERGY_DB_PASSWORD"],
        dbname=os.environ["ENERGY_DB_NAME"],
    ) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT ts, wind_onshore_mw + wind_offshore_mw AS eds_wind_mw, "
            "solar_mw AS eds_solar_mw FROM production_forecasts WHERE price_area='DK1'"
        )
        eds = pd.DataFrame(cur.fetchall(), columns=["ts", "eds_wind_mw", "eds_solar_mw"])
    df = frame.merge(eds, on="ts").dropna(subset=["eds_wind_mw", "eds_solar_mw"])
    df[["eds_wind_mw", "eds_solar_mw"]] = df[["eds_wind_mw", "eds_solar_mw"]].astype(float)

    honest_X = df[NUMERIC_FEATURES + CALENDAR_FEATURES]
    ceiling_X = df[NUMERIC_FEATURES + ["eds_wind_mw", "eds_solar_mw"] + CALENDAR_FEATURES]
    y = df[TARGET]

    rows = {"honest": [], "ceiling": [], "actual": []}
    for start in month_starts(df):
        end = start + pd.DateOffset(months=1)
        train = df["ts"] < start
        test = (df["ts"] >= start) & (df["ts"] < end)
        for name, X in [("honest", honest_X), ("ceiling", ceiling_X)]:
            model = lgb.LGBMRegressor(**LGBM_PARAMS).fit(X[train], y[train])
            rows[name].append(pd.Series(model.predict(X[test])))
        rows["actual"].append(y[test].reset_index(drop=True))

    actual = pd.concat(rows["actual"], ignore_index=True)
    print("=== 2. honest vs leaky-EDS ceiling (same rows, same walk-forward) ===")
    for name in ["honest", "ceiling"]:
        mae = (pd.concat(rows[name], ignore_index=True) - actual).abs().mean()
        print(f"  {name:<8} MAE {mae:.4f} kr/kWh")


# --- 3. vintage gap: archive vs strictly pre-auction previous_day1 ------------

def vintage_gap() -> None:
    print("=== 3. archive vs previous_day1 wind (2024-04 -> now, ECMWF) ===")
    import os
    import psycopg2
    for location, lat, lon in [("jutland_west", 56.0, 8.4), ("north_sea", 55.7, 7.8)]:
        r = requests.get(
            "https://previous-runs-api.open-meteo.com/v1/forecast",
            params={"latitude": lat, "longitude": lon,
                    "start_date": "2024-04-01", "end_date": "2026-07-13",
                    "hourly": "wind_speed_100m_previous_day1",
                    "models": "ecmwf_ifs025", "wind_speed_unit": "ms", "timezone": "UTC"},
            timeout=120,
        )
        r.raise_for_status()
        h = r.json()["hourly"]
        prev = pd.DataFrame({"ts": pd.to_datetime(h["time"], utc=True),
                             "prev_day1": h[list(h)[1]]}).dropna()

        with psycopg2.connect(
            host=os.environ["ENERGY_DB_HOST"], port=os.environ["ENERGY_DB_PORT"],
            user=os.environ["ENERGY_DB_USER"], password=os.environ["ENERGY_DB_PASSWORD"],
            dbname=os.environ["ENERGY_DB_NAME"],
        ) as conn, conn.cursor() as cur:
            cur.execute("SELECT ts, wind_speed_100m_ms FROM weather_forecasts WHERE location=%s",
                        (location,))
            ours = pd.DataFrame(cur.fetchall(), columns=["ts", "archive"])
        m = prev.merge(ours, on="ts").astype({"prev_day1": float, "archive": float})
        diff = (m["archive"] - m["prev_day1"]).abs()
        print(f"  {location:<13} n={len(m):,}  corr {m['archive'].corr(m['prev_day1']):.3f}"
              f"  mean|diff| {diff.mean():.2f} m/s  p90|diff| {diff.quantile(0.9):.2f} m/s")


if __name__ == "__main__":
    frame = load_training_frame()
    preds = pd.read_csv(REPO / "ml" / "backtest_predictions.csv", parse_dates=["ts"])
    where_it_fails(preds, frame)
    print()
    ceiling_model(frame)
    print()
    vintage_gap()
