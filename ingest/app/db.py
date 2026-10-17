import logging
import time

from sqlalchemy import create_engine, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import OperationalError

from . import config
from .mqtt_client import is_valve_metric
from .schema import devices, sensor_readings, valve_events

log = logging.getLogger("ingest.db")


def create_engine_with_retry(max_attempts=30, delay_seconds=2):
    """Postgres peut mettre quelques secondes à devenir prêt au premier démarrage."""
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


def ensure_device(engine, device_id):
    stmt = (
        pg_insert(devices)
        .values(device_id=device_id, name=device_id)
        .on_conflict_do_nothing(index_elements=["device_id"])
    )
    with engine.begin() as conn:
        conn.execute(stmt)


def update_device_info(engine, device_id, fields):
    """fields: {colonne de devices: valeur}, ex {"ip_address": "192.168.1.50"}."""
    with engine.begin() as conn:
        conn.execute(devices.update().where(devices.c.device_id == device_id).values(**fields))


def valve_event_rows(rows):
    """Lignes de vannes à copier dans valve_events : journal permanent des
    arrosages, qui survit à la purge du brut (voir 005_data_retention.sql)."""
    return [
        {key: row[key] for key in ("time", "device_id", "metric", "value")}
        for row in rows
        if is_valve_metric(row["metric"])
    ]


def insert_readings(engine, rows):
    """rows: liste de dicts (time, device_id, metric, value, unit). Les
    vannes vont aussi dans valve_events, dans la même transaction."""
    if not rows:
        return
    valve_rows = valve_event_rows(rows)
    with engine.begin() as conn:
        conn.execute(sensor_readings.insert(), rows)
        if valve_rows:
            conn.execute(valve_events.insert(), valve_rows)


def get_known_device_ids(engine):
    with engine.connect() as conn:
        return [row[0] for row in conn.execute(select(devices.c.device_id))]
