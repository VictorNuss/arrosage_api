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
}
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
