import threading
import time
import unicodedata
from datetime import datetime, timedelta, timezone

import pandas as pd
from sqlalchemy import create_engine, func, literal_column, select

from . import config, live_state
from .schema import (
    crop_profiles,
    devices,
    garden_zone_sensors,
    garden_zones,
    sensor_readings,
    sensor_readings_1min,
    sensor_readings_daily,
    sensor_readings_hourly,
    valve_events,
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
        devices.c.fw_version,
    ).order_by(devices.c.device_id)
    with engine.connect() as conn:
        return pd.read_sql_query(stmt, conn)


def set_device_ip(device_id: str, ip_address: str) -> None:
    with engine.begin() as conn:
        conn.execute(
            devices.update().where(devices.c.device_id == device_id).values(ip_address=ip_address)
        )


# Deux abonnés MQTT indépendants (ingest et le cache mémoire du dashboard)
# reçoivent le même message à quelques instants près l'un de l'autre : leurs
# horodatages de réception peuvent différer de quelques millisecondes sans
# que ça ne signifie que l'un des deux a raté un message. Cette marge évite
# qu'un tel écart insignifiant ne fasse "gagner" la ligne base (qui ne
# connaît pas la direction de transition, toujours None) sur la ligne cache
# mémoire (qui la connaît) pour un seul et même évènement.
_LIVE_CACHE_STALE_BUFFER = timedelta(seconds=5)


_latest_cache_lock = threading.Lock()
_latest_cache: tuple[float, pd.DataFrame] | None = None


def _query_db_latest_readings() -> pd.DataFrame:
    """Instantané base des dernières valeurs, mis en cache
    LATEST_READINGS_CACHE_S : à chaque tick, plusieurs callbacks (vannes,
    cuve, sol, cartes) le demandent en même temps."""
    global _latest_cache
    with _latest_cache_lock:
        now = time.monotonic()
        if _latest_cache is not None and now - _latest_cache[0] < config.LATEST_READINGS_CACHE_S:
            return _latest_cache[1].copy()
        df = _fetch_db_latest_readings()
        _latest_cache = (now, df)
        return df.copy()


def _fetch_db_latest_readings() -> pd.DataFrame:
    # Pas de borne de temps : l'index (device_id, metric, time DESC) rend
    # déjà ce DISTINCT ON rapide (~7 ms sur 14 jours de brut à 5 s, chunks
    # compressés compris), et une borne ferait perdre les dernières valeurs
    # anciennes (cuve muette, "dernière mesure" de l'onglet Firmware).
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
    # Etat des vannes depuis le journal permanent : une vanne restée fermée
    # plus longtemps que la conservation du brut (15 jours) garde son état,
    # sans dépendre du message retenu que le cache live ignore.
    v = valve_events
    valve_stmt = (
        select(v.c.device_id, v.c.metric, v.c.value, v.c.time)
        .distinct(v.c.device_id, v.c.metric)
        .order_by(v.c.device_id, v.c.metric, v.c.time.desc())
    )
    with engine.connect() as conn:
        db_df = pd.read_sql_query(stmt, conn)
        valve_df = pd.read_sql_query(valve_stmt, conn)
    return merge_valve_journal(db_df, valve_df)


def merge_valve_journal(readings_df: pd.DataFrame, valve_df: pd.DataFrame) -> pd.DataFrame:
    """Ajoute à la dernière mesure de chaque clé du brut les vannes du
    journal permanent ; une seule ligne par (device, métrique), la plus
    récente (une vanne présente dans les deux a la même ligne)."""
    if not valve_df.empty:
        valve_df = valve_df.assign(unit=None)
        frames = [df for df in (readings_df, valve_df) if not df.empty]
        readings_df = pd.concat(frames, ignore_index=True)
        readings_df["time"] = pd.to_datetime(readings_df["time"], utc=True)
        readings_df = (
            readings_df.sort_values("time")
            .drop_duplicates(subset=["device_id", "metric"], keep="last")
            .sort_values(["device_id", "metric"])
            .reset_index(drop=True)
        )
    # La base ne connaît pas la notion de "direction" de transition (voir
    # live_state.py) : seul le cache mémoire la fournit.
    readings_df["direction"] = None
    return readings_df


