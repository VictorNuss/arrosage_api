"""Logique du potager (sans Dash) : humidité calibrée d'une zone, statut par
rapport à sa culture, ensoleillement du jour et alertes.

Une zone = une vanne + un ou plusieurs capteurs de sol + un profil de
culture (voir db/init/003_garden_zones.sql). Les seuils ci-dessous sont des
ordres de grandeur pour un potager amateur, pas des valeurs agronomiques
précises.
"""

from datetime import datetime, timedelta, timezone

import pandas as pd

from . import config

MOISTURE = config.SOIL_MOISTURE_METRIC  # "humidity_pct", ESP32 comme Zigbee
TEMPERATURE = "temperature_c"
AIR_HUMIDITY = "air_humidity_pct_rh"
ILLUMINANCE = "illuminance_lux"
FERTILITY = "fertility_us_cm"
BATTERY = config.SOIL_BATTERY_METRIC

# Plein soleil : quelques dizaines de milliers de lux (ciel couvert ~10 000,
# soleil direct 50 000 à 100 000).
SUN_LUX_THRESHOLD = 20_000

FROST_ALERT_C = 3
HEAT_ALERT_C = 32
# Mildiou : humidité de l'air élevée et températures douces pendant
# plusieurs heures (règle simplifiée, indicative).
MILDEW_WINDOW = timedelta(hours=6)
MILDEW_MIN_AIR_HUMIDITY = 85
MILDEW_TEMP_RANGE = (10, 25)

STATUS_LABELS = {
    "dry": "Trop sec : arrosage conseillé",
    "ok": "Dans la cible",
    "wet": "Trop humide pour cette culture",
    "unknown": "Pas de mesure d'humidité",
}


def calibrated_moisture(raw, dry_ref, wet_ref):
    """Valeur brute du capteur ramenée à l'échelle de la zone : 0 = sol sec
    au toucher, 100 = sol gorgé d'eau. Sans calibration, la valeur brute."""
    if raw is None or pd.isna(raw):
        return None
    if dry_ref is None or wet_ref is None or pd.isna(dry_ref) or pd.isna(wet_ref) or wet_ref <= dry_ref:
        return float(raw)
    return min(100.0, max(0.0, (raw - dry_ref) / (wet_ref - dry_ref) * 100))


def zone_moisture(zone, latest_by_sensor):
    """Humidité calibrée moyenne des capteurs de la zone (None si aucun n'a
    de mesure). latest_by_sensor : {device_id: {metric: value}}."""
    values = [
        calibrated_moisture(latest_by_sensor.get(device_id, {}).get(MOISTURE),
                            zone.get("moisture_dry_ref"), zone.get("moisture_wet_ref"))
        for device_id in zone["sensors"]
    ]
    values = [v for v in values if v is not None]
    return sum(values) / len(values) if values else None


def raw_moisture(sensors, latest_by_sensor):
    """Moyenne des valeurs brutes (pour la calibration)."""
    values = [latest_by_sensor.get(d, {}).get(MOISTURE) for d in sensors]
    values = [v for v in values if v is not None and not pd.isna(v)]
    return round(sum(values) / len(values), 1) if values else None


def moisture_status(moisture, crop):
    if moisture is None:
        return "unknown"
    if crop is None:
        return "ok"
    if moisture < crop["moisture_min"]:
        return "dry"
    if moisture > crop["moisture_max"]:
        return "wet"
    return "ok"


def zone_metric(zone, latest_by_sensor, metric):
    """Moyenne d'une métrique sur les capteurs de la zone, ou None."""
    values = [latest_by_sensor.get(d, {}).get(metric) for d in zone["sensors"]]
    values = [v for v in values if v is not None and not pd.isna(v)]
    return sum(values) / len(values) if values else None


def sun_hours(lux_history: pd.DataFrame) -> float | None:
    """Heures de plein soleil depuis minuit, d'après l'agrégat 1 minute de
    la luminosité (une ligne = une minute ; plusieurs capteurs moyennés)."""
    if lux_history.empty:
        return None
    per_minute = lux_history.groupby("time")["value"].mean()
    return float((per_minute >= SUN_LUX_THRESHOLD).sum()) / 60


def mildew_risk(recent: pd.DataFrame, now=None) -> bool:
    """recent : historique (time, metric, value) des capteurs de la zone."""
    now = now or datetime.now(timezone.utc)
    window = recent[pd.to_datetime(recent["time"], utc=True) >= now - MILDEW_WINDOW] if not recent.empty else recent
    if window.empty:
        return False
    air = window[window["metric"] == AIR_HUMIDITY]["value"]
    temp = window[window["metric"] == TEMPERATURE]["value"]
    if air.empty or temp.empty:
        return False
    low, high = MILDEW_TEMP_RANGE
    return bool(air.mean() >= MILDEW_MIN_AIR_HUMIDITY and low <= temp.mean() <= high)


def zone_alerts(zone, latest_by_sensor, sensor_times, recent, now=None, device_names=None):
    """Liste de (couleur Bootstrap, message), la plus importante d'abord."""
    now = now or datetime.now(timezone.utc)
    device_names = device_names or {}
    crop = zone.get("crop")
    alerts = []

    if not zone["sensors"]:
        alerts.append(("secondary", "Aucun capteur de sol associé à cette zone."))
    temperature = zone_metric(zone, latest_by_sensor, TEMPERATURE)
    if temperature is not None and temperature <= FROST_ALERT_C and (crop is None or crop["frost_sensitive"]):
        alerts.append(("danger", f"Risque de gel ({temperature:.0f} °C) : protéger les plants (voile d'hivernage)."))
    if temperature is not None and temperature >= HEAT_ALERT_C:
        alerts.append(("warning", f"Forte chaleur ({temperature:.0f} °C) : surveiller l'humidité, arroser tôt le matin."))
    if crop is not None and crop["mildew_sensitive"] and mildew_risk(recent, now):
        alerts.append(("warning", "Conditions favorables au mildiou (air humide et doux) : "
                                  "arroser au pied, aérer, surveiller les feuilles."))

    for device_id in zone["sensors"]:
        name = device_names.get(device_id, device_id)
        battery = latest_by_sensor.get(device_id, {}).get(BATTERY)
        if battery is not None and battery < config.LOW_BATTERY_PCT:
            alerts.append(("warning", f"Pile faible sur {name} ({battery:.0f} %)."))
        last = sensor_times.get(device_id)
        if last is None or now - last > timedelta(hours=config.SOIL_STALE_HOURS):
            alerts.append(("secondary", f"Pas de mesure récente de {name}."))
    return alerts


def latest_by_device(readings: pd.DataFrame):
    """({device_id: {metric: value}}, {device_id: dernière mesure}) depuis
    queries.get_latest_readings() (la plus récente si doublon base/cache)."""
    if readings.empty:
        return {}, {}
    df = readings.assign(time=pd.to_datetime(readings["time"], utc=True)).sort_values("time")
    df = df.drop_duplicates(subset=["device_id", "metric"], keep="last")
    values, times = {}, {}
    for row in df.itertuples():
        values.setdefault(row.device_id, {})[row.metric] = row.value
        times[row.device_id] = max(times.get(row.device_id, row.time), row.time)
    return values, times
