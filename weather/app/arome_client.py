"""Client pour les API WCS AROME / ARPEGE de Météo-France.

Ces API renvoient des grilles GRIB2 (et non du JSON point par point). Pour
construire une série temporelle "prévisions des prochains jours" en un point
donné, il faut :
  1. interroger GetCapabilities pour lister les CoverageId disponibles pour
     le run le plus récent (chaque CoverageId correspond à un paramètre et
     une échéance donnés) ;
  2. pour chaque CoverageId d'intérêt, appeler GetCoverage pour récupérer la
     grille GRIB2 correspondante ;
  3. décoder le GRIB2 (cfgrib/xarray) et extraire le point de grille le plus
     proche des coordonnées configurées.

Les identifiants de ressource WCS et les mots-clés de paramètres sont dans
config.py : si Météo-France change sa nomenclature, ce sont les seuls
endroits à ajuster.
"""

import logging
import re
import tempfile
from xml.etree import ElementTree

import cfgrib
import requests

from . import config

log = logging.getLogger("weather.arome")

_WCS_NS = {
    "wcs": "http://www.opengis.net/wcs/2.0",
    "ows": "http://www.opengis.net/ows/2.0",
}

_RUN_TIMESTAMP_RE = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}\.\d{2}\.\d{2}Z")

GRIB_FORMAT = "application/wmo-grib"


class MeteoFranceApiError(Exception):
    pass


def _headers():
    if not config.METEOFRANCE_API_KEY:
        raise MeteoFranceApiError("METEOFRANCE_API_KEY non configurée")
    return {"apikey": config.METEOFRANCE_API_KEY, "accept": "application/json"}


def _get_capabilities(wcs_path: str) -> list[str]:
    url = f"{config.METEOFRANCE_BASE_URL}{wcs_path}/GetCapabilities"
    resp = requests.get(
        url, headers=_headers(), params={"service": "WCS", "version": "2.0.1"}, timeout=30
    )
    if resp.status_code != 200:
        raise MeteoFranceApiError(
            f"GetCapabilities a échoué ({resp.status_code}) sur {wcs_path}: {resp.text[:300]}"
        )
    root = ElementTree.fromstring(resp.content)
    return [
        el.text
        for el in root.findall(".//wcs:CoverageSummary/wcs:CoverageId", _WCS_NS)
        if el.text
    ]


def _select_latest_run(coverage_ids: list[str], keywords: list[str]) -> list[str]:
    matches = [cid for cid in coverage_ids if any(kw in cid for kw in keywords)]
    if not matches:
        return []

    def run_token(cid):
        found = _RUN_TIMESTAMP_RE.search(cid)
        return found.group(0) if found else ""

    latest_run = max(run_token(cid) for cid in matches)
    if not latest_run:
        # Nomenclature inattendue : on ne peut pas identifier le run le plus
        # récent, on retourne quand même les correspondances pour ne pas
        # bloquer, mais on log pour investigation.
        log.warning(
            "Impossible d'extraire un horodatage de run dans les CoverageId "
            "correspondants; nomenclature Météo-France peut-être changée."
        )
        return matches
    return [cid for cid in matches if run_token(cid) == latest_run]


def _get_coverage_series(wcs_path: str, coverage_id: str, lat: float, lon: float):
    url = f"{config.METEOFRANCE_BASE_URL}{wcs_path}/GetCoverage"
    resp = requests.get(
        url,
        headers=_headers(),
        params={
            "service": "WCS",
            "version": "2.0.1",
            "coverageId": coverage_id,
            "format": GRIB_FORMAT,
        },
        timeout=60,
    )
    if resp.status_code != 200:
        raise MeteoFranceApiError(
            f"GetCoverage a échoué ({resp.status_code}) pour {coverage_id}: {resp.text[:300]}"
        )

    with tempfile.NamedTemporaryFile(suffix=".grib2") as tmp:
        tmp.write(resp.content)
        tmp.flush()
        datasets = cfgrib.open_datasets(tmp.name)

        series = []
        for ds in datasets:
            data_vars = list(ds.data_vars)
            if not data_vars:
                continue
            point = ds.sel(latitude=lat, longitude=lon, method="nearest")
            for var_name in data_vars:
                value = float(point[var_name].values)
                valid_time = point["valid_time"].values
                series.append((valid_time, value))
        return series


def fetch_forecast_series(lat: float, lon: float):
    """Renvoie un dict {source: [{"valid_time", "metric", "value"}, ...]} pour
    AROME (~42h) complété par ARPEGE (jours suivants, jusqu'à J+4)."""
    results = {}

    for wcs_path, source in ((config.AROME_WCS_PATH, "AROME"), (config.ARPEGE_WCS_PATH, "ARPEGE")):
        try:
            coverage_ids = _get_capabilities(wcs_path)
        except Exception:
            log.exception("GetCapabilities indisponible pour %s (%s)", source, wcs_path)
            continue

        source_rows = results.setdefault(source, [])
        for metric, keywords in config.PARAMETER_KEYWORDS.items():
            selected = _select_latest_run(coverage_ids, keywords)
            if not selected:
                log.warning(
                    "Aucun CoverageId %s trouvé pour '%s' sur %s parmi %s identifiants reçus",
                    keywords, metric, source, len(coverage_ids),
                )
                continue
            for coverage_id in selected:
                try:
                    for valid_time, value in _get_coverage_series(wcs_path, coverage_id, lat, lon):
                        source_rows.append({"valid_time": valid_time, "metric": metric, "value": value})
                except Exception:
                    log.exception("Échec de récupération de %s (%s)", coverage_id, source)

    return results
