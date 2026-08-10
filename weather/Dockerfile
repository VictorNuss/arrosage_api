# cfgrib/eccodes (bindings Python) ont besoin de la bibliothèque système
# eccodes pour décoder les GRIB2 renvoyés par les API WCS AROME/ARPEGE ;
# disponible via apt (libeccodes-dev) sur cette image de base.
FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends libeccodes-dev \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/

ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PROJECT_ENVIRONMENT=/usr/local

WORKDIR /app

COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-install-project --no-dev

COPY app ./app

CMD ["python", "-u", "-m", "app.main"]
