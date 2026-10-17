import logging
import time

from sqlalchemy import create_engine, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import OperationalError

from . import config
from .schema import (
    crop_profiles,
    garden_zone_sensors,
    garden_zones,
    sensor_readings,
    watering_program_valves,
    watering_programs,
    watering_runs,
    weather_forecast,
)

log = logging.getLogger("scheduler.db")


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


def get_enabled_programs(engine):
    """Renvoie la liste des programmes actifs, chacun avec ses vannes."""
    with engine.connect() as conn:
        program_rows = conn.execute(
            select(watering_programs).where(watering_programs.c.enabled.is_(True))
        ).mappings().all()

        programs = []
        for row in program_rows:
            valve_rows = conn.execute(
                select(watering_program_valves).where(watering_program_valves.c.program_id == row["id"])
            ).mappings().all()
            programs.append({**dict(row), "valves": [dict(v) for v in valve_rows]})
        return programs


def get_latest_tank_value(engine):
    stmt = (
        select(sensor_readings.c.value)
        .where(sensor_readings.c.metric == config.TANK_LEVEL_METRIC)
        .order_by(sensor_readings.c.time.desc())
        .limit(1)
    )
    with engine.connect() as conn:
        row = conn.execute(stmt).first()
    return row[0] if row else None


def get_latest_soil_moisture(engine, device_ids):
    """Dernière humidité du sol connue de chaque capteur demandé :
    {device_id: {"value": float, "time": datetime}}."""
    if not device_ids:
        return {}
    stmt = (
        select(sensor_readings.c.device_id, sensor_readings.c.value, sensor_readings.c.time)
        .where(sensor_readings.c.metric == config.SOIL_MOISTURE_METRIC)
        .where(sensor_readings.c.device_id.in_(list(device_ids)))
        .distinct(sensor_readings.c.device_id)
        .order_by(sensor_readings.c.device_id, sensor_readings.c.time.desc())
    )
    with engine.connect() as conn:
        rows = conn.execute(stmt).mappings().all()
    return {row["device_id"]: {"value": row["value"], "time": row["time"]} for row in rows}


def get_zones_by_valve(engine):
    """Zones du potager ayant une vanne, indexées par (device_id, metric) :
    {"name", "crop_name", "moisture_min", "dry_ref", "wet_ref", "sensors"}.
    crop_name / moisture_min valent None si la zone n'a pas de culture."""
    stmt = (
        select(
            garden_zones.c.id,
            garden_zones.c.name,
            garden_zones.c.valve_device_id,
            garden_zones.c.valve_metric,
            garden_zones.c.moisture_dry_ref,
            garden_zones.c.moisture_wet_ref,
            crop_profiles.c.name.label("crop_name"),
            crop_profiles.c.moisture_min,
        )
        .select_from(garden_zones.outerjoin(crop_profiles, crop_profiles.c.id == garden_zones.c.crop_profile_id))
        .where(garden_zones.c.valve_device_id.isnot(None))
    )
    with engine.connect() as conn:
        zones = conn.execute(stmt).mappings().all()
        sensor_rows = conn.execute(select(garden_zone_sensors.c.zone_id, garden_zone_sensors.c.device_id)).all()
    sensors_by_zone = {}
    for zone_id, device_id in sensor_rows:
        sensors_by_zone.setdefault(zone_id, []).append(device_id)
    return {
        (zone["valve_device_id"], zone["valve_metric"]): {
            "name": zone["name"],
            "crop_name": zone["crop_name"],
            "moisture_min": zone["moisture_min"],
            "dry_ref": zone["moisture_dry_ref"],
            "wet_ref": zone["moisture_wet_ref"],
            "sensors": sorted(sensors_by_zone.get(zone["id"], [])),
        }
        for zone in zones
    }


def get_rain_forecast_rows(engine):
    """weather_forecast est un instantané (voir weather/app/main.py) : pas de
    filtre de fraîcheur nécessaire, toute la table est la dernière prévision
    connue."""
    stmt = select(
        weather_forecast.c.valid_time, weather_forecast.c.source, weather_forecast.c.value
    ).where(weather_forecast.c.metric == "precipitation_mm")
    with engine.connect() as conn:
        rows = conn.execute(stmt).mappings().all()
    return [dict(r) for r in rows]


def try_claim_run(engine, program_id, program_name, scheduled_for, status, skip_reason, valves_triggered):
    """Tente d'enregistrer l'exécution de ce créneau planifié. Renvoie True
    si CET appel est le premier à l'enregistrer (la contrainte unique sur
    (program_id, scheduled_for) fait foi), False si un tick précédent l'a
    déjà fait — auquel cas il ne faut pas re-déclencher les vannes."""
    stmt = (
        pg_insert(watering_runs)
        .values(
            program_id=program_id,
            program_name=program_name,
            scheduled_for=scheduled_for,
            status=status,
            skip_reason=skip_reason,
            valves_triggered=valves_triggered,
        )
        .on_conflict_do_nothing(
            index_elements=["program_id", "scheduled_for"],
            index_where=watering_runs.c.program_id.isnot(None),
        )
        .returning(watering_runs.c.id)
    )
    with engine.begin() as conn:
        result = conn.execute(stmt)
        return result.first() is not None
