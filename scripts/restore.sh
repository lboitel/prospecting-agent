#!/usr/bin/env bash
# Restaure une sauvegarde : ./scripts/restore.sh backups/2026-09-16_0315
# Écrase les données actuelles. À tester régulièrement sur une autre machine.
set -euo pipefail

cd "$(dirname "$0")/.."
dir="${1:?usage: restore.sh <dossier de sauvegarde>}"

read -r -p "Écraser les bases actuelles avec $dir ? (oui/non) " answer
[[ "$answer" == "oui" ]] || exit 1

docker compose stop n8n api
for db in prospecting n8n; do
  docker compose exec -T postgres \
    pg_restore -U postgres --clean --if-exists --no-owner --role="$db" --dbname="$db" \
    < "$dir/$db.dump"
done
docker compose start n8n api
echo "restauration terminée"
