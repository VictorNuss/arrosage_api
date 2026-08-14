# Arrosage — supervision de capteurs ESP32 (MQTT → TimescaleDB → Dash)

Plateforme auto-hébergée pour superviser un système d'arrosage : les ESP32
publient leurs mesures (niveau d'eau, humidité, température, état des vannes)
sur un broker MQTT, un service les stocke dans PostgreSQL/TimescaleDB, et un
dashboard Dash les affiche. Un service optionnel enrichit le tout avec les
prévisions Météo-France (modèle AROME) et la pluviométrie passée.

## Architecture

| Service       | Rôle                                                             |
|---------------|-------------------------------------------------------------------|
| `mosquitto`   | Broker MQTT (port 1883)                                          |
| `timescaledb` | PostgreSQL + extension TimescaleDB (hypertables, compression)    |
| `ingest`      | Abonné MQTT → écrit les mesures en base                          |
| `dashboard`   | Application Dash (port 8050) : vue d'ensemble, historique, météo |
| `weather`     | Récupère prévisions AROME/ARPEGE et pluvio passée (DPClim)       |

## Démarrage

```bash
cp .env.example .env
# éditer .env : mot de passe Postgres, coordonnées du jardin (WEATHER_LAT/LON), etc.

docker compose up -d --build
```

Le dashboard est ensuite accessible sur http://localhost:8060 (port configurable via
`DASHBOARD_PORT` dans `.env`).

## Stack technique

Les trois services Python (`ingest`, `dashboard`, `weather`) partagent la même
approche :

