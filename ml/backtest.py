"""Walk-forward backtest for day-ahead DK1 price forecasting.

For each of the last N_TEST_MONTHS full months: train on all history BEFORE the
month, predict the month, slide forward. Every model is scored on the exact same
out-of-sample rows, so the MAEs are directly comparable:

    naive           tomorrow = today (price_lag_24h)
    seasonal        tomorrow = last week (price_lag_168h)
    linear          LinearRegression, calendar one-hot encoded
    lightgbm        LGBMRegressor, fixed modest params, calendar as categoricals

Run:  python ml/backtest.py   (reads DB creds from energy_etl/.env)
"""

from pathlib import Path

import lightgbm as lgb
import pandas as pd
import psycopg2
from dotenv import load_dotenv
from sklearn.linear_model import LinearRegression

import os

from energy_etl.ml import CALENDAR_CATEGORIES, TRAINING_SQL, encode_calendar

REPO = Path(__file__).resolve().parent.parent
N_TEST_MONTHS = 24

CALENDAR_FEATURES = list(CALENDAR_CATEGORIES)
NUMERIC_FEATURES = [
    "price_lag_24h", "price_lag_168h", "price_avg_prev24h",
    "wind_jutland_ms", "wind_northsea_ms", "wind_zealand_ms",
    "solar_rad_wm2", "temp_c",
]
TARGET = "target_price"

LGBM_PARAMS = dict(
    n_estimators=500, learning_rate=0.05, num_leaves=63,
    subsample=0.9, colsample_bytree=0.9, random_state=42, verbose=-1,
)


def load_training_frame() -> pd.DataFrame:
    load_dotenv(REPO / "energy_etl" / ".env")
    with psycopg2.connect(
        host=os.environ["ENERGY_DB_HOST"], port=os.environ["ENERGY_DB_PORT"],
        user=os.environ["ENERGY_DB_USER"], password=os.environ["ENERGY_DB_PASSWORD"],
        dbname=os.environ["ENERGY_DB_NAME"],
    ) as conn, conn.cursor() as cur:
        cur.execute(TRAINING_SQL)
        df = pd.DataFrame(cur.fetchall(), columns=[c.name for c in cur.description])
    df = df.astype({c: float for c in NUMERIC_FEATURES + [TARGET]})
    return encode_calendar(df)


def month_starts(df: pd.DataFrame) -> list[pd.Timestamp]:
    """Start timestamps of the last N_TEST_MONTHS FULL months in the data."""
    last_complete = df["ts"].max().normalize().replace(day=1)  # current month excluded
    return [last_complete - pd.DateOffset(months=i) for i in range(N_TEST_MONTHS, 0, -1)]


def main() -> None:
    df = load_training_frame()
    print(f"training frame: {len(df):,} rows  {df['ts'].min():%Y-%m-%d} -> {df['ts'].max():%Y-%m-%d}")

    # one-hot calendar once, globally, so train/test matrices always align
    linear_X = pd.get_dummies(df[NUMERIC_FEATURES + CALENDAR_FEATURES],
                              columns=CALENDAR_FEATURES, dtype=float)
    lgbm_X = df[NUMERIC_FEATURES + CALENDAR_FEATURES]
    y = df[TARGET]

    preds = []  # one frame per test month with every model's predictions
    for start in month_starts(df):
        end = start + pd.DateOffset(months=1)
        train = df["ts"] < start
        test = (df["ts"] >= start) & (df["ts"] < end)

        linear = LinearRegression().fit(linear_X[train], y[train])
        booster = lgb.LGBMRegressor(**LGBM_PARAMS).fit(lgbm_X[train], y[train])

        preds.append(pd.DataFrame({
            "ts": df.loc[test, "ts"],
            "actual": y[test],
            "naive": df.loc[test, "price_lag_24h"],
            "seasonal": df.loc[test, "price_lag_168h"],
            "linear": linear.predict(linear_X[test]),
            "lightgbm": booster.predict(lgbm_X[test]),
        }))
        print(f"  {start:%Y-%m}: trained on {int(train.sum()):,} rows, scored {int(test.sum()):,}")

    out = pd.concat(preds)
    models = ["naive", "seasonal", "linear", "lightgbm"]

    print(f"\n=== MAE over {len(out):,} out-of-sample hours (kr/kWh) ===")
    for m in models:
        mae = (out[m] - out["actual"]).abs().mean()
        print(f"  {m:<9} {mae:.4f}")

    print("\n=== MAE per year ===")
    out["year"] = out["ts"].dt.year
    per_year = out.groupby("year").apply(
        lambda g: pd.Series({m: (g[m] - g["actual"]).abs().mean() for m in models}),
        include_groups=False,
    )
    print(per_year.round(4).to_string())

    print("\n=== LightGBM feature importance (gain, final month's model) ===")
    imp = pd.Series(booster.booster_.feature_importance("gain"),
                    index=booster.feature_name_).sort_values(ascending=False)
    print((imp / imp.sum() * 100).round(1).astype(str).add(" %").to_string())

    out.to_csv(REPO / "ml" / "backtest_predictions.csv", index=False)
    print("\npredictions saved -> ml/backtest_predictions.csv")


if __name__ == "__main__":
    main()
