#!/usr/bin/env bash
# Sauvegarde quotidienne des bases n8n et prospecting.
# Cron (root, sur le VPS) :
#   15 3 * * * /opt/prospecting/scripts/backup.sh >> /var/log/prospecting-backup.log 2>&1
set -euo pipefail

cd "$(dirname "$0")/.."
set -a; source .env; set +a

RETENTION_DAYS=14
stamp="$(date +%Y-%m-%d_%H%M)"
dir="backups/$stamp"
mkdir -p "$dir"
chmod 700 backups "$dir"

for db in prospecting n8n; do
  docker compose exec -T postgres pg_dump -U postgres --format=custom "$db" > "$dir/$db.dump"
  # Un dump vide signale un échec silencieux : on arrête tout.
  test -s "$dir/$db.dump"
done
# La clé de chiffrement n8n n'est pas dans la base : la sauvegarder avec .env, hors de ce dossier.
cp docker-compose.yml "$dir/"

find backups -mindepth 1 -maxdepth 1 -type d -mtime +"$RETENTION_DAYS" -exec rm -rf {} +

if [[ -n "${RESTIC_REPOSITORY:-}" ]]; then
  # restic chiffre côté VPS avant l'envoi : l'hébergeur du stockage ne voit rien en clair.
  restic backup backups/"$stamp" --tag prospecting
  restic forget --tag prospecting --keep-daily 14 --keep-weekly 8 --keep-monthly 6 --prune
else
  echo "ATTENTION : RESTIC_REPOSITORY vide, sauvegarde uniquement locale" >&2
fi

echo "sauvegarde OK : $dir"
