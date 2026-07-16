FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    DAGSTER_HOME=/dagster_home

# lightgbm's wheel needs OpenMP, which slim images don't ship
RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY pyproject.toml README.md ./
COPY energy_etl ./energy_etl
RUN pip install --no-cache-dir .

COPY deploy/dagster.yaml /dagster_home/dagster.yaml
COPY deploy/workspace.yaml /app/workspace.yaml

CMD ["dagster-daemon", "run", "-w", "/app/workspace.yaml"]
