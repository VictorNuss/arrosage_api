import logging
import queue
import threading
import time

import paho.mqtt.client as mqtt

from . import config, db
from .mqtt_client import parse_payload, parse_topic

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("ingest.main")

FLUSH_INTERVAL_SECONDS = 2.0
_pending_rows = queue.Queue()


def _on_connect(client, userdata, flags, reason_code, properties=None):
    log.info("Connecté au broker MQTT, abonnement à %s", config.MQTT_TOPIC)
    client.subscribe(config.MQTT_TOPIC, qos=1)


def _on_message(client, userdata, message):
    device_id = parse_topic(message.topic)
    if device_id is None:
        log.warning("Topic inattendu ignoré: %s", message.topic)
        return
    try:
        rows = parse_payload(device_id, message.payload)
    except Exception:
        log.exception("Payload invalide sur %s, message ignoré", message.topic)
        return
    if not rows:
        return
    _pending_rows.put((device_id, rows))


def _flush_loop(engine):
    while True:
        time.sleep(FLUSH_INTERVAL_SECONDS)
        batch = []
        devices_in_batch = set()
        try:
            while True:
                device_id, rows = _pending_rows.get_nowait()
                devices_in_batch.add(device_id)
                batch.extend(rows)
        except queue.Empty:
            pass

        if not batch:
            continue

        try:
            # Toujours upsert (idempotent) plutôt que de faire confiance à un
            # cache mémoire : si la ligne "devices" disparaît sans que ce
            # process ne redémarre (nettoyage manuel, restauration DB...), un
            # cache aurait fait échouer l'insert suivant sur une violation de
            # clé étrangère.
            for device_id in devices_in_batch:
                db.ensure_device(engine, device_id)
            db.insert_readings(engine, batch)
            log.info("Insertion de %s mesures (%s appareils)", len(batch), len(devices_in_batch))
        except Exception:
            log.exception("Échec de l'insertion d'un lot de %s mesures, lot perdu", len(batch))


def main():
    engine = db.create_engine_with_retry()

    flush_thread = threading.Thread(target=_flush_loop, args=(engine,), daemon=True)
    flush_thread.start()

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    if config.MQTT_USERNAME:
        client.username_pw_set(config.MQTT_USERNAME, config.MQTT_PASSWORD)
    client.on_connect = _on_connect
    client.on_message = _on_message

    while True:
        try:
            client.connect(config.MQTT_HOST, config.MQTT_PORT)
            client.loop_forever()
        except Exception:
            log.exception("Connexion MQTT perdue, nouvelle tentative dans 5s")
            time.sleep(5)


if __name__ == "__main__":
    main()
