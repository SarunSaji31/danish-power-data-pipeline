# ML: day-ahead DK1 price forecasting

Predicts tomorrow's 24 hourly DK1 consumer prices **before the 12:00 CET
day-ahead auction** (results publish ~13:00 — any later prediction would be
worthless). Features are leak-free by construction: price lags, calendar,
point-in-time morning weather forecasts (Open-Meteo) — NOT the grid operator's
wind/solar MW forecast, which publishes only in the evening (verified) — and
the previous TTF gas settlement (always at least two days old relative to the
target day, i.e. already published at prediction time).

## Results (walk-forward backtest, 24 monthly retrains, 17,520 OOS hours)

| forecaster | MAE (DKK/kWh) |
|---|---|
| seasonal naive (= last week) | 0.3707 |
| naive (= today) — the bar | 0.2866 |
| linear regression | 0.2234 |
| LightGBM, 11 features (weather + lags) | 0.2104 |
| **LightGBM + TTF gas (shipped)** | **0.1938 (−32% vs naive)** |

Beats naive in every tested year. Adding the leaky grid-operator features to
measure a "ceiling" changes MAE by only ~0.8% — the honest weather features
recover almost all of it.

**What the gas feature did and didn't fix** (measured on identical
out-of-sample hours): it improved every error percentile (p50 0.151→0.139,
p90 0.452→0.409, p99 1.017→0.935) — the model now sees the fuel-cost *regime*
instead of inferring it from price lags. It did **not** fix the scarcity-spike
blind spot: on the top-1% price hours the error grew (1.27→1.46, still under
naive's 1.56), and the Dec-2024 Dunkelflaute miss is unchanged — that event
happened at a *normal* gas price, so those spikes are capacity scarcity, not
fuel cost. The remaining missing signal is interconnector/outage data, not gas.

## Data note: gas source

Daily TTF front-month settlement (EUR/MWh) from Yahoo Finance's chart API —
an unofficial source, documented as such (TTF settlements are ICE/EEX exchange
data with no free official API; a production trading system would license
them). Two gotchas encoded in the ingest: range-style requests silently
downsample old history to ~weekly (explicit `period1`/`period2` windows are
required), and the feature join must use the last settlement dated ≤ target
day − 2 — the newest one that existed at the morning prediction run — with
"≤" forward-filling weekends and holidays identically in training and serving.

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
07:45 weather + gas fetches): loads the newest `energy_etl/energy_etl/models/
model_YYYY-MM-DD.txt` (LightGBM native format), builds tomorrow's 24 feature
rows via `PREDICTION_SQL`, writes predictions to the `price_predictions`
hypertable keyed by `(ts, model_version)` — predictions are immutable
receipts, which is also why model files are never overwritten: each retrain
gets a new dated file so every stored prediction stays traceable to the exact
model that made it.

## Retraining policy

Monthly, deliberately manual:

1. `python ml/train.py` → writes `model_YYYY-MM-DD.txt` (new dated file)
2. commit the model file (git history = model registry) and push
3. CI ships it; `latest_model_path()` picks the newest file automatically

Monthly cadence is what the backtest simulated, so production matches the
evaluated setup. Automating training in CI is possible future work; at one
model, the added complexity buys nothing.
