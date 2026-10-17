"""Traduction des messages Zigbee2MQTT vers le contrat device du projet.

Zigbee2MQTT publie l'état complet d'un appareil dans un seul message JSON
sur <base_topic>/<friendly_name>, ex :

    zigbee2mqtt/sol-potager
    {"soil_moisture": 42, "temperature": 18.3, "illuminance": 1200,
     "soil_fertility": 350, "battery": 87, "linkquality": 110}

Plutôt que d'apprendre ce format à chaque abonné (ingest, cache live du
dashboard...), ingest le republie dans le contrat existant, un topic retenu
par mesure : arrosage/<friendly_name>/etat/<metric>, {"value": 42}. Ingest
reçoit ensuite ces messages par son abonnement habituel et les stocke comme
ceux d'un ESP32 ; le dashboard les voit en direct de la même façon.

Le friendly_name (renommable dans l'interface Zigbee2MQTT) devient le
device_id : un capteur = un device.
"""

import json
import logging

from . import config

log = logging.getLogger("ingest.zigbee")

# Champ Zigbee2MQTT -> métrique du contrat device. Seuls ces champs sont
# repris (battery_state, voltage, update... sont ignorés). Pour une même
# métrique, le premier champ présent l'emporte (ex : illuminance_lux,
# exposé par certaines versions, est préféré à illuminance).
#
# L'humidité du sol réutilise "humidity_pct", la clé déjà utilisée par les
# ESP32 pour le même usage : le dashboard et la condition de programme
# "sol déjà humide" traitent ainsi tous les capteurs de sol pareil.
FIELD_TO_METRIC = (
    ("soil_moisture", "humidity_pct"),
    ("temperature", "temperature_c"),
    ("humidity", "air_humidity_pct_rh"),
    ("illuminance_lux", "illuminance_lux"),
    ("illuminance", "illuminance_lux"),
    ("soil_fertility", "fertility_us_cm"),
    ("fertility", "fertility_us_cm"),
    ("soil_conductivity", "fertility_us_cm"),
    ("battery", "battery_pct"),
    ("linkquality", "zigbee_lqi"),
)

# Caractères interdits dans un niveau de topic MQTT publié.
_FORBIDDEN_NAME_CHARS = set("+#")


def parse_topic(topic):
    """<base>/<friendly_name> -> friendly_name, ou None.

    Les sous-topics (<base>/<nom>/availability, <base>/<nom>/set...) et ceux
    du pont lui-même (<base>/bridge/...) ne sont pas des états d'appareil.
    Un friendly_name contenant "/" (regroupement par dossier dans Zigbee2MQTT)
    n'est pas supporté : il donnerait un device_id sur plusieurs niveaux.
    """
    base = config.ZIGBEE2MQTT_BASE_TOPIC
    if not base:
        return None
    prefix = f"{base}/"
    if not topic.startswith(prefix):
        return None
    name = topic[len(prefix):]
    if not name or "/" in name or name == "bridge" or _FORBIDDEN_NAME_CHARS & set(name):
        return None
    return name


def translate_payload(payload_bytes):
    """Payload d'état Zigbee2MQTT -> liste de (metric, valeur float).

    Les champs absents, null ou non numériques sont ignorés (Zigbee2MQTT
    envoie null pour une mesure pas encore reçue). ValueError si le payload
    n'est pas un objet JSON.
    """
    data = json.loads(payload_bytes.decode("utf-8"))
    if not isinstance(data, dict):
        raise ValueError("le payload Zigbee2MQTT doit être un objet JSON")

    readings = {}
    for field, metric in FIELD_TO_METRIC:
        if metric in readings:
            continue
        raw_value = data.get(field)
        if isinstance(raw_value, bool) or not isinstance(raw_value, (int, float)):
            continue
        readings[metric] = float(raw_value)
    return list(readings.items())


def republish(client, device_id, readings):
    """Publie chaque mesure dans le contrat device (retenu, comme le
    firmware : le dashboard retrouve la dernière valeur à sa reconnexion)."""
    for metric, value in readings:
        client.publish(f"arrosage/{device_id}/etat/{metric}", json.dumps({"value": value}), qos=1, retain=True)
