import logging
import time
from datetime import datetime, timezone

from . import arome_client, config, db, dpclim_client

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("weather.main")

CHECK_INTERVAL_SECONDS = 60


def run_forecast_cycle(engine):
    if not config.METEOFRANCE_API_KEY:
        log.warning("METEOFRANCE_API_KEY absente : cycle prévisions ignoré.")
        return
    try:
        by_source = arome_client.fetch_forecast_series(config.WEATHER_LAT, config.WEATHER_LON)
    except Exception:
        log.exception("Échec du cycle de prévisions AROME/ARPEGE")
        return

    total = sum(len(rows) for rows in by_source.values())
    if not total:
        log.warning("Aucune donnée de prévision récupérée ce cycle.")
        return

    fetched_at = datetime.now(timezone.utc)
    for source, source_rows in by_source.items():
        db.replace_forecast(engine, fetched_at, source, config.WEATHER_LAT, config.WEATHER_LON, source_rows)
    log.info("Prévisions remplacées (instantané): %s points", total)


def run_observed_cycle(engine):
    if not config.METEOFRANCE_API_KEY or not config.METEOFRANCE_STATION_ID:
        log.warning("METEOFRANCE_API_KEY ou METEOFRANCE_STATION_ID absente : cycle observations ignoré.")
        return
    try:
        rows = dpclim_client.fetch_observed_series(config.METEOFRANCE_STATION_ID)
    except Exception:
        log.exception("Échec du cycle d'observations DPClim")
        return

    if not rows:
        log.warning("Aucune observation récupérée ce cycle.")
        return

    db.upsert_observed(engine, config.METEOFRANCE_STATION_ID, rows)
    log.info("Observations insérées/mises à jour: %s points", len(rows))


def main():
    engine = db.create_engine_with_retry()

    next_forecast_run = 0.0
    next_observed_run = 0.0

    while True:
        now = time.monotonic()
        if now >= next_forecast_run:
            run_forecast_cycle(engine)
            next_forecast_run = now + config.FORECAST_REFRESH_INTERVAL_SECONDS
        if now >= next_observed_run:
            run_observed_cycle(engine)
            next_observed_run = now + config.OBSERVED_REFRESH_INTERVAL_SECONDS
        time.sleep(CHECK_INTERVAL_SECONDS)


if __name__ == "__main__":
    main()
