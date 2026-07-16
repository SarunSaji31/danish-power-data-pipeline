# ML: day-ahead DK1 price forecasting

Predicts tomorrow's 24 hourly DK1 consumer prices **before the 12:00 CET
day-ahead auction** (results publish ~13:00 — any later prediction would be
worthless). Features are leak-free by construction: price lags, calendar, and
point-in-time morning weather forecasts (Open-Meteo) — NOT the grid operator's
wind/solar MW forecast, which publishes only in the evening (verified).

## Results (walk-forward backtest, 24 monthly retrains, 17,520 OOS hours)

| forecaster | MAE (DKK/kWh) |
|---|---|
| seasonal naive (= last week) | 0.3707 |
| naive (= today) — the bar | 0.2866 |
| linear regression | 0.2394 |
| **LightGBM (shipped)** | **0.2104 (−27% vs naive)** |

Beats naive in every tested year. Known blind spot (documented, expected):
calm-wind scarcity spikes (e.g. the Dec 2024 Dunkelflaute) — the missing
gas-price/interconnector signal; the model still beats naive on those hours.
Adding the leaky grid-operator features to measure a "ceiling" changes MAE by
only ~0.8% — the honest weather features recover almost all of it.

## Files

- `backtest.py` — walk-forward evaluation (the honest number). Run first.
- `error_analysis.py` — where it fails, ceiling model, forecast-vintage gap.
- `train.py` — trains on ALL history, saves the production model.
- `baselines.sql` — the naive bars, computable without any ML.
- Feature contract (SQL + encoding) lives in `energy_etl/energy_etl/ml.py` —
  single source shared with the production asset; the consistency of the
  training and prediction feature paths is verified (identical output).

## Production

The `price_forecast` Dagster asset runs each morning at 08:15 CPH (after the
07:45 weather fetch): loads the newest `energy_etl/energy_etl/models/
model_YYYY-MM.txt` (LightGBM native format), builds tomorrow's 24 feature rows
via `PREDICTION_SQL`, writes predictions to the `price_predictions` hypertable
keyed by `(ts, model_version)` — predictions are immutable receipts. The
dashboard's prices page overlays forecast vs auction result with a rolling
30-day MAE.

## Retraining policy

Monthly, deliberately manual:

1. `python ml/train.py` → writes `model_YYYY-MM.txt` (new dated file)
2. commit the model file (git history = model registry) and push
3. CI ships it; `latest_model_path()` picks the newest file automatically

Monthly cadence is what the backtest simulated, so production matches the
evaluated setup. Automating training in CI is possible future work; at one
model, the added complexity buys nothing.
