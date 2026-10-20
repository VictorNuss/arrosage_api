# Contrat device attendu par le backend (MQTT + OTA)

> **Le firmware réel vit maintenant dans le repo séparé `arrosage_fw`**
> (ESP-IDF/FreeRTOS). Le sketch Arduino de ce dossier
> ([`exemple_sketch/exemple_sketch.ino`](exemple_sketch/exemple_sketch.ino))
> est un premier brouillon conservé à titre illustratif — **son contrat de
> commande est périmé** (ancien format, retenu) par rapport à ce qui suit,
> qui est la référence actuellement en production.

## État (device → serveur)

Un topic **par mesure/vanne** plutôt qu'un JSON combiné :

```
arrosage/<device_id>/etat/<key>
```

`<device_id>` est un identifiant stable et unique par appareil (ex:
`jardin-1`). Il sert de clé pour la table `devices` et apparaît tel quel
dans le dashboard tant qu'aucun nom n'est renseigné en base. `<key>` est un
des noms de mesure (`water_distance_cm`, `humidity_pct`, `temperature_c`,
`battery_v`) ou de vanne (`vanne_1`, `vanne_2`, `vanne_3`, ...).

QoS 1, **retain=true sur chaque sous-topic**, publié **uniquement quand une
donnée réelle et fraîche existe** — pas de cycle périodique, pas de valeur
bidon. Un capteur jamais lu avec succès depuis le boot du device ne publie
simplement rien sur son topic. Les clés capteurs dépendent du matériel
installé (menuconfig du firmware) : aucune n'est obligatoire.

`water_distance_cm` est la distance **brute** capteur ultrason → surface de
l'eau : le device ne connaît pas la cuve. `ingest` la convertit en
`water_level_cm` (hauteur d'eau, réglages `TANK_DISTANCE_EMPTY_CM` /
`TANK_DISTANCE_FULL_CM`) et republie ce niveau sur
`arrosage/<device_id>/etat/water_level_cm` — un device ne doit pas publier
`water_level_cm` lui-même.

Payload capteur :
```json
{"value": 34.5}
```
Payload vanne :
```json
{"state": "open"}
```
Les électrovannes sont motorisées (condensateur de démarrage) : ni
l'ouverture ni la fermeture ne sont instantanées, le moteur met un temps
variable (~15s par défaut, configurable côté firmware, **pas figé**) à
actionner réellement le passage d'eau — **dans les deux sens**. Séquence
exacte pour une commande `open` ou `close` :

1. Le device publie immédiatement `{"state": "transitioning"}` dès qu'il
   reçoit la commande (le GPIO a basculé, le moteur n'a pas fini son
   mouvement).
2. Une fois le mouvement terminé (délai variable), le device publie l'état
   final : `{"state": "open"}` ou `{"state": "closed"}` selon la commande.

```json
{"state": "transitioning"}
```

Règles :
- Pas de champ `ts` : le backend utilise l'horodatage de réception MQTT.
- **L'absence d'un message pour une clé ne veut pas dire "capteur en
  panne"** : ça veut juste dire "rien de neuf depuis la dernière valeur
  connue". Le `retain` du broker redonne la dernière valeur connue à un
  abonné qui (re)démarre ou se reconnecte.
- Les métriques de vannes contiennent `vanne` dans leur nom, valeur
  `"open"`/`"closed"`/`"transitioning"` (chaînes) dans le champ `state`
  (aussi acceptés en synonymes : `"on"`/`"off"`, `"transition"`,
  `"moving"`, `"opening"`, `"closing"` — ces trois derniers sont traités
  comme une simple transition, le backend ne s'appuie pas dessus pour
  connaître le sens : voir plus bas).
- Le backend ne connaît pas le sens de la transition à partir du seul
  champ `state` (`"transitioning"` ne le précise pas) : le dashboard déduit
  "ouverture"/"fermeture" à partir du dernier état stable connu avant la
  transition. C'est purement indicatif pour l'affichage ; si le firmware
  peut publier directement `"opening"`/`"closing"` plutôt qu'un
  `"transitioning"` générique, c'est plus fiable et le backend les
  reconnaît déjà tous les deux.
- **`"transitioning"` est un état normal, pas une erreur** : le backend
  n'impose aucun délai fixe avant de considérer qu'une commande a "échoué"
  faute d'état final — il affiche `"transitioning"` tel quel tant qu'il ne
  reçoit rien d'autre. `get_status` peut lui aussi renvoyer
  `"transitioning"` si une commande est en cours au moment de la requête.
