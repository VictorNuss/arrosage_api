import json
import logging
import queue
import threading
import time

import paho.mqtt.client as mqtt

from . import config, db
from .mqtt_client import parse_ip_payload, parse_payload, parse_topic

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("ingest.main")

FLUSH_INTERVAL_SECONDS = 2.0
_pending_rows = queue.Queue()
_pending_ip_updates = queue.Queue()


def _request_resync(client, engine):
    """Demande à chaque device déjà connu de republier son état complet
    connu (vannes + dernière valeur de chaque capteur déjà lu au moins une
    fois) : filet de sécurité en plus des messages retenus par le broker,
    utile si le broker a perdu ses messages retenus (volume réinitialisé...).
    """
    try:
        device_ids = db.get_known_device_ids(engine)
    except Exception:
        log.exception("Impossible de lister les devices connus pour la resynchronisation")
        return
    for device_id in device_ids:
        client.publish(f"arrosage/{device_id}/commande", json.dumps({"action": "get_status"}), qos=1, retain=False)
    if device_ids:
        log.info("Resynchronisation (get_status) demandée à %s device(s)", len(device_ids))


def _on_connect(client, userdata, flags, reason_code, properties=None):
    log.info("Connecté au broker MQTT, abonnement à %s", config.MQTT_TOPIC)
    client.subscribe(config.MQTT_TOPIC, qos=1)
    _request_resync(client, userdata)


def _on_message(client, userdata, message):
    parsed_topic = parse_topic(message.topic)
    if parsed_topic is None:
        log.warning("Topic inattendu ignoré: %s", message.topic)
        return
    device_id, metric = parsed_topic

    if metric == config.IP_METRIC_KEY:
        try:
            ip_address = parse_ip_payload(message.payload)
        except Exception:
            log.exception("Payload IP invalide sur %s, message ignoré", message.topic)
            return
        _pending_ip_updates.put((device_id, ip_address))
        return

    try:
        rows = parse_payload(device_id, metric, message.payload)
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

        ip_updates = {}
        try:
            while True:
                device_id, ip_address = _pending_ip_updates.get_nowait()
                ip_updates[device_id] = ip_address  # ne garde que la plus récente du lot
        except queue.Empty:
            pass

        if not batch and not ip_updates:
            continue

        try:
            # Toujours upsert (idempotent) plutôt que de faire confiance à un
            # cache mémoire : si la ligne "devices" disparaît sans que ce
            # process ne redémarre (nettoyage manuel, restauration DB...), un
            # cache aurait fait échouer l'insert suivant sur une violation de
            # clé étrangère.
            for device_id in devices_in_batch | set(ip_updates):
                db.ensure_device(engine, device_id)
            if batch:
                db.insert_readings(engine, batch)
                log.info("Insertion de %s mesures (%s appareils)", len(batch), len(devices_in_batch))
            for device_id, ip_address in ip_updates.items():
                db.update_device_ip(engine, device_id, ip_address)
            if ip_updates:
                log.info("IP mise à jour pour %s device(s)", len(ip_updates))
        except Exception:
            log.exception("Échec de l'insertion d'un lot (%s mesures, %s IP), lot perdu", len(batch), len(ip_updates))


def main():
    engine = db.create_engine_with_retry()

    flush_thread = threading.Thread(target=_flush_loop, args=(engine,), daemon=True)
    flush_thread.start()

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, userdata=engine)
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
