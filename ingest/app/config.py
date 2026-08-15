import os

MQTT_HOST = os.environ.get("MQTT_HOST", "mosquitto")
MQTT_PORT = int(os.environ.get("MQTT_PORT", "1883"))
MQTT_USERNAME = os.environ.get("MQTT_USERNAME") or None
MQTT_PASSWORD = os.environ.get("MQTT_PASSWORD") or None
MQTT_TOPIC = "arrosage/+/etat/#"
VALVE_METRIC_HINT = "vanne"

POSTGRES_HOST = os.environ.get("POSTGRES_HOST", "timescaledb")
POSTGRES_PORT = int(os.environ.get("POSTGRES_PORT", "5432"))
POSTGRES_USER = os.environ.get("POSTGRES_USER", "arrosage")
POSTGRES_PASSWORD = os.environ.get("POSTGRES_PASSWORD", "")
POSTGRES_DB = os.environ.get("POSTGRES_DB", "arrosage")

# Valeurs textuelles reconnues pour l'état ("state") d'une vanne.
TRUTHY_STRINGS = {"open", "on", "true", "1", "ouvert", "ouverte"}
FALSY_STRINGS = {"closed", "off", "false", "0", "ferme", "fermee", "fermé", "fermée"}
