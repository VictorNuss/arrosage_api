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
    -- Publiées par le device lui-même (clés MQTT "ip" et "fw_version", à
    -- chaque connexion et sur get_status) : IP locale fixe utilisée pour
    -- relayer l'OTA en HTTP, et version du firmware en cours (affichée
    -- avant/après une mise à jour pour vérifier qu'elle a été appliquée).
    ip_address  TEXT,
    fw_version  TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------------------
-- Mesures de capteurs, modèle EAV (une ligne par métrique) pour supporter
-- l'ajout de nouveaux capteurs côté firmware sans migration de schéma.
--
-- Conservation par paliers de résolution ("downsampling") :
--
--   palier                    résolution        conservation
--   sensor_readings (brut)    ~5 s (firmware)   15 jours
--   sensor_readings_1min      1 minute          15 jours  -> historique 24 h / 7 jours
--   sensor_readings_hourly    1 heure           3 ans     -> historique 30 jours
--   sensor_readings_daily     1 jour (local)    illimité  -> historique 1 an, comparaisons
--   valve_events              chaque changement illimité  -> arrosages exacts, toute période
--
-- Chaque agrégat est rafraîchi depuis le brut sur une fenêtre récente
-- (start_offset) toujours PLUS COURTE que la conservation du brut : un
-- rafraîchissement sur une plage dont le brut a déjà été supprimé effacerait
-- les agrégats correspondants.
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

-- Compression au-delà de 2 jours (les données de capteurs sont peu
-- volumineuses individuellement mais s'accumulent vite), purge à 15 jours.
ALTER TABLE sensor_readings SET (
    timescaledb.compress,
    timescaledb.compress_segmentby = 'device_id, metric'
);

SELECT add_compression_policy('sensor_readings', INTERVAL '2 days', if_not_exists => TRUE);
SELECT add_retention_policy('sensor_readings', INTERVAL '15 days', if_not_exists => TRUE);

-- Journal permanent des vannes (alimenté par ingest, en plus de
-- sensor_readings) : leurs messages sont des changements d'état (0/1,
-- quelques-uns par arrosage), une moyenne horaire ou journalière n'en dirait
-- rien, et ils doivent survivre à la purge du brut pour comparer les
-- arrosages d'une année à l'autre.
CREATE TABLE IF NOT EXISTS valve_events (
    time        TIMESTAMPTZ NOT NULL,
    device_id   TEXT NOT NULL REFERENCES devices (device_id),
    metric      TEXT NOT NULL,              -- 'vanne_1', ...
    value       DOUBLE PRECISION NOT NULL   -- 1.0 ouverte, 0.5 en transition, 0.0 fermée
);

CREATE INDEX IF NOT EXISTS idx_valve_events_device_metric_time
    ON valve_events (device_id, metric, time DESC);
CREATE INDEX IF NOT EXISTS idx_valve_events_time ON valve_events (time);

-- Agrégats continus : le dashboard choisit le palier selon la période
-- affichée (voir queries.get_history). materialized_only = false : les
-- dernières minutes/heures pas encore matérialisées sont calculées à la
-- volée depuis le brut.
CREATE MATERIALIZED VIEW IF NOT EXISTS sensor_readings_1min
WITH (timescaledb.continuous, timescaledb.materialized_only = false) AS
SELECT
    device_id,
    metric,
    time_bucket('1 minute', time) AS bucket,
    avg(value)  AS avg_value,
    min(value)  AS min_value,
    max(value)  AS max_value,
    count(*)    AS sample_count
FROM sensor_readings
GROUP BY device_id, metric, bucket
WITH NO DATA;

SELECT add_continuous_aggregate_policy(
    'sensor_readings_1min',
    start_offset => INTERVAL '2 days',
    end_offset => INTERVAL '1 minute',
    schedule_interval => INTERVAL '5 minutes',
    if_not_exists => TRUE
);
SELECT add_retention_policy('sensor_readings_1min', INTERVAL '15 days', if_not_exists => TRUE);

CREATE MATERIALIZED VIEW IF NOT EXISTS sensor_readings_hourly
WITH (timescaledb.continuous, timescaledb.materialized_only = false) AS
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
SELECT add_retention_policy('sensor_readings_hourly', INTERVAL '3 years', if_not_exists => TRUE);

-- Jours locaux (et non UTC) : un "jour" d'arrosage ou de mesure est une
-- journée calendaire au jardin. Pas de purge.
CREATE MATERIALIZED VIEW IF NOT EXISTS sensor_readings_daily
WITH (timescaledb.continuous, timescaledb.materialized_only = false) AS
SELECT
    device_id,
    metric,
    time_bucket('1 day', time, 'Europe/Paris') AS bucket,
    avg(value)  AS avg_value,
    min(value)  AS min_value,
    max(value)  AS max_value,
    count(*)    AS sample_count
FROM sensor_readings
GROUP BY device_id, metric, bucket
WITH NO DATA;

SELECT add_continuous_aggregate_policy(
    'sensor_readings_daily',
    start_offset => INTERVAL '4 days',
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
