import json
import logging
from datetime import datetime, timezone

from . import config

log = logging.getLogger("ingest.parser")

_UNIT_SUFFIXES = {
    "_cm": "cm",
    "_mm": "mm",
    "_pct": "%",
    "_c": "°C",
    "_v": "V",
    "_pct_rh": "%RH",
}


def _infer_unit(metric):
    for suffix, unit in _UNIT_SUFFIXES.items():
        if metric.endswith(suffix):
            return unit
    return None


def _coerce_value(metric, raw_value):
    """Renvoie un float, en normalisant les états de vanne textuels/booléens en 0/1."""
    if isinstance(raw_value, bool):
        return 1.0 if raw_value else 0.0
    if isinstance(raw_value, (int, float)):
        return float(raw_value)
    if isinstance(raw_value, str):
        normalized = raw_value.strip().lower()
        if normalized in config.TRUTHY_STRINGS:
            return 1.0
        if normalized in config.FALSY_STRINGS:
            return 0.0
        try:
            return float(normalized)
        except ValueError:
            raise ValueError(f"valeur non numérique pour la métrique '{metric}': {raw_value!r}")
    raise ValueError(f"type de valeur non supporté pour la métrique '{metric}': {type(raw_value)}")


def parse_topic(topic):
    """arrosage/<device_id>/etat -> device_id (ou None si le topic ne correspond pas)."""
    parts = topic.split("/")
    if len(parts) != 3 or parts[0] != "arrosage" or parts[2] != "etat":
        return None
    return parts[1]


def parse_payload(device_id, payload_bytes):
    """Retourne une liste de dicts {time, device_id, metric, value, unit}."""
    data = json.loads(payload_bytes.decode("utf-8"))
    if not isinstance(data, dict):
        raise ValueError("le payload JSON doit être un objet")

    ts_raw = data.get("ts")
    if ts_raw:
        reading_time = datetime.fromisoformat(ts_raw.replace("Z", "+00:00"))
    else:
        reading_time = datetime.now(timezone.utc)

    rows = []
    for metric, raw_value in data.items():
        if metric in config.NON_METRIC_FIELDS:
            continue
        try:
            value = _coerce_value(metric, raw_value)
        except ValueError as exc:
            log.warning("Ignoré: %s (device=%s)", exc, device_id)
            continue
        rows.append(
            {
                "time": reading_time,
                "device_id": device_id,
                "metric": metric,
                "value": value,
                "unit": _infer_unit(metric),
            }
        )
    return rows
