-- Schéma TimescaleDB pour la supervision d'arrosage
-- Exécuté automatiquement au premier démarrage du conteneur timescaledb
-- (via /docker-entrypoint-initdb.d)

CREATE EXTENSION IF NOT EXISTS timescaledb;

-- ---------------------------------------------------------------------------
-- Appareils (ESP32) connus. Auto-alimentée par le service ingest.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS devices (
    device_id   TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    location    TEXT,
    lat         DOUBLE PRECISION,
    lon         DOUBLE PRECISION,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------------------
-- Mesures de capteurs, modèle EAV (une ligne par métrique) pour supporter
-- l'ajout de nouveaux capteurs côté firmware sans migration de schéma.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS sensor_readings (
    time        TIMESTAMPTZ NOT NULL,
    device_id   TEXT NOT NULL REFERENCES devices (device_id),
    metric      TEXT NOT NULL,
    value       DOUBLE PRECISION NOT NULL,
    unit        TEXT
);

SELECT create_hypertable(
    'sensor_readings', 'time',
    chunk_time_interval => INTERVAL '1 day',
    if_not_exists => TRUE
);

CREATE INDEX IF NOT EXISTS idx_sensor_readings_device_metric_time
    ON sensor_readings (device_id, metric, time DESC);

-- Compression : au-delà de 7 jours, les chunks sont compressés (les données
-- de capteurs sont peu volumineuses individuellement mais s'accumulent vite).
ALTER TABLE sensor_readings SET (
    timescaledb.compress,
    timescaledb.compress_segmentby = 'device_id, metric'
);

SELECT add_compression_policy('sensor_readings', INTERVAL '7 days', if_not_exists => TRUE);

-- Agrégat continu horaire, utilisé par le dashboard pour les plages > ~2 jours
-- afin d'éviter de scanner des millions de points bruts.
CREATE MATERIALIZED VIEW IF NOT EXISTS sensor_readings_hourly
WITH (timescaledb.continuous) AS
SELECT
    device_id,
    metric,
    time_bucket('1 hour', time) AS bucket,
    avg(value)  AS avg_value,
    min(value)  AS min_value,
    max(value)  AS max_value,
    count(*)    AS sample_count
FROM sensor_readings
GROUP BY device_id, metric, bucket
WITH NO DATA;

SELECT add_continuous_aggregate_policy(
    'sensor_readings_hourly',
    start_offset => INTERVAL '3 days',
    end_offset => INTERVAL '1 hour',
    schedule_interval => INTERVAL '1 hour',
    if_not_exists => TRUE
);

-- ---------------------------------------------------------------------------
-- Météo : prévisions (AROME / ARPEGE) et observations passées (DPClim)
-- ---------------------------------------------------------------------------
-- weather_forecast est un instantané (snapshot), pas un historique : à chaque
-- cycle, le service weather remplace entièrement les lignes d'une source
-- donnée plutôt que d'accumuler indéfiniment (une prévision périmée n'a pas
-- de valeur une fois la suivante disponible). Table classique, pas de
-- hypertable ici.
CREATE TABLE IF NOT EXISTS weather_forecast (
    fetched_at  TIMESTAMPTZ NOT NULL,
    valid_time  TIMESTAMPTZ NOT NULL,
    source      TEXT NOT NULL,           -- 'AROME' | 'ARPEGE'
    lat         DOUBLE PRECISION NOT NULL,
    lon         DOUBLE PRECISION NOT NULL,
    metric      TEXT NOT NULL,           -- 'temperature_c' | 'precipitation_mm' | ...
    value       DOUBLE PRECISION NOT NULL,
    PRIMARY KEY (source, metric, valid_time)
);

CREATE INDEX IF NOT EXISTS idx_weather_forecast_metric_valid_time
    ON weather_forecast (metric, valid_time);

CREATE TABLE IF NOT EXISTS weather_observed (
    time        TIMESTAMPTZ NOT NULL,
    station_id  TEXT NOT NULL,
    metric      TEXT NOT NULL,           -- 'precipitation_mm' | 'temperature_c' | ...
    value       DOUBLE PRECISION NOT NULL
);

SELECT create_hypertable(
    'weather_observed', 'time',
    chunk_time_interval => INTERVAL '30 days',
    if_not_exists => TRUE
);

CREATE INDEX IF NOT EXISTS idx_weather_observed_station_metric_time
    ON weather_observed (station_id, metric, time DESC);

-- Évite les doublons lors des ré-imports DPClim (commande sur une plage qui
-- chevauche des données déjà présentes).
CREATE UNIQUE INDEX IF NOT EXISTS uq_weather_observed
    ON weather_observed (station_id, metric, time);
