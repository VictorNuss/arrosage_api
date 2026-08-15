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
des noms de mesure (`water_level_cm`, `humidity_pct`, `temperature_c`,
`battery_v`) ou de vanne (`vanne_1`, `vanne_2`, `vanne_3`, ...).

QoS 1, **retain=true sur chaque sous-topic**, publié **uniquement quand une
donnée réelle et fraîche existe** — pas de cycle périodique, pas de valeur
bidon. Un capteur jamais lu avec succès depuis le boot du device ne publie
simplement rien sur son topic.

Payload capteur :
```json
{"value": 34.5}
```
Payload vanne :
```json
{"state": "open"}
```

Règles :
- Pas de champ `ts` : le backend utilise l'horodatage de réception MQTT.
- **L'absence d'un message pour une clé ne veut pas dire "capteur en
  panne"** : ça veut juste dire "rien de neuf depuis la dernière valeur
  connue". Le `retain` du broker redonne la dernière valeur connue à un
  abonné qui (re)démarre ou se reconnecte.
- Les métriques de vannes contiennent `vanne` dans leur nom, valeur
  `"open"`/`"closed"` (chaînes) dans le champ `state`.
- Suffixes de nom reconnus par le dashboard pour l'unité affichée : `_cm`,
  `_mm`, `_pct`, `_c` (°C), `_v` (V).
- Le backend s'abonne avec le wildcard `arrosage/+/etat/#` (un seul niveau
  après `etat` est attendu, mais `#` reste plus tolérant que `+` si jamais
  ça évolue).

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
  l'envoi de la commande).
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

Chaque device a une IP locale fixe (configurée au flashage, saisie une fois
dans le dashboard, onglet "Firmware"). Il héberge un serveur HTTP local :

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

### Tester l'OTA sans matériel

[`mock_ota_server.py`](mock_ota_server.py) simule cet endpoint (bibliothèque
standard uniquement, tourne directement sur l'hôte, pas besoin de Docker) :

```bash
python esp32/mock_ota_server.py 8090
```

Il accepte n'importe quel corps non vide (`200 OK, redemarrage en
cours...`), sauf s'il commence par `FAIL` (`400`, pratique pour tester le
chemin d'erreur du dashboard). Un délai de 2s est simulé avant la réponse.
Dans le dashboard, renseigner `<IP de cette machine>:8090` comme IP du
device dans l'onglet Firmware pour tester le relais de bout en bout.

## Tester le contrat MQTT sans matériel

Avec le broker Mosquitto du projet démarré (`docker compose up -d mosquitto`),
simuler un appareil avec `mosquitto_pub` (un topic par mesure, retain=true) :

```bash
mosquitto_pub -h localhost -p 1883 -r -t arrosage/test-device/etat/water_level_cm -m '{"value":34.5}'
mosquitto_pub -h localhost -p 1883 -r -t arrosage/test-device/etat/temperature_c -m '{"value":21.3}'
mosquitto_pub -h localhost -p 1883 -r -t arrosage/test-device/etat/vanne_1 -m '{"state":"open"}'
```

Et écouter les commandes envoyées par le dashboard (simule ce que ferait le
firmware) :

```bash
mosquitto_sub -h localhost -p 1883 -t arrosage/test-device/commande -v
```
