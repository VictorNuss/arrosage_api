"""Publication de commandes d'ouverture/fermeture de vannes.

Contrat (voir esp32/README.md, firmware réel dans le repo arrosage_fw) :
topic `arrosage/<device_id>/commande`, QoS 1, jamais retain :

    {"vanne": "vanne_1", "action": "open", "duration_s": 600}
    {"vanne": "vanne_2", "action": "close"}
    {"action": "stop_all"}

C'est le firmware qui gère localement le minuteur d'auto-fermeture (robuste
à une coupure réseau/dashboard). Comme la commande n'est pas retenue, un
redémarrage du device ne rejoue rien : pas de risque de commande périmée à
gérer côté firmware.
"""

import json
import logging
import threading

import paho.mqtt.client as mqtt

from . import config

log = logging.getLogger("dashboard.mqtt_control")

_client = None
_lock = threading.Lock()


def _get_client():
    global _client
    with _lock:
        if _client is not None:
            return _client
        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
        if config.MQTT_USERNAME:
            client.username_pw_set(config.MQTT_USERNAME, config.MQTT_PASSWORD)
        client.connect(config.MQTT_HOST, config.MQTT_PORT)
        client.loop_start()
        _client = client
        return _client


def _publish(device_id: str, payload: dict) -> bool:
    topic = f"arrosage/{device_id}/commande"
    try:
        client = _get_client()
        result = client.publish(topic, json.dumps(payload), qos=1, retain=False)
        result.wait_for_publish(timeout=5)
    except Exception:
        log.exception("Échec de l'envoi de la commande sur %s", topic)
        return False

    log.info("Commande envoyée sur %s: %s", topic, payload)
    return True


def send_valve_command(device_id: str, metric: str, action: str, duration_s: int | None = None) -> bool:
    """action: 'open' ou 'close'. Renvoie False si la publication a échoué
    (ex: broker MQTT injoignable) plutôt que de lever une exception jusqu'au
    callback Dash."""
    payload = {"vanne": metric, "action": action}
    if action == "open" and duration_s:
        payload["duration_s"] = duration_s
    return _publish(device_id, payload)


def send_stop_all(device_id: str) -> bool:
    return _publish(device_id, {"action": "stop_all"})