def get_latest_readings() -> pd.DataFrame:
    """Dernière valeur connue pour chaque (device, metric).

    Fusionne l'instantané base (source de vérité durable) avec le cache
    mémoire alimenté par l'abonnement MQTT direct du dashboard
    (live_state.py) : ce dernier reflète la dernière valeur reçue à
    l'instant près, sans attendre le batching de `ingest` ni un nouveau
    poll. Pour une clé connue du cache mémoire, on le préfère toujours à la
    base, sauf si celle-ci est nettement plus fraîche (au-delà de
    `_LIVE_CACHE_STALE_BUFFER`) — signe que le cache mémoire a raté un
    message (ex: coupure MQTT côté dashboard), pas juste l'écart normal
    entre deux abonnés indépendants au même évènement.
    """
    db_df = _query_db_latest_readings()

    live_rows = live_state.get_latest_readings()
    if not live_rows:
        return db_df

    live_df = pd.DataFrame(live_rows, columns=["device_id", "metric", "value", "unit", "time", "direction"])
    live_df["time"] = pd.to_datetime(live_df["time"], utc=True)
    if db_df.empty:
        return live_df
    db_df["time"] = pd.to_datetime(db_df["time"], utc=True)

    live_times = {
        (row.device_id, row.metric): row.time for row in live_df.itertuples()
    }

    def _db_row_needed(row):
        key = (row["device_id"], row["metric"])
        live_time = live_times.get(key)
        if live_time is None:
            return True
        return row["time"] > live_time + _LIVE_CACHE_STALE_BUFFER

    db_only = db_df[db_df.apply(_db_row_needed, axis=1)]
    # Une clé où la base l'emporte ne doit plus apparaître côté cache : sinon
    # deux lignes pour la même (device, metric), soit une vanne affichée deux
    # fois dans le panneau.
    db_keys = set(zip(db_only["device_id"], db_only["metric"]))
    live_only = live_df[[key not in db_keys for key in zip(live_df["device_id"], live_df["metric"])]]
    combined = pd.concat([db_only, live_only], ignore_index=True)
    return combined.reset_index(drop=True)


def history_source(span: timedelta):
    """Agrégat à interroger pour afficher `span` : assez fin pour être
    lisible, assez grossier pour garder quelques milliers de points par
    courbe (le brut, ~5 s, n'est jamais tracé tel quel)."""
    if span <= timedelta(days=config.HISTORY_1MIN_MAX_DAYS):
        return sensor_readings_1min
    if span <= timedelta(days=config.HISTORY_HOURLY_MAX_DAYS):
        return sensor_readings_hourly
    return sensor_readings_daily


_TIER_STEPS = {
    "sensor_readings_1min": timedelta(minutes=1),
    "sensor_readings_hourly": timedelta(hours=1),
    "sensor_readings_daily": timedelta(days=1),
}

# Pas de ré-échantillonnage proposés, alignés sur des durées « rondes ». Pas
# de multiple du jour : l'agrégat journalier est en jours locaux, un
# time_bucket UTC par-dessus le décalerait.
_RESAMPLE_STEPS = [
    timedelta(minutes=m) for m in (1, 2, 5, 10, 15, 30)
] + [timedelta(hours=h) for h in (1, 2, 3, 6, 12)]


def resample_step(span: timedelta, max_points: int | None) -> timedelta | None:
    """Pas de regroupement à appliquer à l'agrégat de history_source(span)
    pour ne pas dépasser `max_points` points par courbe, ou None si le pas
    natif de l'agrégat suffit déjà (ou si aucun pas proposé ne convient)."""
    if not max_points:
        return None
    native = _TIER_STEPS[history_source(span).name]
    if span / native <= max_points:
        return None
    for step in _RESAMPLE_STEPS:
        if step > native and span / step <= max_points:
            return step
    return None


def get_history(
    devices_filter: list[str],
    metrics: list[str] | None,
    start: datetime,
    end: datetime,
    max_points: int | None = None,
) -> pd.DataFrame:
    """Moyenne par intervalle (minute, heure ou jour selon la durée, voir
    history_source), colonnes time/device_id/metric/value.

    Avec `max_points`, les intervalles sont regroupés (voir resample_step)
    pour rester sous ce nombre de points par courbe : moyenne pondérée par
    le nombre d'échantillons de chaque intervalle. Sans, une ligne par
    intervalle natif (garden.sun_hours compte des minutes).

    `metrics` à None : toutes les mesures présentes dans l'agrégat sur la
    période, y compris celles d'un capteur retiré depuis (le brut, lui, ne
    garde que 15 jours)."""
    if not devices_filter or metrics == []:
        return pd.DataFrame(columns=["time", "device_id", "metric", "value"])

    agg = history_source(end - start)
    step = resample_step(end - start, max_points)
    filters = [agg.c.device_id.in_(devices_filter), agg.c.bucket.between(start, end)]
    if metrics is not None:
        filters.append(agg.c.metric.in_(metrics))
    if step is None:
        stmt = (
            select(agg.c.bucket.label("time"), agg.c.device_id, agg.c.metric, agg.c.avg_value.label("value"))
            .where(*filters)
            .order_by(agg.c.bucket)
        )
    else:
        # Intervalle en littéral (pas issu d'une saisie : voir
        # _RESAMPLE_STEPS) : avec un paramètre lié, SELECT et GROUP BY
        # porteraient deux paramètres distincts et Postgres refuserait le
        # regroupement.
        interval = literal_column(f"INTERVAL '{int(step.total_seconds())} seconds'")
        time = func.time_bucket(interval, agg.c.bucket).label("time")
        value = (func.sum(agg.c.avg_value * agg.c.sample_count) / func.sum(agg.c.sample_count)).label("value")
        stmt = (
            select(time, agg.c.device_id, agg.c.metric, value)
            .where(*filters)
            .group_by(time, agg.c.device_id, agg.c.metric)
            .order_by(time)
        )
    with engine.connect() as conn:
        return pd.read_sql_query(stmt, conn)


