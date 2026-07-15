FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY *.py ./
COPY assets ./assets
COPY data ./data
COPY pages ./pages

# DB credentials arrive via --env-file at run time, never baked in.
CMD ["gunicorn", "--workers", "2", "--preload", "--bind", "0.0.0.0:8050", "app:server"]
