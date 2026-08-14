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

# Au-delà de cette plage, l'historique interroge l'agrégat horaire plutôt
# que la table brute.
HOURLY_AGGREGATE_THRESHOLD_DAYS = 2

OVERVIEW_REFRESH_INTERVAL_MS = 15_000

# Métrique utilisée comme niveau de la cuve à eau, et hauteur (cm) du capteur
# quand la cuve est pleine.
TANK_LEVEL_METRIC = "water_level_cm"
TANK_HEIGHT_FULL_CM = float(os.environ.get("TANK_HEIGHT_FULL_CM", "150"))
TANK_FULL_THRESHOLD_PCT = 95

# En dessous de ce cumul (mm) sur la fenêtre considérée, on affiche "pas de
# pluie prévue" plutôt que de signaler un bruit de mesure/arrondi.
RAIN_THRESHOLD_MM = 0.2

# --- MQTT (envoi de commandes d'ouverture/fermeture de vannes) ---
MQTT_HOST = os.environ.get("MQTT_HOST", "mosquitto")
MQTT_PORT = int(os.environ.get("MQTT_PORT", "1883"))
MQTT_USERNAME = os.environ.get("MQTT_USERNAME") or None
MQTT_PASSWORD = os.environ.get("MQTT_PASSWORD") or None

VALVE_DURATION_OPTIONS_MIN = [5, 10, 15, 30, 60]
DEFAULT_VALVE_DURATION_MIN = 10

# --- Carte radar (widget Windy, gratuit, sans clé API) ---
WEATHER_LAT = float(os.environ.get("WEATHER_LAT", "45.1885"))
WEATHER_LON = float(os.environ.get("WEATHER_LON", "5.7245"))
