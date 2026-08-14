from datetime import datetime, timedelta, timezone

import pandas as pd
from sqlalchemy import create_engine, func, select

from . import config
from .schema import devices, sensor_readings, sensor_readings_hourly, weather_forecast, weather_observed

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
    """Dernière valeur connue pour chaque (device, metric)."""
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
        return pd.read_sql_query(stmt, conn)


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
