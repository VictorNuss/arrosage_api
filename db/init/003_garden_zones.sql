-- Potager : zones (une vanne + ses capteurs de sol + une culture) et
-- bibliothèque de profils de culture.
--
-- Échelle d'humidité des profils : celle des capteurs de la zone une fois
-- calibrés (0 = sol sec au toucher, 100 = sol gorgé d'eau ~1 h après un gros
-- arrosage, voir garden_zones.moisture_dry_ref / moisture_wet_ref). Sans
-- calibration, la valeur brute du capteur (%) est utilisée telle quelle.

CREATE TABLE IF NOT EXISTS crop_profiles (
    id               SERIAL PRIMARY KEY,
    name             TEXT NOT NULL UNIQUE,
    -- Plage d'humidité du sol visée : sous moisture_min il faut arroser,
    -- au-dessus de moisture_max le sol est trop humide pour cette culture.
    moisture_min     SMALLINT NOT NULL,
    moisture_max     SMALLINT NOT NULL,
    -- 'frequent' : sol frais en permanence (petits arrosages rapprochés) ;
    -- 'deep' : arrosages profonds et espacés, laisser ressuyer entre deux.
    watering_style   TEXT NOT NULL CHECK (watering_style IN ('frequent', 'deep')),
    -- Heures de plein soleil par jour souhaitées.
    sun_hours_min    SMALLINT NOT NULL,
    frost_sensitive  BOOLEAN NOT NULL,
    mildew_sensitive BOOLEAN NOT NULL,
    notes            TEXT,
    CHECK (moisture_min < moisture_max)
);

CREATE TABLE IF NOT EXISTS garden_zones (
    id                SERIAL PRIMARY KEY,
    name              TEXT NOT NULL,
    -- Vanne qui arrose la zone (une vanne n'arrose qu'une zone).
    valve_device_id   TEXT,
    valve_metric      TEXT,
    crop_profile_id   INTEGER REFERENCES crop_profiles (id) ON DELETE SET NULL,
    -- Calibration des capteurs de la zone : valeurs brutes relevées sol sec
    -- au toucher / sol gorgé d'eau. NULL = pas encore calibrée.
    moisture_dry_ref  DOUBLE PRECISION,
    moisture_wet_ref  DOUBLE PRECISION,
    notes             TEXT,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (valve_device_id, valve_metric),
    CHECK (moisture_dry_ref IS NULL OR moisture_wet_ref IS NULL OR moisture_dry_ref < moisture_wet_ref)
);

CREATE TABLE IF NOT EXISTS garden_zone_sensors (
    zone_id    INTEGER NOT NULL REFERENCES garden_zones (id) ON DELETE CASCADE,
    device_id  TEXT NOT NULL,
    PRIMARY KEY (zone_id, device_id)
);

-- Profils indicatifs pour un potager paillé au goutte-à-goutte, à ajuster
-- avec l'expérience (modifiables en base). Les plages sont volontairement
-- larges : c'est la tendance et la calibration de la zone qui comptent.
INSERT INTO crop_profiles
    (name, moisture_min, moisture_max, watering_style, sun_hours_min, frost_sensitive, mildew_sensitive, notes)
VALUES
    ('Tomate',          40, 75, 'deep',     6, TRUE,  TRUE,  'Arrosage au pied, profond et espacé : trop d''eau rend les fruits fades et les fait éclater.'),
    ('Courgette',       45, 80, 'deep',     6, TRUE,  TRUE,  'Grosse consommatrice d''eau en été ; sensible à l''oïdium.'),
    ('Concombre',       50, 80, 'frequent', 6, TRUE,  TRUE,  'Sol frais en permanence, irrégularités = fruits amers.'),
    ('Poivron / piment',45, 75, 'deep',     6, TRUE,  FALSE, 'Aime la chaleur ; éviter l''excès d''eau.'),
    ('Aubergine',       45, 75, 'deep',     6, TRUE,  FALSE, 'Très frileuse, plein soleil.'),
    ('Pomme de terre',  45, 75, 'deep',     6, TRUE,  TRUE,  'Sensible au mildiou ; besoin en eau surtout à la floraison.'),
    ('Haricot',         45, 75, 'deep',     6, TRUE,  FALSE, 'Peu d''eau avant la floraison, régulier ensuite.'),
    ('Salade',          55, 85, 'frequent', 4, FALSE, FALSE, 'Sol frais en permanence ; supporte la mi-ombre, monte en graine à la chaleur.'),
    ('Épinard',         55, 80, 'frequent', 3, FALSE, FALSE, 'Mi-ombre, monte vite en graine au soleil et au sec.'),
    ('Radis',           50, 80, 'frequent', 4, FALSE, FALSE, 'Un manque d''eau le rend creux et piquant.'),
    ('Carotte',         40, 70, 'deep',     5, FALSE, FALSE, 'Arrosages réguliers mais pas excessifs (sinon éclatement).'),
    ('Oignon / ail',    30, 60, 'deep',     6, FALSE, FALSE, 'Peu d''eau, arrêter avant la récolte.'),
    ('Fraisier',        50, 80, 'frequent', 6, FALSE, FALSE, 'Sol frais pendant la fructification ; le paillage limite la pourriture.'),
    ('Basilic',         45, 75, 'frequent', 6, TRUE,  FALSE, 'Chaleur et soleil, sol frais mais pas détrempé.'),
    ('Aromatiques méditerranéennes', 20, 50, 'deep', 6, FALSE, FALSE, 'Thym, romarin, sauge : craignent surtout l''excès d''eau.')
ON CONFLICT (name) DO NOTHING;
