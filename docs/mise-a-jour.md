# Mettre à jour le serveur sans perdre les données

## Où sont les données

Toutes les données vivent dans des **volumes Docker nommés**, pas dans les
conteneurs :

| Volume | Contenu |
|---|---|
| `<projet>_timescaledb_data` | La base : mesures, programmes, zones, historique |
| `<projet>_mosquitto_data` | Messages MQTT retenus (dernier état de chaque device) |
| `<projet>_mosquitto_log` | Logs du broker |

`<projet>` est le nom du dossier du dépôt (`arrosage_api`). Reconstruire ou
recréer les conteneurs (`docker compose up -d --build`, `down`, `restart`)
**ne touche pas aux volumes**.

Ce qui efface les données — à ne jamais lancer sur le serveur sans
sauvegarde :

- `docker compose down -v` (le `-v` supprime les volumes),
- `docker volume rm ...`, `docker volume prune`, `docker system prune --volumes`,
- renommer ou déplacer le dossier du dépôt : le nom de projet change, Docker
  crée des volumes neufs et vides (les anciens existent toujours, mais ne
  sont plus utilisés). Pour garder le nom, ajouter `COMPOSE_PROJECT_NAME=arrosage_api`
  dans `.env`.

## Mise à jour normale

Sur le serveur, dans le dossier du dépôt :

```bash
scripts/update.sh
```

Le script :

1. refuse de continuer s'il y a des modifications locales non commitées ;
2. récupère les nouveaux commits de `main` (sans encore les appliquer) et
   s'arrête si un script SQL **existant** a été modifié (voir plus bas) ;
3. **sauvegarde la base** dans `backups/` (`pg_dump`, 10 dernières
   conservées) ;
4. avance le code, puis construit les images pendant que les anciens
   conteneurs tournent encore ;
5. applique les **nouveaux** fichiers `db/init/*.sql` (ils sont écrits pour
   pouvoir s'appliquer sur une base existante : `CREATE ... IF NOT EXISTS`) ;
6. redémarre les services et attend qu'ils soient prêts.

Profils optionnels (Zigbee...) : les déclarer une fois pour toutes dans
`.env`, ils seront pris en compte par le script comme par `docker compose` :

```bash
COMPOSE_PROFILES=zigbee
```

Après une mise à jour qui ajoute des variables dans `.env.example`, les
reporter dans `.env` (comparer avec `diff .env.example .env`).

## Changement de schéma

Les scripts `db/init` ne s'exécutent qu'au premier démarrage, sur une base
vide : il n'y a pas d'outil de migration. Quand une mise à jour **modifie**
un script existant (au lieu d'en ajouter un), `scripts/update.sh` s'arrête
avant de toucher à quoi que ce soit. Deux options :

- écrire et appliquer à la main le SQL qui fait passer la base existante au
  nouveau schéma (`ALTER TABLE ...`), puis relancer le script ;
- ou, si les données n'ont pas d'importance (phase de test), repartir d'une
  base vide : `docker compose down -v`, puis `docker compose up -d --build`
  (une sauvegarde reste utile, voir ci-dessous).

## Sauvegarder et restaurer

Sauvegarde manuelle (le script en fait une à chaque mise à jour) :

```bash
mkdir -p backups
docker compose exec -T timescaledb sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc' > backups/arrosage_$(date +%Y%m%d_%H%M%S).dump
```

Les sauvegardes restent sur le serveur : en copier régulièrement une
ailleurs (`scp`, disque externe...), sinon une panne du disque emporte la
base et ses sauvegardes.

Restauration (remplace **toute** la base par la sauvegarde) :

```bash
DUMP=backups/arrosage_XXXXXXXX_XXXXXX.dump
docker compose stop ingest dashboard scheduler weather
docker compose exec -T timescaledb sh -c 'dropdb -U "$POSTGRES_USER" "$POSTGRES_DB" && createdb -U "$POSTGRES_USER" "$POSTGRES_DB"'
docker compose exec -T timescaledb sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "CREATE EXTENSION IF NOT EXISTS timescaledb; SELECT timescaledb_pre_restore();"'
docker compose exec -T timescaledb sh -c 'pg_restore -U "$POSTGRES_USER" -d "$POSTGRES_DB" --no-owner' < "$DUMP"
docker compose exec -T timescaledb sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "SELECT timescaledb_post_restore();"'
docker compose start ingest dashboard scheduler weather
```

`pg_restore` affiche quelques avertissements propres à TimescaleDB (clés
étrangères circulaires des tables internes) : c'est normal. La restauration
exige **la même version de TimescaleDB** que celle qui a fait la sauvegarde.

## Mettre à jour TimescaleDB ou Mosquitto

`scripts/update.sh` ne télécharge pas de nouvelle version des images
officielles (`timescaledb`, `mosquitto`) : elles ne changent que sur
`docker compose pull`. Pour TimescaleDB, c'est une opération à part :

1. sauvegarde (ci-dessus) ;
2. `docker compose pull timescaledb && docker compose up -d timescaledb` ;
3. mettre à jour l'extension dans la base :
   ```bash
   docker compose exec timescaledb sh -c 'psql -X -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "ALTER EXTENSION timescaledb UPDATE;"'
   ```

Changer de version **majeure de PostgreSQL** (`-pg16` → `-pg17`) ne se fait
pas comme ça : le format des données change, il faut une sauvegarde puis
une restauration dans une base neuve.
