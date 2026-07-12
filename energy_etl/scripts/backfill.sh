#!/bin/bash
# Backfill 2021-01 .. current month, respecting the energidataservice rate limit.
# Small assets fetch a whole YEAR per run (BackfillPolicy.single_run + --partition-range);
# private_consumption stays monthly (~470k rows/month is enough for one run).
# Resumable: completed work is recorded in backfill_state.txt and skipped on rerun.
set -u
cd "$(dirname "$0")/.."
source ../.venv/bin/activate
set -a; source .env; set +a
export DAGSTER_HOME="$HOME/.dagster_home"

STATE=backfill_state.txt
LOG=backfill.log
touch "$STATE"

run_one() {  # run_one <asset> <partition-flag> <key-or-range>
  local asset=$1 flag=$2 key=$3
  if grep -q "^OK $asset $key$" "$STATE"; then return; fi
  echo "$(date '+%H:%M:%S') running $asset $key" | tee -a "$LOG"
  if dagster asset materialize -m energy_etl.definitions --select "$asset" "$flag" "$key" >> "$LOG" 2>&1; then
    echo "OK $asset $key" >> "$STATE"
  else
    echo "FAIL $asset $key" | tee -a "$LOG" >> "$STATE"
  fi
  sleep 20
}

year_ranges="2021-01-01...2021-12-01 2022-01-01...2022-12-01 2023-01-01...2023-12-01 2024-01-01...2024-12-01 2025-01-01...2025-12-01 2026-01-01...$(date '+%Y-%m')-01"

for asset in spot_prices production_forecasts co2_emissions; do
  for r in $year_ranges; do
    run_one "$asset" --partition-range "$r"
  done
done

months=$(python3 -c "
from datetime import date
d = date(2021, 1, 1)
today = date.today().replace(day=1)
while d <= today:
    print(d.isoformat())
    d = date(d.year + d.month // 12, d.month % 12 + 1, 1)
")
for m in $months; do
  run_one private_consumption --partition "$m"
done

echo "$(date '+%H:%M:%S') BACKFILL COMPLETE" | tee -a "$LOG"
