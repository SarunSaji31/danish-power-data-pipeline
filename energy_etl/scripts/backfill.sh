#!/bin/bash
# Sequential monthly backfill of all partitioned assets (2021-01 .. current month).
# Respects the energidataservice rate limit; resumable — completed partitions
# are recorded in backfill_state.txt and skipped on rerun.
set -u
cd "$(dirname "$0")/.."
source ../.venv/bin/activate
set -a; source .env; set +a
export DAGSTER_HOME="$HOME/.dagster_home"

STATE=backfill_state.txt
LOG=backfill.log
touch "$STATE"

months=$(python3 -c "
from datetime import date
d = date(2021, 1, 1)
today = date.today().replace(day=1)
while d <= today:
    print(d.isoformat())
    d = date(d.year + d.month // 12, d.month % 12 + 1, 1)
")

for asset in spot_prices production_forecasts co2_emissions private_consumption; do
  for m in $months; do
    if grep -q "^OK $asset $m$" "$STATE"; then continue; fi
    echo "$(date '+%H:%M:%S') running $asset $m" | tee -a "$LOG"
    if dagster asset materialize -m energy_etl.definitions --select "$asset" --partition "$m" >> "$LOG" 2>&1; then
      echo "OK $asset $m" >> "$STATE"
    else
      echo "FAIL $asset $m" | tee -a "$LOG"
      echo "FAIL $asset $m" >> "$STATE"
    fi
    sleep 20
  done
done
echo "$(date '+%H:%M:%S') BACKFILL COMPLETE" | tee -a "$LOG"
