import os

POSTGRES_HOST = os.environ.get("POSTGRES_HOST", "timescaledb")
POSTGRES_PORT = int(os.environ.get("POSTGRES_PORT", "5432"))
POSTGRES_USER = os.environ.get("POSTGRES_USER", "arrosage")
POSTGRES_PASSWORD = os.environ.get("POSTGRES_PASSWORD", "")
POSTGRES_DB = os.environ.get("POSTGRES_DB", "arrosage")

WEATHER_LAT = float(os.environ.get("WEATHER_LAT", "45.1885"))
WEATHER_LON = float(os.environ.get("WEATHER_LON", "5.7245"))

METEOFRANCE_API_KEY = os.environ.get("METEOFRANCE_API_KEY") or None
METEOFRANCE_STATION_ID = os.environ.get("METEOFRANCE_STATION_ID") or None

METEOFRANCE_BASE_URL = "https://public-api.meteofrance.fr/public"

# Chemins WCS. Voir README.md > Service météo : ces identifiants de ressource
# proviennent de la doc du portail (portail-api.meteofrance.fr) et peuvent
# changer ; ajustez-les ici si Météo-France les fait évoluer.
AROME_WCS_PATH = "/arome/1.0/wcs/MF-NWP-HIGHRES-AROME-001-FRANCE-WCS"
ARPEGE_WCS_PATH = "/arpege/1.0/wcs/MF-NWP-GLOBAL-ARPEGE-01-EUROPE-WCS"

# Mots-clés utilisés pour repérer, dans les CoverageId renvoyés par
# GetCapabilities, les paramètres qui nous intéressent. À adapter si
# Météo-France change sa nomenclature (voir logs du service en cas de 0
# correspondance : la liste complète des CoverageId disponibles y est tracée).
PARAMETER_KEYWORDS = {
    "temperature_c": ["TEMPERATURE"],
    "precipitation_mm": ["TOTAL_PRECIPITATION", "PRECIPITATION"],
}

# DPClim : pas de temps utilisé pour la pluviométrie/température passées.
DPCLIM_STEP = "quotidienne"
DPCLIM_METRIC_COLUMNS = {
    "RR": "precipitation_mm",
    "TN": "temperature_min_c",
    "TX": "temperature_max_c",
}

FORECAST_REFRESH_INTERVAL_SECONDS = 3 * 3600
OBSERVED_REFRESH_INTERVAL_SECONDS = 24 * 3600
