import logging
import time

from sqlalchemy import create_engine, delete
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import OperationalError

from . import config
from .schema import weather_forecast, weather_observed

log = logging.getLogger("weather.db")


def create_engine_with_retry(max_attempts=30, delay_seconds=2):
    url = (
        f"postgresql+psycopg://{config.POSTGRES_USER}:{config.POSTGRES_PASSWORD}"
        f"@{config.POSTGRES_HOST}:{config.POSTGRES_PORT}/{config.POSTGRES_DB}"
    )
    engine = create_engine(url, pool_pre_ping=True)

    attempt = 0
    while True:
        attempt += 1
        try:
            with engine.connect():
                pass
            log.info("Connecté à PostgreSQL (%s:%s)", config.POSTGRES_HOST, config.POSTGRES_PORT)
            return engine
        except OperationalError as exc:
            if attempt >= max_attempts:
                raise
            log.warning("PostgreSQL indisponible (tentative %s/%s): %s", attempt, max_attempts, exc)
            time.sleep(delay_seconds)


def replace_forecast(engine, fetched_at, source, lat, lon, rows):
    """rows: liste de dicts (valid_time, metric, value). Remplace entièrement
    l'instantané précédent pour cette source (pas d'historique conservé)."""
    with engine.begin() as conn:
        conn.execute(delete(weather_forecast).where(weather_forecast.c.source == source))
        if rows:
            conn.execute(
                weather_forecast.insert(),
                [
                    {
                        "fetched_at": fetched_at,
                        "valid_time": row["valid_time"],
                        "source": source,
                        "lat": lat,
                        "lon": lon,
                        "metric": row["metric"],
                        "value": row["value"],
                    }
                    for row in rows
                ],
            )


def upsert_observed(engine, station_id, rows):
    """rows: liste de dicts (time, metric, value)."""
    if not rows:
        return
    stmt = pg_insert(weather_observed).values(
        [
            {"time": row["time"], "station_id": station_id, "metric": row["metric"], "value": row["value"]}
            for row in rows
        ]
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=["station_id", "metric", "time"],
        set_={"value": stmt.excluded.value},
    )
    with engine.begin() as conn:
        conn.execute(stmt)
