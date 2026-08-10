# Contrat device attendu par le backend (MQTT + OTA)

> **Le firmware réel vit maintenant dans le repo séparé `arrosage_fw`**
> (ESP-IDF/FreeRTOS). Le sketch Arduino de ce dossier
> ([`exemple_sketch/exemple_sketch.ino`](exemple_sketch/exemple_sketch.ino))
> est un premier brouillon conservé à titre illustratif — **son contrat de
> commande est périmé** (ancien format, retenu) par rapport à ce qui suit,
> qui est la référence actuellement en production.

## État (device → serveur)

Topic : `arrosage/<device_id>/etat`, QoS 1, **retain=true**, publié toutes
les ~60s.

`<device_id>` est un identifiant stable et unique par appareil (ex:
`jardin-1`). Il sert de clé pour la table `devices` et apparaît tel quel
dans le dashboard tant qu'aucun nom n'est renseigné en base.

```json
{
  "ts": "2026-07-16T10:00:00Z",
  "water_level_cm": 34.5,
  "humidity_pct": 62.1,
  "temperature_c": 21.3,
  "battery_v": 3.98,
  "vanne_1": "open",
  "vanne_2": "closed",
  "vanne_3": "closed"
}
```

Règles :
- Toutes les clés sont **toujours présentes** dans chaque message (le
  firmware n'omet jamais un champ, même si la valeur n'a pas encore de
  lecture fiable).
- `battery_v` vaut toujours `0.0` (alimentation secteur, pas de batterie sur
  ce modèle) — pas un vrai capteur pour l'instant.
- `water_level_cm` peut valoir `0.0` avant la première lecture réelle du
  capteur : ne pas interpréter ça comme "cuve vide" dans une future
  évolution du dashboard (actuellement le dashboard ne fait pas cette
  distinction, à garder en tête si on l'affine).
- Les métriques de vannes contiennent `vanne` dans leur nom, valeur
  `"open"`/`"closed"` (chaînes).
- Suffixes de nom reconnus par le dashboard pour l'unité affichée : `_cm`,
  `_mm`, `_pct`, `_c` (°C), `_v` (V).

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

Règles :
- `action: "open"` + `duration_s` → le **firmware** gère localement le
  minuteur d'auto-fermeture (robuste à une coupure réseau/dashboard après
  l'envoi de la commande).
- `action: "close"` → ferme immédiatement, annule le minuteur en cours pour
  cette vanne.
- `action: "stop_all"` → ferme toutes les vannes immédiatement (pas de champ
  `vanne`). Pas encore de bouton dédié dans le dashboard actuel, mais le
  firmware doit le supporter.
- Le message n'est **jamais retenu** : un redémarrage du device ne rejoue
  aucune commande passée (pas de risque de commande périmée à gérer).
- Après toute action sur une vanne (commande ou fin de minuteur), le
  firmware republie immédiatement un état complet sur `.../etat` plutôt que
  d'attendre le prochain cycle périodique, pour que le dashboard reflète le
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
simuler un appareil avec `mosquitto_pub` :

```bash
mosquitto_pub -h localhost -p 1883 -t arrosage/test-device/etat -m \
  '{"ts":"2026-07-16T10:00:00Z","water_level_cm":34.5,"humidity_pct":62.1,"temperature_c":21.3,"battery_v":0.0,"vanne_1":"open","vanne_2":"closed","vanne_3":"closed"}'
```

Et écouter les commandes envoyées par le dashboard (simule ce que ferait le
firmware) :

```bash
mosquitto_sub -h localhost -p 1883 -t arrosage/test-device/commande -v
```
