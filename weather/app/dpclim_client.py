"""Client pour l'API DPClim (Données Publiques Climatologiques) de
Météo-France : pluviométrie/température passées d'une station.

Fonctionnement en deux temps (asynchrone) :
  1. POST /commande-station/{pas de temps} -> renvoie un identifiant de commande
  2. GET /commande/fichier?id-cmde=... -> 204 tant que ce n'est pas prêt,
     200 + CSV une fois généré (généralement en quelques secondes).
"""

import csv
import io
import logging
import time
from datetime import datetime, timedelta, timezone

import requests

from . import config

log = logging.getLogger("weather.dpclim")

_BASE = f"{config.METEOFRANCE_BASE_URL}/DPClim/v1"


class MeteoFranceApiError(Exception):
    pass


def _headers():
    if not config.METEOFRANCE_API_KEY:
        raise MeteoFranceApiError("METEOFRANCE_API_KEY non configurée")
    return {"apikey": config.METEOFRANCE_API_KEY, "accept": "application/json"}


def _request_command(station_id: str, start: datetime, end: datetime) -> str:
    url = f"{_BASE}/commande-station/{config.DPCLIM_STEP}"
    params = {
        "id-station": station_id,
        "date-deb-periode": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "date-fin-periode": end.strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    resp = requests.get(url, headers=_headers(), params=params, timeout=30)
    if resp.status_code != 202 and resp.status_code != 200:
        raise MeteoFranceApiError(f"Commande DPClim refusée ({resp.status_code}): {resp.text[:300]}")
    return str(resp.json()["elaboration"] if "elaboration" in resp.json() else resp.json()["id"])


def _download_command(command_id: str, max_attempts=10, delay_seconds=5) -> str:
    url = f"{_BASE}/commande/fichier"
    for attempt in range(1, max_attempts + 1):
        resp = requests.get(
            url, headers=_headers(), params={"id-cmde": command_id}, timeout=30
        )
        if resp.status_code == 201 or resp.status_code == 200:
            return resp.content.decode("latin-1")
        if resp.status_code == 204:
            log.info("Commande DPClim %s pas encore prête (tentative %s/%s)", command_id, attempt, max_attempts)
            time.sleep(delay_seconds)
            continue
        raise MeteoFranceApiError(
            f"Téléchargement DPClim a échoué ({resp.status_code}) pour {command_id}: {resp.text[:300]}"
        )
    raise MeteoFranceApiError(f"Commande DPClim {command_id} jamais prête après {max_attempts} tentatives")


def _parse_csv(content: str):
    """Renvoie une liste de dicts {time, metric, value}. Colonnes attendues:
    voir config.DPCLIM_METRIC_COLUMNS ; les colonnes inconnues sont ignorées."""
    reader = csv.DictReader(io.StringIO(content), delimiter=";")
    if reader.fieldnames:
        log.info("Colonnes DPClim reçues: %s", reader.fieldnames)

    rows = []
    for record in reader:
        date_raw = record.get("DATE")
        if not date_raw:
            continue
        try:
            day = datetime.strptime(date_raw, "%Y%m%d").replace(tzinfo=timezone.utc)
        except ValueError:
            log.warning("Date DPClim illisible, ligne ignorée: %s", date_raw)
            continue

        for column, metric in config.DPCLIM_METRIC_COLUMNS.items():
            raw_value = record.get(column)
            if raw_value in (None, "", "mq"):  # "mq" = donnée manquante côté Météo-France
                continue
            try:
                value = float(raw_value.replace(",", "."))
            except ValueError:
                continue
            rows.append({"time": day, "metric": metric, "value": value})
    return rows


def fetch_observed_series(station_id: str, days: int = 7):
    """Commande et télécharge la pluviométrie/température des `days` derniers
    jours pour la station configurée. Renvoie une liste de (time, metric, value)."""
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=days)
    command_id = _request_command(station_id, start, end)
    content = _download_command(command_id)
    return _parse_csv(content)
