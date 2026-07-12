from dagster import Definitions, load_assets_from_modules

from energy_etl import assets  # noqa: TID252
from energy_etl.assets import (
    briefing_job,
    daily_briefing_schedule,
    daily_ingest_schedule,
    ingest_job,
)

all_assets = load_assets_from_modules([assets])

defs = Definitions(
    assets=all_assets,
    jobs=[ingest_job, briefing_job],
    schedules=[daily_ingest_schedule, daily_briefing_schedule],
)
