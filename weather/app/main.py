import logging
import time
from datetime import datetime, timezone

from . import config, db, open_meteo_client

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("weather.main")

CHECK_INTERVAL_SECONDS = 60

# Pas un vrai identifiant de station : Open-Meteo interroge directement les
# coordonnées configurées, sans notion de station météo.
OBSERVED_SOURCE_LABEL = "open-meteo"


def run_cycle(engine):
    try:
        rows = open_meteo_client.fetch_series(
            config.WEATHER_LAT,
            config.WEATHER_LON,
            past_days=config.OPEN_METEO_PAST_DAYS,
            forecast_days=config.OPEN_METEO_FORECAST_DAYS,
        )
    except Exception:
        log.exception("Échec du cycle Open-Meteo")
        return

    if not rows:
        log.warning("Aucune donnée récupérée ce cycle.")
        return

    now = datetime.now(timezone.utc)
    fetched_at = now

    forecast_by_source = {}
    # AROME et ARPEGE renvoient tous les deux des points sur le passé récent
    # (chevauchement) : dédupliqué par (metric, time) en préférant AROME,
    # sinon l'upsert planterait (ON CONFLICT DO UPDATE ne peut pas toucher
    # deux fois la même ligne dans un même batch).
    observed_by_key = {}
    for row in rows:
        if row["time"] < now:
            key = (row["metric"], row["time"])
            if key not in observed_by_key or row["source"] == "AROME":
                observed_by_key[key] = row
        else:
            forecast_by_source.setdefault(row["source"], []).append(
                {"valid_time": row["time"], "metric": row["metric"], "value": row["value"]}
            )

    observed_rows = [
        {"time": row["time"], "metric": row["metric"], "value": row["value"]}
        for row in observed_by_key.values()
    ]

    for source, source_rows in forecast_by_source.items():
        db.replace_forecast(engine, fetched_at, source, config.WEATHER_LAT, config.WEATHER_LON, source_rows)
    if observed_rows:
        db.upsert_observed(engine, OBSERVED_SOURCE_LABEL, observed_rows)

    log.info(
        "Cycle Open-Meteo: %s points de prévision, %s points passés",
        sum(len(v) for v in forecast_by_source.values()),
        len(observed_rows),
    )


def main():
    engine = db.create_engine_with_retry()

    next_run = 0.0
    while True:
        now = time.monotonic()
        if now >= next_run:
            run_cycle(engine)
            next_run = now + config.REFRESH_INTERVAL_SECONDS
        time.sleep(CHECK_INTERVAL_SECONDS)


if __name__ == "__main__":
    main()
