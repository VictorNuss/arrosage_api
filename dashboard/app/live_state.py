"""Cache mémoire des derniers messages d'état MQTT
(arrosage/<device_id>/etat/<key>), mis à jour instantanément à la réception
plutôt que d'attendre le prochain cycle d'écriture en base par le service
ingest (~2s de batching) puis le prochain poll du dashboard.

Contrat (un topic par mesure/vanne, voir esp32/README.md) :
  - capteur : {"value": 34.5}
  - vanne   : {"state": "open"} / {"state": "closed"}
Pas de champ "ts" : l'horodatage de réception MQTT fait foi. L'absence d'un
message pour une clé ne veut pas dire "capteur en panne", juste "rien de
neuf depuis la dernière valeur connue" (le broker republie la dernière
valeur retenue à la (re)connexion).

Le dashboard s'abonne au broker en parallèle de `ingest` : ce sont deux
abonnés indépendants du même topic, l'un n'affecte pas l'autre. Ce cache ne
remplace pas la base (utilisée pour l'historique, les devices connus, etc.),
il vient juste combler le délai pour l'état "instantané" (vannes, dernières
valeurs) — voir `queries.get_latest_readings()` qui fusionne les deux
sources.
"""

import json
import logging
import threading
import time
from datetime import datetime, timezone

import paho.mqtt.client as mqtt

from . import config

log = logging.getLogger("dashboard.live_state")

TOPIC = "arrosage/+/etat/#"
VALVE_METRIC_HINT = "vanne"

_TRUTHY = {"open", "on", "true", "1", "ouvert", "ouverte"}
_FALSY = {"closed", "off", "false", "0", "ferme", "fermee", "fermé", "fermée"}

_UNIT_SUFFIXES = {
    "_cm": "cm",
    "_mm": "mm",
    "_pct": "%",
    "_c": "°C",
    "_v": "V",
    "_pct_rh": "%RH",
}

_lock = threading.Lock()
_latest: dict[tuple[str, str], dict] = {}  # (device_id, metric) -> {value, unit, time}

_client = None
_start_lock = threading.Lock()


def _is_valve_metric(metric):
    return VALVE_METRIC_HINT in metric.lower()


def _infer_unit(metric):
    for suffix, unit in _UNIT_SUFFIXES.items():
        if metric.endswith(suffix):
            return unit
    return None


def _coerce_valve_state(raw_value):
    if isinstance(raw_value, bool):
        return 1.0 if raw_value else 0.0
    if isinstance(raw_value, str):
        normalized = raw_value.strip().lower()
        if normalized in _TRUTHY:
            return 1.0
        if normalized in _FALSY:
            return 0.0
    raise ValueError(f"état de vanne non reconnu: {raw_value!r}")


def _coerce_sensor_value(raw_value):
    if isinstance(raw_value, bool):
        raise ValueError(f"valeur non numérique: {raw_value!r}")
    if isinstance(raw_value, (int, float)):
        return float(raw_value)
    raise ValueError(f"valeur non numérique: {raw_value!r}")


def _parse_topic(topic):
    """arrosage/<device_id>/etat/<key> -> (device_id, key), ou None."""
    parts = topic.split("/")
    if len(parts) != 4 or parts[0] != "arrosage" or parts[2] != "etat":
        return None
    return parts[1], parts[3]


def _on_connect(client, userdata, flags, reason_code, properties=None):
    log.info("Cache live: connecté au broker MQTT, abonnement à %s", TOPIC)
    client.subscribe(TOPIC, qos=1)


def _on_message(client, userdata, message):
    parsed = _parse_topic(message.topic)
    if parsed is None:
        return
    device_id, metric = parsed

    try:
        data = json.loads(message.payload.decode("utf-8"))
    except Exception:
        log.warning("Cache live: payload illisible sur %s", message.topic)
        return
    if not isinstance(data, dict):
        return

    try:
        if _is_valve_metric(metric):
            if "state" not in data:
                raise ValueError("champ 'state' manquant")
            value = _coerce_valve_state(data["state"])
        else:
            if "value" not in data:
                raise ValueError("champ 'value' manquant")
            value = _coerce_sensor_value(data["value"])
    except Exception as exc:
        log.warning("Cache live: payload invalide sur %s (%s)", message.topic, exc)
        return

    with _lock:
        _latest[(device_id, metric)] = {
            "value": value,
            "unit": _infer_unit(metric),
            "time": datetime.now(timezone.utc),
        }
    log.info("Cache live: mis à jour %s/%s", device_id, metric)


def get_latest_readings() -> list[dict]:
    """Snapshot des dernières valeurs reçues depuis le démarrage du
    dashboard, au format proche de queries.get_latest_readings()."""
    with _lock:
        return [
            {"device_id": device_id, "metric": metric, **entry}
            for (device_id, metric), entry in _latest.items()
        ]


def _connect_with_retry(client, max_attempts=30, delay_seconds=2):
    attempt = 0
    while True:
        attempt += 1
        try:
            client.connect(config.MQTT_HOST, config.MQTT_PORT)
            return True
        except Exception as exc:
            if attempt >= max_attempts:
                log.exception("Cache live: impossible de joindre le broker MQTT")
                return False
            log.warning("Cache live: broker MQTT indisponible (tentative %s/%s): %s", attempt, max_attempts, exc)
            time.sleep(delay_seconds)


def _run(client):
    if _connect_with_retry(client):
        client.loop_forever()


def start():
    """Démarre l'abonnement en tâche de fond. Sans effet si déjà démarré."""
    global _client
    with _start_lock:
        if _client is not None:
            return
        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
        if config.MQTT_USERNAME:
            client.username_pw_set(config.MQTT_USERNAME, config.MQTT_PASSWORD)
        client.on_connect = _on_connect
        client.on_message = _on_message
        _client = client
        threading.Thread(target=_run, args=(client,), daemon=True).start()
