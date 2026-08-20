from datetime import datetime, timedelta, timezone

import pandas as pd
from sqlalchemy import create_engine, func, select

from . import config, live_state
from .schema import (
    devices,
    sensor_readings,
    sensor_readings_hourly,
    watering_program_valves,
    watering_programs,
    watering_runs,
    weather_forecast,
    weather_observed,
)

engine = create_engine(config.DATABASE_URL, pool_pre_ping=True)

VALVE_METRIC_HINT = "vanne"


def is_valve_metric(metric: str) -> bool:
    return VALVE_METRIC_HINT in metric.lower()


def get_devices() -> pd.DataFrame:
    stmt = select(
        devices.c.device_id,
        devices.c.name,
        devices.c.location,
        devices.c.lat,
        devices.c.lon,
        devices.c.ip_address,
    ).order_by(devices.c.device_id)
    with engine.connect() as conn:
        return pd.read_sql_query(stmt, conn)


def set_device_ip(device_id: str, ip_address: str) -> None:
    with engine.begin() as conn:
        conn.execute(
            devices.update().where(devices.c.device_id == device_id).values(ip_address=ip_address)
        )


def get_known_metrics() -> list[str]:
    stmt = select(sensor_readings.c.metric).distinct().order_by(sensor_readings.c.metric)
    with engine.connect() as conn:
        df = pd.read_sql_query(stmt, conn)
    return df["metric"].tolist()


def get_latest_readings() -> pd.DataFrame:
    """Dernière valeur connue pour chaque (device, metric).

    Fusionne l'instantané base (source de vérité durable) avec le cache
    mémoire alimenté par l'abonnement MQTT direct du dashboard
    (live_state.py) : ce dernier reflète la dernière valeur reçue à
    l'instant près, sans attendre le batching de `ingest` ni un nouveau
    poll. En cas de clé (device, metric) présente des deux côtés, la plus
    récente des deux l'emporte.
    """
    stmt = (
        select(
            sensor_readings.c.device_id,
            sensor_readings.c.metric,
            sensor_readings.c.value,
            sensor_readings.c.unit,
            sensor_readings.c.time,
        )
        .distinct(sensor_readings.c.device_id, sensor_readings.c.metric)
        .order_by(sensor_readings.c.device_id, sensor_readings.c.metric, sensor_readings.c.time.desc())
    )
    with engine.connect() as conn:
        db_df = pd.read_sql_query(stmt, conn)
    # La base ne connaît pas la notion de "direction" de transition (voir
    # live_state.py) : seul le cache mémoire la fournit.
    db_df["direction"] = None

    live_rows = live_state.get_latest_readings()
    if not live_rows:
        return db_df

    live_df = pd.DataFrame(live_rows, columns=["device_id", "metric", "value", "unit", "time", "direction"])
    live_df["time"] = pd.to_datetime(live_df["time"], utc=True)
    if not db_df.empty:
        db_df["time"] = pd.to_datetime(db_df["time"], utc=True)

    combined = pd.concat([db_df, live_df], ignore_index=True)
    combined = combined.sort_values("time").drop_duplicates(subset=["device_id", "metric"], keep="last")
    return combined.reset_index(drop=True)


def get_history(devices_filter: list[str], metrics: list[str], start: datetime, end: datetime) -> pd.DataFrame:
    if not devices_filter or not metrics:
        return pd.DataFrame(columns=["time", "device_id", "metric", "value"])

    use_aggregate = (end - start) > timedelta(days=config.HOURLY_AGGREGATE_THRESHOLD_DAYS)

    if use_aggregate:
        h = sensor_readings_hourly
        stmt = (
            select(h.c.bucket.label("time"), h.c.device_id, h.c.metric, h.c.avg_value.label("value"))
            .where(h.c.device_id.in_(devices_filter))
            .where(h.c.metric.in_(metrics))
            .where(h.c.bucket.between(start, end))
            .order_by(h.c.bucket)
        )
    else:
        s = sensor_readings
        stmt = (
            select(s.c.time, s.c.device_id, s.c.metric, s.c.value)
            .where(s.c.device_id.in_(devices_filter))
            .where(s.c.metric.in_(metrics))
            .where(s.c.time.between(start, end))
            .order_by(s.c.time)
        )

    with engine.connect() as conn:
        return pd.read_sql_query(stmt, conn)


def get_weather_forecast() -> pd.DataFrame:
    """weather_forecast est un instantané (pas d'historique) : toute la table
    représente la dernière prévision connue."""
    stmt = select(
        weather_forecast.c.valid_time, weather_forecast.c.source, weather_forecast.c.metric, weather_forecast.c.value
    ).order_by(weather_forecast.c.valid_time)
    with engine.connect() as conn:
        return pd.read_sql_query(stmt, conn)


def get_weather_observed(days: int = 30) -> pd.DataFrame:
    cutoff = func.now() - func.make_interval(0, 0, 0, days)
    stmt = (
        select(weather_observed.c.time, weather_observed.c.station_id, weather_observed.c.metric, weather_observed.c.value)
        .where(weather_observed.c.time >= cutoff)
        .order_by(weather_observed.c.time)
    )
    with engine.connect() as conn:
        return pd.read_sql_query(stmt, conn)