- **SQLAlchemy** (Core, pas d'ORM) pour tout l'accès base de données — chaque
  service a un module `app/schema.py` qui déclare les tables (miroir de
  `db/init/001_schema.sql`), et `app/db.py`/`app/queries.py` construisent les
  requêtes avec `select()`/`insert()`/`delete()` plutôt qu'en SQL brut.
- **uv** pour la gestion des dépendances Python : chaque service a son
  `pyproject.toml` + `uv.lock` (commité, donc les builds sont reproductibles),
  installées dans l'image via `uv sync --locked`. Pour ajouter une dépendance
  en développement : `cd <service> && uv add <paquet>` puis rebuild l'image.

## Contrat device (MQTT + OTA)

Le firmware réel vit dans un repo séparé (`arrosage_fw`, ESP-IDF/FreeRTOS).
Voir [`esp32/README.md`](esp32/README.md) pour le contrat complet et à jour
(état, commande, OTA). Résumé rapide :

- État (device → serveur) : `arrosage/<device_id>/etat`, retain=true, toutes
  les clés toujours présentes (`water_level_cm`, `humidity_pct`,
  `temperature_c`, `battery_v`, `vanne_1..N`).
- Commande (serveur → device) : `arrosage/<device_id>/commande`, jamais
  retain, `{"vanne": "vanne_1", "action": "open", "duration_s": 600}` /
  `{"vanne": "vanne_1", "action": "close"}` / `{"action": "stop_all"}`.
- OTA (serveur → device, HTTP local, pas MQTT) : `POST http://<ip>/api/ota`
  avec le `.bin` brut — voir onglet **Firmware** du dashboard plus bas.

Test rapide sans matériel :

```bash
mosquitto_pub -h localhost -p 1883 -t arrosage/test-device/etat -m \
  '{"ts":"2026-07-16T10:00:00Z","water_level_cm":34.5,"humidity_pct":62.1,"temperature_c":21.3,"battery_v":0.0,"vanne_1":"open","vanne_2":"closed","vanne_3":"closed"}'
```

## Base de données

Le schéma (`db/init/*.sql`, exécutés dans l'ordre alphabétique) est appliqué
automatiquement au premier démarrage du conteneur `timescaledb` — sur un
volume déjà initialisé, un nouveau fichier `00N_*.sql` ne s'exécute pas tout
seul, il faut l'appliquer à la main (`docker compose exec -T timescaledb
psql -U arrosage -d arrosage -f - < db/init/00N_fichier.sql`). Il crée :

- `devices` : appareils connus (auto-alimentée par `ingest`), avec
  `ip_address` (IP locale fixe, saisie depuis l'onglet Firmware du
  dashboard, utilisée pour l'OTA),
- `sensor_readings` : hypertable des mesures (une ligne par métrique),
  compressée au-delà de 7 jours, avec un agrégat continu horaire
  (`sensor_readings_hourly`) utilisé par le dashboard pour les longues
  périodes,
- `weather_observed` : hypertable des observations passées (pluvio/température),
- `weather_forecast` : table classique (pas un historique) — voir plus bas.

Pour ajouter un nom lisible ou des coordonnées à un appareil :

```sql
UPDATE devices SET name = 'Serre nord', lat = 45.19, lon = 5.72
WHERE device_id = 'jardin-1';
```

## Service météo (Open-Meteo — AROME / ARPEGE)

Gratuit, **sans clé API ni compte** pour un usage non-commercial :
[Open-Meteo](https://open-meteo.com) utilise réellement les modèles
Météo-France (AROME, ARPEGE) en interne, exposés en JSON simple. Le service
`weather` fonctionne donc dès le démarrage, sans configuration
supplémentaire au-delà de `WEATHER_LAT`/`WEATHER_LON` (coordonnées du
jardin).

> Historique : ce projet utilisait initialement l'API officielle du portail
> Météo-France (GRIB2/WCS + DPClim), qui nécessitait un compte et une clé
> par produit. Abandonné au profit d'Open-Meteo suite à un souci de compte,
> et parce que c'est plus simple à opérer (pas de dépendance eccodes/cfgrib,
> pas de recherche de station DPClim).

### Fonctionnement

- Un seul appel HTTP par cycle (toutes les 3h) récupère à la fois le passé
  récent (7 jours) et la prévision (4 jours) pour AROME et ARPEGE.
- `weather_forecast` est un **instantané, pas un historique** : à chaque
  cycle, les lignes de la source concernée (AROME ou ARPEGE) sont
  entièrement remplacées par la nouvelle prévision.
- `weather_observed` (passé récent) est mis à jour par upsert (idempotent).
- La précipitation horaire renvoyée par Open-Meteo est la pluie tombée
  **pendant l'heure précédente** (pas un cumul depuis le début du run) : le
  dashboard peut donc simplement sommer sur une fenêtre plutôt que calculer
  une différence entre échéances.
- AROME (plus fin, ~48h) et ARPEGE (plus large, jusqu'à J+4) se chevauchent
  sur les échéances proches ; le dashboard privilégie AROME sur cette plage
  pour l'indicateur pluie (voir `dashboard/app/queries.py::get_rain_outlook`)
  et affiche les deux sources comme des courbes séparées dans l'onglet
  Météo.

### Débogage

```bash
docker compose logs -f weather
```

## Dashboard

Un panneau de vannes persistant en haut de page (visible quel que soit
l'onglet actif), avec pour chaque vanne détectée un bouton **Ouvrir**
(avec une durée : 5/10/15/30/60 min) et **Fermer**. Le dashboard publie la
commande sur MQTT (voir [`esp32/README.md`](esp32/README.md) > "Commande des
vannes") ; c'est le firmware qui gère le minuteur d'auto-fermeture en local
(robuste à une coupure réseau). Le badge ouverte/fermée reflète toujours
l'état réel rapporté par l'ESP32 (`.../etat`), pas la commande envoyée — en
cas de défaut matériel, badge et commande peuvent diverger, ce qui est
volontaire (on voit l'état réel, pas ce qu'on a demandé).

Puis quatre onglets :

- **Vue d'ensemble** : jauge de niveau de la cuve (seuil "pleine" configurable
  via `TANK_HEIGHT_FULL_CM`), indicateurs de pluie prévue (3h / 2 jours), et
  une carte par appareil avec ses dernières valeurs connues. Rafraîchi
  toutes les 15s.
- **Historique** : sélection d'appareils/métriques + plage de dates
  (utilise automatiquement l'agrégat horaire au-delà de 2 jours pour rester
  rapide). Les vannes s'affichent séparément des courbes de capteurs, en
  carrés rouge (fermée) / vert (ouverte) sur une piste synchronisée.
- **Météo** : prévisions AROME/ARPEGE, cumul de pluie passé, et une carte
  radar interactive (widget [Windy](https://www.windy.com), gratuit, sans
  clé API — centrée sur `WEATHER_LAT`/`WEATHER_LON`).
- **Firmware** : mise à jour OTA par device. Renseigner une fois l'IP locale
  fixe de chaque device (bouton "Enregistrer", stockée dans
  `devices.ip_address`), puis choisir un fichier `.bin` et cliquer
  "Envoyer" — une confirmation s'affiche avant l'envoi effectif (le device va
  redémarrer). Le dashboard relaie le fichier en `POST` vers
  `http://<ip>/api/ota` (voir [`esp32/README.md`](esp32/README.md) > "Mise à
  jour firmware"). Un seul envoi à la fois par device (bouton désactivé
  pendant l'upload).

## Sécurité

Par défaut, Mosquitto accepte les connexions anonymes (adapté à un réseau
local de confiance). Pour ajouter une authentification, voir les
commentaires dans `mosquitto/config/mosquitto.conf`.
