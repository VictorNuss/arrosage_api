"""Cache mémoire des derniers messages d'état MQTT (arrosage/<device_id>/etat),
mis à jour instantanément à la réception plutôt que d'attendre le prochain
cycle d'écriture en base par le service ingest (~2s de batching) puis le
prochain poll du dashboard (~15s auparavant).

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

TOPIC = "arrosage/+/etat"

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


def _infer_unit(metric):
    for suffix, unit in _UNIT_SUFFIXES.items():
        if metric.endswith(suffix):
            return unit
    return None


def _coerce_value(raw_value):
    if isinstance(raw_value, bool):
        return 1.0 if raw_value else 0.0
    if isinstance(raw_value, (int, float)):
        return float(raw_value)
    if isinstance(raw_value, str):
        normalized = raw_value.strip().lower()
        if normalized in _TRUTHY:
            return 1.0
        if normalized in _FALSY:
            return 0.0
        return float(normalized)
    raise ValueError(f"type non supporté: {type(raw_value)}")


def _parse_topic(topic):
    parts = topic.split("/")
    if len(parts) != 3 or parts[0] != "arrosage" or parts[2] != "etat":
        return None
    return parts[1]


def _on_connect(client, userdata, flags, reason_code, properties=None):
    log.info("Cache live: connecté au broker MQTT, abonnement à %s", TOPIC)
    client.subscribe(TOPIC, qos=1)


def _on_message(client, userdata, message):
    device_id = _parse_topic(message.topic)
    if device_id is None:
        return
    try:
        data = json.loads(message.payload.decode("utf-8"))
    except Exception:
        log.warning("Cache live: payload illisible sur %s", message.topic)
        return
    if not isinstance(data, dict):
        return

    now = datetime.now(timezone.utc)
    with _lock:
        for metric, raw_value in data.items():
            if metric == "ts":
                continue
            try:
                value = _coerce_value(raw_value)
            except Exception:
                continue
            _latest[(device_id, metric)] = {
                "value": value,
                "unit": _infer_unit(metric),
                "time": now,
            }


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
