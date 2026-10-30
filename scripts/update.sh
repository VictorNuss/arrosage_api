#!/usr/bin/env bash
# Mise à jour du serveur sans perte de données : sauvegarde de la base,
# récupération du code, nouveaux scripts SQL, reconstruction des images,
# redémarrage. Voir docs/mise-a-jour.md.
#
# Usage (depuis n'importe où, sur le serveur) : scripts/update.sh
#   BRANCH=main        branche à déployer
#   KEEP_BACKUPS=10    nombre de sauvegardes conservées dans backups/
set -euo pipefail

cd "$(dirname "$0")/.."

BRANCH="${BRANCH:-main}"
KEEP_BACKUPS="${KEEP_BACKUPS:-10}"
BACKUP_DIR="backups"
COMPOSE=(docker compose)

step() { printf '\n==> %s\n' "$*"; }
die() { printf '\nERREUR : %s\n' "$*" >&2; exit 1; }

# psql/pg_dump dans le conteneur, avec l'utilisateur et la base de son
# environnement (ceux de .env) : rien à dupliquer ici.
db_sh() { "${COMPOSE[@]}" exec -T timescaledb sh -c "$1"; }

# --- 1. Code à déployer ---------------------------------------------------

step "Vérification du dépôt"
if ! git diff --quiet || ! git diff --cached --quiet; then
    die "modifications locales non commitées (git status) : à régler avant la mise à jour."
fi
[ -f .env ] || die ".env introuvable (copier .env.example)."

git fetch --quiet origin "$BRANCH"
OLD=$(git rev-parse HEAD)
NEW=$(git rev-parse "origin/$BRANCH")

if [ "$OLD" = "$NEW" ]; then
    echo "Code déjà à jour ($(git log -1 --format='%h %s'))."
    NEW_SQL=()
else
    echo "Nouveaux commits :"
    git log --oneline "$OLD..$NEW"

    # Pas de migrations dans ce projet : db/init n'est exécuté que sur une
    # base vide. Un nouveau fichier SQL (idempotent, CREATE ... IF NOT EXISTS)
    # s'applique sans risque ; un fichier existant modifié veut dire un
    # changement de schéma à faire à la main (ou base à recréer).
    CHANGED_SQL=$(git diff --name-only --diff-filter=MDR "$OLD" "$NEW" -- db/init)
    if [ -n "$CHANGED_SQL" ]; then
        die "scripts SQL existants modifiés, rien n'a été changé sur le serveur :
$CHANGED_SQL
Voir docs/mise-a-jour.md > \"Changement de schéma\"."
    fi
    mapfile -t NEW_SQL < <(git diff --name-only --diff-filter=A "$OLD" "$NEW" -- 'db/init/*.sql' | sort)
fi

# --- 2. Sauvegarde ----------------------------------------------------------

step "Sauvegarde de la base"
"${COMPOSE[@]}" up -d --wait timescaledb
mkdir -p "$BACKUP_DIR"
BACKUP="$BACKUP_DIR/arrosage_$(date +%Y%m%d_%H%M%S)_${OLD:0:7}.dump"
db_sh 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc' > "$BACKUP" || {
    rm -f "$BACKUP"
    die "échec de la sauvegarde, mise à jour annulée."
}
[ -s "$BACKUP" ] || { rm -f "$BACKUP"; die "sauvegarde vide, mise à jour annulée."; }
echo "Sauvegarde : $BACKUP ($(du -h "$BACKUP" | cut -f1))"
# Rotation : ne garde que les KEEP_BACKUPS plus récentes.
ls -1t "$BACKUP_DIR"/arrosage_*.dump | tail -n +"$((KEEP_BACKUPS + 1))" | xargs -r rm -f

# --- 3. Code + images -------------------------------------------------------

if [ "$OLD" != "$NEW" ]; then
    step "Récupération du code"
    git merge --ff-only --quiet "$NEW" || die "impossible d'avancer en fast-forward (historique local divergent)."
fi

# Construites avant de toucher aux conteneurs : les anciens tournent
# pendant le build, la coupure se limite au redémarrage.
step "Construction des images"
"${COMPOSE[@]}" build

# --- 4. Schéma ---------------------------------------------------------------

if [ "${#NEW_SQL[@]}" -gt 0 ]; then
    step "Nouveaux scripts SQL"
    for file in "${NEW_SQL[@]}"; do
        echo "-> $file"
        db_sh 'psql -v ON_ERROR_STOP=1 -q -U "$POSTGRES_USER" -d "$POSTGRES_DB"' < "$file" ||
            die "échec de $file. Code mis à jour mais conteneurs pas redémarrés ; sauvegarde : $BACKUP"
    done
fi

# --- 5. Redémarrage ----------------------------------------------------------

step "Redémarrage des services"
"${COMPOSE[@]}" up -d --wait

step "État"
"${COMPOSE[@]}" ps
# Anciennes images sans tag laissées par le build (jamais les volumes).
docker image prune -f > /dev/null

echo
echo "Mise à jour terminée : $(git log -1 --format='%h %s')"
echo "Sauvegarde avant mise à jour : $BACKUP"