def get_valve_history(devices_filter: list[str], start: datetime, end: datetime) -> pd.DataFrame:
    """Changements d'état des vannes sur [start, end], depuis le journal
    permanent valve_events (jamais purgé, contrairement au brut), précédés
    du dernier état connu avant `start` pour chaque vanne : sans lui, une
    vanne déjà ouverte au début de la période n'apparaîtrait qu'à sa
    fermeture. Quelques lignes par arrosage : volume faible même sur
    plusieurs années."""
    if not devices_filter:
        return pd.DataFrame(columns=["time", "device_id", "metric", "value"])

    v = valve_events
    in_devices = v.c.device_id.in_(devices_filter)
    previous_state = (
        select(v.c.time, v.c.device_id, v.c.metric, v.c.value)
        .where(in_devices, v.c.time < start)
        .distinct(v.c.device_id, v.c.metric)
        .order_by(v.c.device_id, v.c.metric, v.c.time.desc())
    )
    in_period = select(v.c.time, v.c.device_id, v.c.metric, v.c.value).where(
        in_devices, v.c.time.between(start, end)
    )
    with engine.connect() as conn:
        df = pd.concat(
            [pd.read_sql_query(previous_state, conn), pd.read_sql_query(in_period, conn)], ignore_index=True
        )
    return df.sort_values("time").reset_index(drop=True)


def get_weather_forecast() -> pd.DataFrame:
    """weather_forecast est un instantané (pas d'historique) : toute la table
    représente la dernière prévision connue."""
    stmt = select(
        weather_forecast.c.valid_time, weather_forecast.c.source, weather_forecast.c.metric, weather_forecast.c.value
    ).order_by(weather_forecast.c.valid_time)
    with engine.connect() as conn:
        return pd.read_sql_query(stmt, conn)


# AROME (1,3 km, ~2 jours) est plus fin qu'ARPEGE (~4 jours) : là où les deux
# couvrent la même échéance, on garde AROME.
_FORECAST_SOURCE_PRIORITY = {"AROME": 0, "ARPEGE": 1}


def prefer_finest_source(forecast: pd.DataFrame) -> pd.DataFrame:
    """Une seule valeur par (échéance, métrique) : AROME en priorité, ARPEGE
    au-delà de l'horizon d'AROME. Évite de compter deux fois la même pluie
    ou de tracer une courbe qui oscille entre les deux modèles."""
    if forecast.empty:
        return forecast
    ordered = forecast.sort_values(
        "source", key=lambda s: s.map(_FORECAST_SOURCE_PRIORITY).fillna(99), kind="stable"
    )
    return ordered.drop_duplicates(subset=["valid_time", "metric"], keep="first").sort_values("valid_time")


def get_weather_observed(days: int = 30) -> pd.DataFrame:
    cutoff = func.now() - func.make_interval(0, 0, 0, days)
    stmt = (
        select(weather_observed.c.time, weather_observed.c.station_id, weather_observed.c.metric, weather_observed.c.value)
        .where(weather_observed.c.time >= cutoff)
        .order_by(weather_observed.c.time)
    )
    with engine.connect() as conn:
        return pd.read_sql_query(stmt, conn)


