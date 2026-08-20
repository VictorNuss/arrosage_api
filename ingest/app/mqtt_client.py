import json
import logging
import re
from datetime import datetime, timezone

from . import config

log = logging.getLogger("ingest.parser")

_IPV4_RE = re.compile(r"^(\d{1,3}\.){3}\d{1,3}$")

_UNIT_SUFFIXES = {
    "_cm": "cm",
    "_mm": "mm",
    "_pct": "%",
    "_c": "°C",
    "_v": "V",
    "_pct_rh": "%RH",
}


def is_valve_metric(metric):
    return config.VALVE_METRIC_HINT in metric.lower()


def _infer_unit(metric):
    for suffix, unit in _UNIT_SUFFIXES.items():
        if metric.endswith(suffix):
            return unit
    return None


def _coerce_valve_state(raw_value):
    if isinstance(raw_value, bool):
        return config.VALVE_OPEN_VALUE if raw_value else config.VALVE_CLOSED_VALUE
    if isinstance(raw_value, str):
        normalized = raw_value.strip().lower()
        if normalized in config.TRUTHY_STRINGS:
            return config.VALVE_OPEN_VALUE
        if normalized in config.FALSY_STRINGS:
            return config.VALVE_CLOSED_VALUE
        if normalized in config.TRANSITION_STRINGS:
            return config.VALVE_TRANSITION_VALUE
    raise ValueError(f"état de vanne non reconnu: {raw_value!r}")


def is_valid_ipv4(value) -> bool:
    if not isinstance(value, str) or not _IPV4_RE.match(value):
        return False
    return all(0 <= int(octet) <= 255 for octet in value.split("."))


def parse_ip_payload(payload_bytes) -> str:
    """Clé 'ip' (arrosage/<device_id>/etat/ip) : {"value": "192.168.1.50"}.

    Contrairement aux autres clés, la valeur est une chaîne (adresse IPv4
    fixe du device), pas un nombre : elle alimente devices.ip_address, pas
    sensor_readings (colonne float non nullable).
    """
    data = json.loads(payload_bytes.decode("utf-8"))
    if not isinstance(data, dict) or "value" not in data:
        raise ValueError("champ 'value' manquant pour la clé 'ip'")
    raw_value = data["value"]
    if not is_valid_ipv4(raw_value):
        raise ValueError(f"adresse IP invalide: {raw_value!r}")
    return raw_value


def parse_topic(topic):
    """arrosage/<device_id>/etat/<key> -> (device_id, key), ou None si le
    topic ne correspond pas (un seul niveau après "etat" est attendu)."""
    parts = topic.split("/")
    if len(parts) != 4 or parts[0] != "arrosage" or parts[2] != "etat":
        return None
    return parts[1], parts[3]


def parse_payload(device_id, metric, payload_bytes):
    """Un message = une seule métrique. Renvoie une liste de 0 ou 1 dict
    {time, device_id, metric, value, unit} (liste pour rester compatible
    avec le traitement par lot de main.py).

    Payload attendu : {"value": 34.5} pour un capteur, {"state": "open"}
    pour une vanne. Pas de champ "ts" dans ce contrat : l'horodatage de
    réception MQTT fait foi.
    """
    data = json.loads(payload_bytes.decode("utf-8"))
    if not isinstance(data, dict):
        raise ValueError("le payload JSON doit être un objet")

    if is_valve_metric(metric):
        if "state" not in data:
            raise ValueError(f"champ 'state' manquant pour la vanne '{metric}'")
        value = _coerce_valve_state(data["state"])
    else:
        if "value" not in data:
            raise ValueError(f"champ 'value' manquant pour le capteur '{metric}'")
        raw_value = data["value"]
        if not isinstance(raw_value, (int, float)) or isinstance(raw_value, bool):
            raise ValueError(f"valeur non numérique pour '{metric}': {raw_value!r}")
        value = float(raw_value)

    return [
        {
            "time": datetime.now(timezone.utc),
            "device_id": device_id,
            "metric": metric,
            "value": value,
            "unit": _infer_unit(metric),
        }
    ]