def get_valve_states() -> pd.DataFrame:
    """Dernier état connu de chaque vanne (toutes appareils confondus)."""
    latest = get_latest_readings()
    if latest.empty:
        return latest
    return latest[latest["metric"].apply(is_valve_metric)].sort_values(["device_id", "metric"])


def get_tank_level() -> dict | None:
    """Dernière hauteur d'eau connue (métrique config.TANK_LEVEL_METRIC), ou
    None si aucun appareil n'a encore renvoyé cette mesure."""
    latest = get_latest_readings()
    matches = latest[latest["metric"] == config.TANK_LEVEL_METRIC]
    if matches.empty:
        return None
    row = matches.sort_values("time").iloc[-1]
    return {"device_id": row["device_id"], "value": float(row["value"]), "time": row["time"]}


def get_rain_outlook() -> dict | None:
    """Cumul de pluie prévu sur les 3h et les 48h à venir.

    Le paramètre 'precipitation_mm' d'Open-Meteo est la pluie tombée pendant
    l'heure précédant chaque horodatage (pas un cumul depuis le début du
    run) : on peut donc simplement sommer les valeurs sur la fenêtre voulue.
    AROME et ARPEGE se chevauchent sur les échéances proches ; on garde
    AROME en priorité (plus fin) pour ne pas compter la même pluie deux
    fois. Renvoie None si aucune prévision n'est disponible (service météo
    pas encore alimenté).
    """
    forecast = get_weather_forecast()
    precip = forecast[forecast["metric"] == "precipitation_mm"]
    if precip.empty:
        return None

    source_priority = {"AROME": 0, "ARPEGE": 1}
    precip = precip.sort_values("source", key=lambda s: s.map(source_priority).fillna(99))
    precip = precip.drop_duplicates(subset="valid_time", keep="first")

    now = datetime.now(timezone.utc)

    def sum_window(hours):
        window = precip[(precip["valid_time"] >= now) & (precip["valid_time"] <= now + timedelta(hours=hours))]
        return float(window["value"].sum())

    return {"rain_3h_mm": sum_window(3), "rain_48h_mm": sum_window(48)}


def get_known_valves() -> list[dict]:
    """Toutes les vannes déjà vues au moins une fois (device_id, metric)."""
    stmt = (
        select(sensor_readings.c.device_id, sensor_readings.c.metric)
        .where(sensor_readings.c.metric.ilike(f"%{VALVE_METRIC_HINT}%"))
        .distinct()
        .order_by(sensor_readings.c.device_id, sensor_readings.c.metric)
    )
    with engine.connect() as conn:
        return pd.read_sql_query(stmt, conn).to_dict("records")


def get_programs() -> list[dict]:
    """Tous les programmes d'arrosage, chacun avec ses vannes."""
    with engine.connect() as conn:
        program_rows = conn.execute(select(watering_programs).order_by(watering_programs.c.id)).mappings().all()
        programs = []
        for row in program_rows:
            valve_rows = conn.execute(
                select(watering_program_valves).where(watering_program_valves.c.program_id == row["id"])
            ).mappings().all()
            programs.append({**dict(row), "valves": [dict(v) for v in valve_rows]})
        return programs


def get_program(program_id: int) -> dict | None:
    for program in get_programs():
        if program["id"] == program_id:
            return program
    return None


def save_program(
    program_id,
    name,
    enabled,
    start_time,
    days_of_week,
    default_duration_s,
    conditions_list,
    valves,
) -> int:
    """Crée un programme si program_id est None, sinon le met à jour.
    valves : liste de tuples (device_id, metric). Renvoie l'id du programme."""
    now = datetime.now(timezone.utc)
    with engine.begin() as conn:
        if program_id is None:
            result = conn.execute(
                watering_programs.insert()
                .values(
                    name=name,
                    enabled=enabled,
                    start_time=start_time,
                    days_of_week=days_of_week,
                    default_duration_s=default_duration_s,
                    conditions=conditions_list,
                    created_at=now,
                    updated_at=now,
                )
                .returning(watering_programs.c.id)
            )
            program_id = result.scalar_one()
        else:
            conn.execute(
                watering_programs.update()
                .where(watering_programs.c.id == program_id)
                .values(
                    name=name,
                    enabled=enabled,
                    start_time=start_time,
                    days_of_week=days_of_week,
                    default_duration_s=default_duration_s,
                    conditions=conditions_list,
                    updated_at=now,
                )
            )
            conn.execute(
                watering_program_valves.delete().where(watering_program_valves.c.program_id == program_id)
            )

        if valves:
            conn.execute(
                watering_program_valves.insert(),
                [{"program_id": program_id, "device_id": d, "metric": m} for d, m in valves],
            )

    return program_id


def set_program_enabled(program_id: int, enabled: bool) -> None:
    with engine.begin() as conn:
        conn.execute(
            watering_programs.update().where(watering_programs.c.id == program_id).values(enabled=enabled)
        )


def delete_program(program_id: int) -> None:
    with engine.begin() as conn:
        conn.execute(watering_programs.delete().where(watering_programs.c.id == program_id))


def get_recent_runs(limit: int = 20) -> pd.DataFrame:
    stmt = select(watering_runs).order_by(watering_runs.c.scheduled_for.desc()).limit(limit)
    with engine.connect() as conn:
        return pd.read_sql_query(stmt, conn)
