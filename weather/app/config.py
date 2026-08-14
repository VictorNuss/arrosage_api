import os

POSTGRES_HOST = os.environ.get("POSTGRES_HOST", "timescaledb")
POSTGRES_PORT = int(os.environ.get("POSTGRES_PORT", "5432"))
POSTGRES_USER = os.environ.get("POSTGRES_USER", "arrosage")
POSTGRES_PASSWORD = os.environ.get("POSTGRES_PASSWORD", "")
POSTGRES_DB = os.environ.get("POSTGRES_DB", "arrosage")

WEATHER_LAT = float(os.environ.get("WEATHER_LAT", "45.1885"))
WEATHER_LON = float(os.environ.get("WEATHER_LON", "5.7245"))

# Open-Meteo : gratuit, sans clé API pour un usage non-commercial.
OPEN_METEO_PAST_DAYS = 7
OPEN_METEO_FORECAST_DAYS = 4

REFRESH_INTERVAL_SECONDS = 3 * 3600