- Suffixes de nom reconnus par le dashboard pour l'unité affichée : `_cm`,
  `_mm`, `_pct`, `_c` (°C), `_v` (V).
- Le backend s'abonne avec le wildcard `arrosage/+/etat/#` (un seul niveau
  après `etat` est attendu, mais `#` reste plus tolérant que `+` si jamais
  ça évolue).

### Clé spéciale `ip`

Topic `arrosage/<device_id>/etat/ip`, payload `{"value": "192.168.1.50"}`.
Contrairement aux autres clés, la valeur est une **chaîne** (l'IP locale
fixe du device, pas de DHCP — configurée à la fabrication/au flashage), pas
un nombre : elle alimente `devices.ip_address` côté base, pas
`sensor_readings`. Utilisée pour cibler automatiquement le bon device lors
d'une mise à jour OTA (voir plus bas), sans mapping tenu à la main dans le
dashboard.

Règles :
- Publiée par le device **automatiquement à chaque (re)connexion MQTT**,
  et en réponse à un `get_status` — mêmes règles que les autres clés côté
  fraîcheur (retain=true, rien publié tant que l'IP réelle n'est pas
  connue, pas de valeur par défaut).
- Cette IP étant fixe, elle ne change en principe jamais pour un
  `device_id` donné une fois connue — mais rien ne garantit qu'elle soit
  déjà connue (device jamais vu depuis que le backend écoute) : le
  dashboard grise le bouton de mise à jour OTA tant qu'elle est absente,
  et permet de forcer un `get_status` pour redemander sa republication.

### Clé spéciale `fw_version`

Topic `arrosage/<device_id>/etat/fw_version`, payload `{"value": "v1.2.0"}`.
Comme `ip` : une **chaîne** (version du firmware en cours d'exécution,
`esp_app_get_description()->version` côté ESP-IDF — tag/`git describe` au
moment du build), publiée aux mêmes moments (chaque connexion MQTT +
`get_status`, retain=true). Elle alimente `devices.fw_version`, pas
`sensor_readings`. Le dashboard l'affiche dans l'onglet Firmware et dans la
confirmation d'envoi ; après un OTA réussi, le device redémarre et republie
sa nouvelle version, ce qui permet de vérifier que la mise à jour a bien été
appliquée (et pas annulée par le rollback). Chaîne non vide, 64 caractères
imprimables au plus — sinon le message est ignoré par `ingest`.

## Commande des vannes (serveur → device)

Topic : `arrosage/<device_id>/commande`, QoS 1, **jamais retain**.

```json
{"vanne": "vanne_1", "action": "open", "duration_s": 600}
```
```json
{"vanne": "vanne_2", "action": "close"}
```
```json
{"action": "stop_all"}
```
```json
{"action": "get_status"}
```

Règles :
- `action: "open"` + `duration_s` → le **firmware** gère localement le
  minuteur d'auto-fermeture (robuste à une coupure réseau/dashboard après
  l'envoi de la commande). Sur une vanne **déjà ouverte ou en cours
  d'ouverture**, `open` ne rejoue pas la transition (rien ne bouge, aucun
  état republié) : `duration_s` s'**ajoute** au temps restant
  ("extra-time"), le total restant borné par la durée max de la vanne.
  C'est le bouton "Prolonger" du dashboard ; un programme qui se déclenche
  sur une vanne déjà ouverte la prolonge de la même façon.
- `action: "close"` → ferme immédiatement, annule le minuteur en cours pour
  cette vanne.
- `action: "stop_all"` → ferme toutes les vannes immédiatement (pas de champ
  `vanne`). Pas encore de bouton dédié dans le dashboard actuel, mais le
  firmware doit le supporter.
