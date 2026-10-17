-- Programmes d'arrosage automatiques : planning + conditions + historique
-- d'exécution. Le dashboard écrit/édite ces tables, le service `scheduler`
-- les lit et déclenche les vannes via MQTT quand un programme est dû.

CREATE TABLE IF NOT EXISTS watering_programs (
    id                  SERIAL PRIMARY KEY,
    name                TEXT NOT NULL,
    enabled             BOOLEAN NOT NULL DEFAULT TRUE,
    start_time          TIME NOT NULL,
    -- Jours ISO (1=lundi .. 7=dimanche). {1,2,3,4,5,6,7} = tous les jours.
    days_of_week        SMALLINT[] NOT NULL,
    default_duration_s  INTEGER NOT NULL,
    -- Liste de conditions, ex:
    -- [{"type":"no_rain_forecast","window_hours":3,"threshold_mm":0.2},
    --  {"type":"avoid_time_window","start":"10:00","end":"18:00"},
    --  {"type":"min_tank_pct","min_pct":10}]
    conditions          JSONB NOT NULL DEFAULT '[]',
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS watering_program_valves (
    program_id  INTEGER NOT NULL REFERENCES watering_programs (id) ON DELETE CASCADE,
    device_id   TEXT NOT NULL,
    metric      TEXT NOT NULL,
    -- NULL = utilise watering_programs.default_duration_s.
    duration_s  INTEGER,
    PRIMARY KEY (program_id, device_id, metric)
);

-- Historique/audit : pas de FK stricte vers watering_programs pour pouvoir
-- garder l'historique même après suppression d'un programme (nom en dur).
CREATE TABLE IF NOT EXISTS watering_runs (
    id                SERIAL PRIMARY KEY,
    program_id        INTEGER REFERENCES watering_programs (id) ON DELETE SET NULL,
    program_name      TEXT NOT NULL,
    scheduled_for     TIMESTAMPTZ NOT NULL,
    status            TEXT NOT NULL,  -- 'executed' | 'skipped'
    skip_reason       TEXT,
    valves_triggered  JSONB,
    executed_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_watering_runs_scheduled_for ON watering_runs (scheduled_for DESC);
CREATE INDEX IF NOT EXISTS idx_watering_runs_program_id ON watering_runs (program_id);

-- Empêche un double déclenchement du même programme sur le même créneau
-- planifié, même si le scheduler tique plusieurs fois dans la même minute.
CREATE UNIQUE INDEX IF NOT EXISTS uq_watering_runs_program_slot
    ON watering_runs (program_id, scheduled_for) WHERE program_id IS NOT NULL;
