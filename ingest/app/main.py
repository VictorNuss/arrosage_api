import json
import logging
import queue
import threading
import time

import paho.mqtt.client as mqtt

from . import config, db, tank, zigbee
from .mqtt_client import is_device_info_key, parse_device_info, parse_payload, parse_topic

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("ingest.main")

FLUSH_INTERVAL_SECONDS = 2.0
_pending_rows = queue.Queue()
_pending_device_info = queue.Queue()


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
    if config.ZIGBEE2MQTT_BASE_TOPIC:
        zigbee_topic = f"{config.ZIGBEE2MQTT_BASE_TOPIC}/+"
        log.info("Abonnement aux capteurs Zigbee2MQTT sur %s", zigbee_topic)
        client.subscribe(zigbee_topic, qos=1)
    _request_resync(client, userdata)


def _on_zigbee_message(client, device_id, message):
    """Republie l'état d'un capteur Zigbee dans le contrat device : il est
    stocké quand ingest reçoit ces messages republiés, pas ici."""
    try:
        readings = zigbee.translate_payload(message.payload)
    except Exception:
        log.exception("Payload Zigbee2MQTT invalide sur %s, message ignoré", message.topic)
        return
    if readings:
        zigbee.republish(client, device_id, readings)


def _on_message(client, userdata, message):
    zigbee_device_id = zigbee.parse_topic(message.topic)
    if zigbee_device_id is not None:
        _on_zigbee_message(client, zigbee_device_id, message)
        return

    parsed_topic = parse_topic(message.topic)
    if parsed_topic is None:
        log.warning("Topic inattendu ignoré: %s", message.topic)
        return
    device_id, metric = parsed_topic

    if is_device_info_key(metric):
        try:
            column, value = parse_device_info(metric, message.payload)
        except Exception:
            log.exception("Payload d'info device invalide sur %s, message ignoré", message.topic)
            return
        _pending_device_info.put((device_id, column, value))
        return

    try:
        rows = parse_payload(device_id, metric, message.payload)
    except Exception:
        log.exception("Payload invalide sur %s, message ignoré", message.topic)
        return
    if not rows:
        return
    _pending_rows.put((device_id, rows))

    if metric == config.TANK_DISTANCE_METRIC:
        level = tank.water_level_cm(rows[0]["value"])
        if level is not None:
            tank.republish(client, device_id, level)


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

        info_updates = {}
        try:
            while True:
                device_id, column, value = _pending_device_info.get_nowait()
                # Ne garde que la valeur la plus récente du lot pour chaque colonne.
                info_updates.setdefault(device_id, {})[column] = value
        except queue.Empty:
            pass

        if not batch and not info_updates:
            continue

        try:
            # Toujours upsert (idempotent) plutôt que de faire confiance à un
            # cache mémoire : si la ligne "devices" disparaît sans que ce
            # process ne redémarre (nettoyage manuel, restauration DB...), un
            # cache aurait fait échouer l'insert suivant sur une violation de
            # clé étrangère.
            for device_id in devices_in_batch | set(info_updates):
                db.ensure_device(engine, device_id)
            if batch:
                db.insert_readings(engine, batch)
                log.info("Insertion de %s mesures (%s appareils)", len(batch), len(devices_in_batch))
            for device_id, fields in info_updates.items():
                db.update_device_info(engine, device_id, fields)
            if info_updates:
                log.info("Infos (IP/firmware) mises à jour pour %s device(s)", len(info_updates))
        except Exception:
            log.exception(
                "Échec de l'insertion d'un lot (%s mesures, %s infos device), lot perdu", len(batch), len(info_updates)
            )


def main():
    if tank.tank_capacity_cm() is None:
        log.error(
            "TANK_DISTANCE_FULL_CM (%s) doit être inférieure à TANK_DISTANCE_EMPTY_CM (%s) : "
            "niveau de cuve non calculé, seule la distance brute est stockée",
            config.TANK_DISTANCE_FULL_CM,
            config.TANK_DISTANCE_EMPTY_CM,
        )
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
