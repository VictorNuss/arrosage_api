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

    Le paramètre 'precipitation_mm' de weather_forecast est, par convention
    Météo-France pour le produit AROME/ARPEGE "précipitation totale", cumulé
    depuis le début du run plutôt qu'exprimé par pas de temps. On calcule
    donc le cumul sur une fenêtre par différence entre les deux échéances les
    plus proches de ses bornes, plutôt qu'en sommant les valeurs brutes (ce
    qui compterait plusieurs fois la même pluie). Renvoie None si aucune
    prévision n'est disponible (service météo pas configuré).
    """
    forecast = get_weather_forecast()
    precip = forecast[forecast["metric"] == "precipitation_mm"].sort_values("valid_time")
    if precip.empty:
        return None

    now = datetime.now(timezone.utc)

    def cumulative_at(target):
        past = precip[precip["valid_time"] <= target]
        if past.empty:
            return 0.0
        return float(past.iloc[-1]["value"])

    baseline = cumulative_at(now)
    return {
        "rain_3h_mm": max(0.0, cumulative_at(now + timedelta(hours=3)) - baseline),
        "rain_48h_mm": max(0.0, cumulative_at(now + timedelta(hours=48)) - baseline),
    }
