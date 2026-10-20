import os

POSTGRES_HOST = os.environ.get("POSTGRES_HOST", "timescaledb")
POSTGRES_PORT = int(os.environ.get("POSTGRES_PORT", "5432"))
POSTGRES_USER = os.environ.get("POSTGRES_USER", "arrosage")
POSTGRES_PASSWORD = os.environ.get("POSTGRES_PASSWORD", "")
POSTGRES_DB = os.environ.get("POSTGRES_DB", "arrosage")

MQTT_HOST = os.environ.get("MQTT_HOST", "mosquitto")
MQTT_PORT = int(os.environ.get("MQTT_PORT", "1883"))
MQTT_USERNAME = os.environ.get("MQTT_USERNAME") or None
MQTT_PASSWORD = os.environ.get("MQTT_PASSWORD") or None

# Même métrique/seuil que la jauge du dashboard (dashboard/app/config.py) :
# à garder synchronisé si l'un des deux change.
TANK_LEVEL_METRIC = "water_level_cm"
TANK_HEIGHT_FULL_CM = float(os.environ.get("TANK_DISTANCE_EMPTY_CM") or "150") - float(
    os.environ.get("TANK_DISTANCE_FULL_CM") or "25"
)

# Humidité du sol (ESP32 ou capteurs Zigbee republiés par ingest), pour la
# condition "max_soil_moisture". Même métrique que dashboard/app/config.py.
SOIL_MOISTURE_METRIC = "humidity_pct"

# Fréquence de vérification des programmes dus. Une minute suffit largement
# (les horaires de programme sont exprimés en HH:MM) et reste bon marché.
CHECK_INTERVAL_SECONDS = 30
