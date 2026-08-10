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

## Service météo (AROME / ARPEGE / DPClim)

Ce service est optionnel : sans clé API, il ne fait rien et le dashboard
affiche simplement "service météo non configuré".

### Obtenir une clé API

1. Créer un compte sur https://portail-api.meteofrance.fr
2. Souscrire aux API **AROME**, **ARPEGE** et **DPClim (Données Publiques
   Climatologiques)** depuis le portail (souscriptions gratuites,
   individuelles par API).
3. Générer une clé applicative (apikey) et la mettre dans `.env` :
   `METEOFRANCE_API_KEY=...`

### Coordonnées et station

- `WEATHER_LAT` / `WEATHER_LON` : coordonnées du jardin, utilisées pour
  extraire le point de grille le plus proche dans les prévisions AROME/ARPEGE.
- `METEOFRANCE_STATION_ID` : identifiant de la station Météo-France la plus
  proche pour la pluviométrie passée (API DPClim). Pour la trouver :

  ```bash
  curl -H "apikey: VOTRE_CLE" \
    "https://public-api.meteofrance.fr/public/DPClim/v1/liste-stations/quotidienne?id-departement=38"
  ```

  (remplacer `38` par le numéro de département) et repérer la station la
  plus proche de vos coordonnées dans la réponse JSON.

### Fonctionnement

- Prévisions (AROME ~42h, complétées par ARPEGE jusqu'à J+4) : rafraîchies
  toutes les 3h. `weather_forecast` est un **instantané, pas un historique** :
  à chaque cycle, les lignes de la source concernée (AROME ou ARPEGE) sont
  entièrement remplacées par la nouvelle prévision (une prévision périmée n'a
  pas de valeur une fois la suivante disponible, et ça évite de faire grossir
  la base indéfiniment). Si vous voulez comparer prévision vs réalité dans le
  temps, il faudra dupliquer les lignes vers une table d'historique séparée
  avant le remplacement (non fait par défaut).
- Pluviométrie/température passées (DPClim) : rafraîchies 1x/jour, stockées
  dans `weather_observed`.
- Les identifiants de ressource WCS (AROME/ARPEGE) sont découverts
  dynamiquement via `GetCapabilities` à chaque cycle plutôt que codés en dur,
  car ils incluent l'horodatage du run et changent en permanence. Si
  Météo-France change sa nomenclature de paramètres, ajustez
  `weather/app/config.py::PARAMETER_KEYWORDS` — les logs du service listent
  les CoverageId reçus quand aucune correspondance n'est trouvée.

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
- **Météo** : prévisions AROME/ARPEGE et cumul de pluie passé.
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
