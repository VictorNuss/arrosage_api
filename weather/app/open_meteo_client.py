"""Client Open-Meteo (open-meteo.com) : prévisions et passé récent AROME/ARPEGE.

Gratuit, sans clé API ni compte pour un usage non-commercial — Open-Meteo
utilise réellement les modèles Météo-France en interne. Un seul appel HTTP
(paramètre `past_days` + `forecast_days`) renvoie à la fois le passé récent
et la prévision ; main.py répartit ensuite les points entre `weather_observed`
(temps < maintenant) et `weather_forecast` (temps >= maintenant).

Contrairement à l'ancienne intégration WCS/GRIB2, la précipitation horaire
renvoyée ici est la pluie tombée PENDANT l'heure précédente (pas un cumul
depuis le début du run) : pas besoin de calcul de delta côté dashboard.
"""

import logging
from datetime import datetime, timezone

import requests

log = logging.getLogger("weather.open_meteo")

BASE_URL = "https://api.open-meteo.com/v1/forecast"

# Variantes "seamless" : Open-Meteo comble les trous entre sous-variantes
# d'un même modèle (ex: AROME 15min vs AROME France selon l'échéance) plutôt
# que d'avoir à gérer ça nous-mêmes.
MODELS = {
    "AROME": "meteofrance_arome_seamless",
    "ARPEGE": "meteofrance_arpege_europe",
}

HOURLY_VARIABLES = {
    "temperature_2m": "temperature_c",
    "precipitation": "precipitation_mm",
}


class OpenMeteoApiError(Exception):
    pass


def _fetch(lat: float, lon: float, past_days: int, forecast_days: int) -> dict:
    params = {
        "latitude": lat,
        "longitude": lon,
        "hourly": ",".join(HOURLY_VARIABLES),
        "models": ",".join(MODELS.values()),
        "past_days": past_days,
        "forecast_days": forecast_days,
        "timezone": "UTC",
    }
    resp = requests.get(BASE_URL, params=params, timeout=30)
    if resp.status_code != 200:
        raise OpenMeteoApiError(f"Open-Meteo a répondu {resp.status_code}: {resp.text[:300]}")
    return resp.json()


def _extract_series(data: dict, source_label: str, model_id: str) -> list[dict]:
    hourly = data.get("hourly", {})
    times = hourly.get("time", [])

    series = []
    for variable, metric in HOURLY_VARIABLES.items():
        # Avec plusieurs modèles explicites, Open-Meteo suffixe chaque
        # variable par l'identifiant du modèle (ex: temperature_2m_meteofrance_arome_seamless).
        # On retombe sur la clé sans suffixe si un seul modèle était demandé,
        # par robustesse face à un futur changement de convention.
        values = hourly.get(f"{variable}_{model_id}", hourly.get(variable))
        if values is None:
            log.warning("Variable '%s' absente de la réponse Open-Meteo pour %s", variable, model_id)
            continue

        for ts, value in zip(times, values):
            if value is None:
                continue
            valid_time = datetime.fromisoformat(ts).replace(tzinfo=timezone.utc)
            series.append({"time": valid_time, "metric": metric, "value": float(value), "source": source_label})
    return series


def fetch_series(lat: float, lon: float, past_days: int, forecast_days: int) -> list[dict]:
    """Renvoie une liste de dicts {time, metric, value, source} couvrant à la
    fois le passé récent (past_days) et la prévision (forecast_days), pour
    AROME et ARPEGE."""
    data = _fetch(lat, lon, past_days, forecast_days)
    rows = []
    for source_label, model_id in MODELS.items():
        rows.extend(_extract_series(data, source_label, model_id))
    return rows
