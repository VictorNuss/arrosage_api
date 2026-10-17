import os

POSTGRES_HOST = os.environ.get("POSTGRES_HOST", "timescaledb")
POSTGRES_PORT = os.environ.get("POSTGRES_PORT", "5432")
POSTGRES_USER = os.environ.get("POSTGRES_USER", "arrosage")
POSTGRES_PASSWORD = os.environ.get("POSTGRES_PASSWORD", "")
POSTGRES_DB = os.environ.get("POSTGRES_DB", "arrosage")

DATABASE_URL = (
    f"postgresql+psycopg://{POSTGRES_USER}:{POSTGRES_PASSWORD}"
    f"@{POSTGRES_HOST}:{POSTGRES_PORT}/{POSTGRES_DB}"
)

# Palier de résolution de l'historique selon la durée affichée (voir
# db/init/001_schema.sql) : 1 minute jusqu'à HISTORY_1MIN_MAX_DAYS, 1 heure
# jusqu'à HISTORY_HOURLY_MAX_DAYS, 1 jour au-delà. Les seuils restent dans la
# conservation de chaque palier (15 jours pour la minute, 3 ans pour l'heure).
HISTORY_1MIN_MAX_DAYS = 8
HISTORY_HOURLY_MAX_DAYS = 95

# Le panneau vannes/vue d'ensemble interroge get_latest_readings(), qui
# fusionne la base avec le cache mémoire alimenté par live_state.py (mis à
# jour instantanément à la réception MQTT) : un intervalle court reste bon
# marché puisqu'il ne s'agit plus d'attendre le batching de l'ingest.
OVERVIEW_REFRESH_INTERVAL_MS = 2_000

# Métrique utilisée comme niveau de la cuve à eau, et hauteur (cm) du capteur
# quand la cuve est pleine.
TANK_LEVEL_METRIC = "water_level_cm"
TANK_HEIGHT_FULL_CM = float(os.environ.get("TANK_HEIGHT_FULL_CM", "150"))
TANK_FULL_THRESHOLD_PCT = 95

# Capteurs d'humidité du sol (ESP32 ou Zigbee, même clé "humidity_pct") :
# panneau "Humidité du sol" et condition de programme "sol déjà humide".
# Même métrique que scheduler/app/config.py : à garder synchronisée.
SOIL_MOISTURE_METRIC = "humidity_pct"
SOIL_BATTERY_METRIC = "battery_pct"
# Couleur de la jauge : sous SOIL_DRY_PCT le sol est "sec", au-dessus de
# SOIL_WET_PCT il est "détrempé". Ordres de grandeur, à ajuster selon le sol.
SOIL_DRY_PCT = 30
SOIL_WET_PCT = 70
LOW_BATTERY_PCT = 20
# Un capteur Zigbee sur pile publie toutes les 30-60 min environ : au-delà
# de ce délai sans nouvelle mesure, sa valeur est signalée comme ancienne.
SOIL_STALE_HOURS = 6
# Valeurs par défaut de la condition de programme "sol déjà humide".
DEFAULT_SOIL_MAX_PCT = 60

# En dessous de ce cumul (mm) sur la fenêtre considérée, on affiche "pas de
# pluie prévue" plutôt que de signaler un bruit de mesure/arrondi.
RAIN_THRESHOLD_MM = 0.2

# --- MQTT (commandes de vannes + abonnement live_state pour l'état instantané) ---
MQTT_HOST = os.environ.get("MQTT_HOST", "mosquitto")
MQTT_PORT = int(os.environ.get("MQTT_PORT", "1883"))
MQTT_USERNAME = os.environ.get("MQTT_USERNAME") or None
MQTT_PASSWORD = os.environ.get("MQTT_PASSWORD") or None

VALVE_DURATION_OPTIONS_MIN = [5, 10, 15, 30, 60]
DEFAULT_VALVE_DURATION_MIN = 10

# Fuseau d'affichage des prévisions (jours, heures) : celui du conteneur.
LOCAL_TZ = os.environ.get("TZ") or "Europe/Paris"

# --- Carte radar (widget Windy, gratuit, sans clé API) ---
WEATHER_LAT = float(os.environ.get("WEATHER_LAT", "45.1885"))
WEATHER_LON = float(os.environ.get("WEATHER_LON", "5.7245"))
