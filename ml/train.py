"""Train the production model on ALL available history and save it.

The walk-forward backtest (backtest.py) validated this exact configuration:
MAE 0.1938 vs naive 0.2866 over 24 out-of-sample months (12 features incl.
TTF gas). This script freezes one model trained on everything, saved in
LightGBM's native text format (version-stable, human-readable, no pickle).

Versions are full dates so a retrain never overwrites the file an earlier
prediction receipt points to.

Run:  python ml/train.py        -> energy_etl/energy_etl/models/model_YYYY-MM-DD.txt
"""

from datetime import date
from pathlib import Path

import lightgbm as lgb

import sys
sys.path.append(str(Path(__file__).resolve().parent))
from backtest import LGBM_PARAMS, TARGET, load_training_frame  # noqa: E402
from energy_etl.ml import FEATURE_COLUMNS, MODELS_DIR  # noqa: E402


def main() -> None:
    df = load_training_frame()  # already calendar-encoded via energy_etl.ml
    X, y = df[FEATURE_COLUMNS], df[TARGET]

    model = lgb.LGBMRegressor(**LGBM_PARAMS).fit(X, y)

    MODELS_DIR.mkdir(exist_ok=True)
    version = date.today().strftime("%Y-%m-%d")
    path = MODELS_DIR / f"model_{version}.txt"
    model.booster_.save_model(path)

    print(f"trained on {len(df):,} rows ({df['ts'].min():%Y-%m-%d} -> {df['ts'].max():%Y-%m-%d})")
    print(f"saved -> {path.relative_to(path.parent.parent.parent)} "
          f"({path.stat().st_size / 1024:.0f} KB, version {version})")


if __name__ == "__main__":
    main()
