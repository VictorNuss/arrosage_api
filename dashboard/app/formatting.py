from datetime import datetime, timedelta, timezone

import pandas as pd


def format_last_seen(ts) -> str:
    """Durée écoulée depuis `ts` (Timestamp/datetime aware, ou NaT/None),
    formulée pour suivre "Vu ..." : "à l'instant", "il y a 5 min"..."""
    if ts is None or pd.isna(ts):
        return "jamais"
    delta = datetime.now(timezone.utc) - pd.Timestamp(ts).to_pydatetime()
    if delta < timedelta(minutes=1):
        return "à l'instant"
    if delta < timedelta(hours=1):
        return f"il y a {int(delta.total_seconds() // 60)} min"
    if delta < timedelta(days=1):
        return f"il y a {int(delta.total_seconds() // 3600)} h"
    return f"il y a {delta.days} j"


# Libellé lisible et icône (Bootstrap Icons) des métriques connues du
# contrat device ; toute autre clé est affichée telle quelle.
_METRIC_DISPLAY = {
    "water_level_cm": ("Niveau d'eau", "bi-water"),
    "temperature_c": ("Température", "bi-thermometer-half"),
    "humidity_pct": ("Humidité du sol", "bi-moisture"),
    "battery_v": ("Alimentation", "bi-battery-half"),
    # Capteurs de sol Zigbee (republiés par ingest, voir ingest/app/zigbee.py).
    "air_humidity_pct_rh": ("Humidité de l'air", "bi-cloud"),
    "illuminance_lux": ("Luminosité", "bi-sun"),
    "fertility_us_cm": ("Fertilité (conductivité)", "bi-flower1"),
    "battery_pct": ("Batterie", "bi-battery-half"),
    "zigbee_lqi": ("Signal Zigbee", "bi-reception-4"),
}
# Mesures sans intérêt après la virgule (lux, µS/cm, %, qualité de lien).
_INTEGER_METRICS = {"illuminance_lux", "fertility_us_cm", "battery_pct", "zigbee_lqi"}
_VALVE_PREFIX = "vanne_"


def metric_label(metric: str) -> str:
    """Ex : "temperature_c" -> "Température", "vanne_2" -> "Vanne 2"."""
    if metric in _METRIC_DISPLAY:
        return _METRIC_DISPLAY[metric][0]
    if metric.startswith(_VALVE_PREFIX) and metric[len(_VALVE_PREFIX):]:
        return f"Vanne {metric[len(_VALVE_PREFIX):].replace('_', ' ')}"
    return metric


def metric_icon(metric: str) -> str:
    if metric in _METRIC_DISPLAY:
        return _METRIC_DISPLAY[metric][1]
    if metric.startswith(_VALVE_PREFIX):
        return "bi-droplet"
    return "bi-activity"


def format_metric_value(metric: str, value, unit=None) -> str:
    """Ex : ("temperature_c", 18.34, "°C") -> "18.3 °C",
    ("illuminance_lux", 1200.0, "lx") -> "1200 lx"."""
    number = f"{value:.0f}" if metric in _INTEGER_METRICS else f"{value:.1f}"
    return f"{number} {unit}" if unit else number
