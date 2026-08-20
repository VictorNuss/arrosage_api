import os

MQTT_HOST = os.environ.get("MQTT_HOST", "mosquitto")
MQTT_PORT = int(os.environ.get("MQTT_PORT", "1883"))
MQTT_USERNAME = os.environ.get("MQTT_USERNAME") or None
MQTT_PASSWORD = os.environ.get("MQTT_PASSWORD") or None
MQTT_TOPIC = "arrosage/+/etat/#"
VALVE_METRIC_HINT = "vanne"

# Clé publiée par chaque device (à sa connexion MQTT et en réponse à un
# get_status) pour faire connaître son IP locale fixe : arrosage/<device_id>
# /etat/ip, {"value": "192.168.1.50"}. Contrairement aux autres clés, la
# valeur est une chaîne, pas un nombre : elle alimente devices.ip_address
# (utilisée pour cibler l'OTA), pas sensor_readings (qui ne stocke que des
# float).
IP_METRIC_KEY = "ip"

POSTGRES_HOST = os.environ.get("POSTGRES_HOST", "timescaledb")
POSTGRES_PORT = int(os.environ.get("POSTGRES_PORT", "5432"))
POSTGRES_USER = os.environ.get("POSTGRES_USER", "arrosage")
POSTGRES_PASSWORD = os.environ.get("POSTGRES_PASSWORD", "")
POSTGRES_DB = os.environ.get("POSTGRES_DB", "arrosage")

# Valeurs textuelles reconnues pour l'état ("state") d'une vanne. Une
# électrovanne motorisée (condensateur de démarrage) met un temps variable
# (~15s par défaut, configurable côté firmware, pas figé) à actionner
# réellement le passage d'eau, à l'ouverture COMME à la fermeture : le
# firmware publie l'état intermédiaire "transitioning" pendant ce délai
# plutôt que de faire attendre le prochain état stable.
TRUTHY_STRINGS = {"open", "on", "true", "1", "ouvert", "ouverte"}
FALSY_STRINGS = {"closed", "off", "false", "0", "ferme", "fermee", "fermé", "fermée"}
TRANSITION_STRINGS = {"transitioning", "transition", "moving", "opening", "closing"}

# Encodage numérique stocké en base (double precision) : 1.0 ouverte, 0.0
# fermée, 0.5 en transition. Les lectures utilisent des seuils (>=0.75 /
# <=0.25) plutôt qu'une égalité stricte, par robustesse à l'arrondi flottant.
VALVE_OPEN_VALUE = 1.0
VALVE_CLOSED_VALUE = 0.0
VALVE_TRANSITION_VALUE = 0.5