def get_valve_states(latest: pd.DataFrame | None = None) -> pd.DataFrame:
    """Dernier état connu de chaque vanne (toutes appareils confondus),
    extrait de `latest` (get_latest_readings()) s'il est déjà chargé."""
    if latest is None:
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
    fois (voir prefer_finest_source). Renvoie None si aucune prévision n'est
    disponible (service météo pas encore alimenté).
    """
    forecast = get_weather_forecast()
    precip = prefer_finest_source(forecast[forecast["metric"] == "precipitation_mm"])
    if precip.empty:
        return None

    now = datetime.now(timezone.utc)

    def sum_window(hours):
        window = precip[(precip["valid_time"] >= now) & (precip["valid_time"] <= now + timedelta(hours=hours))]
        return float(window["value"].sum())

    return {"rain_3h_mm": sum_window(3), "rain_48h_mm": sum_window(48)}


def get_known_valves() -> list[dict]:
    """Toutes les vannes déjà vues au moins une fois (device_id, metric),
    depuis le journal permanent valve_events : une vanne inutilisée depuis
    plus longtemps que la conservation du brut reste connue."""
    stmt = (
        select(valve_events.c.device_id, valve_events.c.metric)
        .distinct()
        .order_by(valve_events.c.device_id, valve_events.c.metric)
    )
    with engine.connect() as conn:
        return pd.read_sql_query(stmt, conn).to_dict("records")


def get_soil_sensors() -> list[dict]:
    """Appareils ayant déjà publié une humidité du sol, avec leur nom :
    [{"device_id": ..., "name": ...}], triés par nom. Depuis l'agrégat
    journalier (jamais purgé, une ligne par jour) plutôt que le brut : plus
    léger, et un capteur muet depuis plus de 15 jours reste proposé."""
    d = sensor_readings_daily
    stmt = (
        select(d.c.device_id, func.coalesce(devices.c.name, d.c.device_id).label("name"))
        .select_from(d.outerjoin(devices, devices.c.device_id == d.c.device_id))
        .where(d.c.metric == config.SOIL_MOISTURE_METRIC)
        .distinct()
    )
    with engine.connect() as conn:
        rows = pd.read_sql_query(stmt, conn).to_dict("records")
    return sorted(rows, key=lambda r: (r["name"], r["device_id"]))


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


# --- Potager : zones et profils de culture -------------------------------------

def alpha_key(text: str) -> str:
    """Tri alphabétique français : "Épinard" avec les E, pas après le Z
    (la base trie octet par octet)."""
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()


def get_crop_profiles() -> list[dict]:
    with engine.connect() as conn:
        rows = conn.execute(select(crop_profiles)).mappings().all()
    return sorted((dict(row) for row in rows), key=lambda profile: alpha_key(profile["name"]))


def get_zones() -> list[dict]:
    """Zones triées par nom, chacune avec "sensors" (liste de device_id) et
    "crop" (profil de culture complet, ou None)."""
    with engine.connect() as conn:
        zones = [dict(row) for row in conn.execute(select(garden_zones).order_by(garden_zones.c.name)).mappings()]
        sensor_rows = conn.execute(
            select(garden_zone_sensors.c.zone_id, garden_zone_sensors.c.device_id).order_by(
                garden_zone_sensors.c.device_id
            )
        ).all()
    profiles = {profile["id"]: profile for profile in get_crop_profiles()}
    sensors_by_zone = {}
    for zone_id, device_id in sensor_rows:
        sensors_by_zone.setdefault(zone_id, []).append(device_id)
    for zone in zones:
        zone["sensors"] = sensors_by_zone.get(zone["id"], [])
        zone["crop"] = profiles.get(zone["crop_profile_id"])
    return zones


def get_zone(zone_id: int) -> dict | None:
    return next((zone for zone in get_zones() if zone["id"] == zone_id), None)


def save_zone(zone_id, name, valve, sensors, crop_profile_id, moisture_dry_ref, moisture_wet_ref, notes) -> int:
    """Crée la zone si zone_id est None, sinon la met à jour. valve :
    (device_id, metric) ou None. Lève sqlalchemy.exc.IntegrityError si la
    vanne arrose déjà une autre zone."""
    valve_device_id, valve_metric = valve if valve else (None, None)
    values = dict(
        name=name,
        valve_device_id=valve_device_id,
        valve_metric=valve_metric,
        crop_profile_id=crop_profile_id,
        moisture_dry_ref=moisture_dry_ref,
        moisture_wet_ref=moisture_wet_ref,
        notes=notes,
    )
    with engine.begin() as conn:
        if zone_id is None:
            zone_id = conn.execute(
                garden_zones.insert().values(**values).returning(garden_zones.c.id)
            ).scalar_one()
        else:
            conn.execute(garden_zones.update().where(garden_zones.c.id == zone_id).values(**values))
            conn.execute(garden_zone_sensors.delete().where(garden_zone_sensors.c.zone_id == zone_id))
        if sensors:
            conn.execute(garden_zone_sensors.insert(), [{"zone_id": zone_id, "device_id": d} for d in sensors])
    return zone_id


def delete_zone(zone_id: int) -> None:
    with engine.begin() as conn:
        conn.execute(garden_zones.delete().where(garden_zones.c.id == zone_id))