- `action: "get_status"` → demande au device de republier son état complet
  connu : l'état de toutes ses vannes (toujours connues), et la dernière
  valeur connue de chaque capteur **déjà lu au moins une fois avec succès**
  depuis le boot (un capteur jamais lu reste absent de la réponse — même
  logique qu'au repos). Utilisé par le backend pour se resynchroniser (ex:
  après un redémarrage qui a perdu son cache d'état), en plus des messages
  retenus automatiquement republiés par le broker à l'abonnement.
- Le message n'est **jamais retenu** : un redémarrage du device ne rejoue
  aucune commande passée (pas de risque de commande périmée à gérer).
- Après toute action sur une vanne (commande ou fin de minuteur), le
  firmware republie immédiatement son état (`.../etat/<vanne>`) plutôt que
  d'attendre une prochaine lecture, pour que le dashboard reflète le
  changement rapidement.

## Mise à jour firmware (OTA), en HTTP local — pas de MQTT

Chaque device a une IP locale fixe (configurée au flashage, publiée par le
device lui-même via la clé MQTT `ip`, voir README principal). Il héberge un
serveur HTTP local :

```
POST http://<ip_fixe_du_device>/api/ota
Content-Type: application/octet-stream
Corps : le .bin brut, tel quel (pas de multipart, pas de JSON)
```

- `200 OK` + texte (ex: `"OK, redemarrage en cours..."`) : image acceptée,
  le device redémarre dessus.
- `400`/`500` + texte d'erreur : image rejetée, l'ancien firmware reste
  actif (rollback automatique côté device, aucun risque de brick).
- Pas d'authentification côté device : réseau local de confiance
  uniquement. Le dashboard relaie le fichier tel quel depuis son propre
  serveur (le navigateur ne parle jamais directement au device).

Test manuel :

```bash
curl -X POST --data-binary @arrosage_fw.bin http://192.168.1.50/api/ota
```

### Tester sans matériel : device simulé

[`device_simulator.py`](device_simulator.py) simule un device complet en
respectant ce contrat : publication de `ip`/`fw_version` à la connexion,
capteurs à chaque "lecture" (la cuve baisse et le sol s'humidifie quand une
vanne est ouverte), vannes à 3 états avec fermeture automatique (mêmes
durées max que `valve_config.cpp`), commandes `open`/`close`/`stop_all`/
`get_status`, et endpoint `POST /api/ota`.

Comme le vrai device, l'OTA n'accepte qu'une **vraie image ESP-IDF** : la
version est lue dans le descripteur d'application du `.bin`, puis le device
"redémarre" (déconnexion MQTT, vannes fermées) et republie cette nouvelle
version. Pour tester, envoyer le `build/arrosage_fw.bin` du repo firmware
(`idf.py build`) ; tout autre fichier est refusé (`400`), ce qui teste le
chemin d'erreur du dashboard. `GET /api/state` renvoie un instantané JSON.

Il tourne dans la stack Docker (profil `sim`, désactivé par défaut) : son
IP de conteneur est joignable par le dashboard, sur le port 80 qu'il
utilise toujours (il n'accepte qu'une IPv4 nue, sans `:port`).

```bash
docker compose --profile sim up -d --build     # sim-jardin + sim-serre
docker compose --profile sim logs -f device-sim-jardin
```

Chaque device simulé est un service de `docker-compose.yml` avec un
`SIM_DEVICE_ID` fixe (stable d'un rebuild à l'autre) ; pour en ajouter un,
copier le bloc `device-sim-serre` en changeant son nom et son
`SIM_DEVICE_ID`. Autres réglages par variable d'environnement ou option : `SIM_FW_VERSION` (`v0.0.0-sim`),
`SIM_TRANSITION_DELAY_S` (15), `SIM_SENSOR_PERIOD_S` (10),
`SIM_REBOOT_DELAY_S` (3) — voir `python esp32/device_simulator.py --help`.

Tests du simulateur :

```bash
uv run --no-project --python 3.12 --with paho-mqtt==2.1.0 --with pytest pytest esp32 -q
```

## Tester le contrat MQTT sans matériel

Avec le broker Mosquitto du projet démarré (`docker compose up -d mosquitto`),
simuler un appareil avec `mosquitto_pub` (un topic par mesure, retain=true) :

```bash
mosquitto_pub -h localhost -p 1883 -r -t arrosage/test-device/etat/water_distance_cm -m '{"value":80.5}'
mosquitto_pub -h localhost -p 1883 -r -t arrosage/test-device/etat/temperature_c -m '{"value":21.3}'
mosquitto_pub -h localhost -p 1883 -r -t arrosage/test-device/etat/vanne_1 -m '{"state":"open"}'
```

Et écouter les commandes envoyées par le dashboard (simule ce que ferait le
firmware) :

```bash
mosquitto_sub -h localhost -p 1883 -t arrosage/test-device/commande -v
```
