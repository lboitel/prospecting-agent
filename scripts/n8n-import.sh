#!/usr/bin/env bash
# Importe les identifiants et les workflows de n8n/workflows/ dans l'instance n8n.
#
#   ./scripts/n8n-import.sh             # importe (workflows non publiés)
#   ./scripts/n8n-import.sh --publish   # importe, publie les workflows et redémarre n8n
#
# Prérequis : pile démarrée, compte propriétaire n8n créé, variables TELEGRAM_* et API_KEY
# renseignées dans .env. Réimporter écrase les workflows du même ID : exportez d'abord
# vos modifications faites dans l'éditeur (voir docs/ARCHITECTURE.md § 8).
set -euo pipefail

cd "$(dirname "$0")/.."
set -a; source .env; set +a

: "${API_KEY:?API_KEY manquant dans .env}"
: "${TELEGRAM_BOT_TOKEN:?TELEGRAM_BOT_TOKEN manquant dans .env}"
: "${TELEGRAM_CHAT_ID:?TELEGRAM_CHAT_ID manquant dans .env}"

compose=(docker compose)
if [[ "${N8N_DOMAIN:-}" == "localhost" ]]; then
  compose+=(-f docker-compose.yml -f docker-compose.dev.yml)
fi

publish=false
[[ "${1:-}" == "--publish" ]] && publish=true

workflow_ids=(prospectW0Errors prospectW3Review prospectW1Import prospectW2Traite prospectW8Source)
tmp=/tmp/prospecting-import

# Secrets et IDs de chat : jamais dans le dépôt, injectés au moment de l'import.
cleanup() { "${compose[@]}" exec -T n8n rm -rf "$tmp" || true; }
trap cleanup EXIT
"${compose[@]}" exec -T n8n sh -c "rm -rf $tmp && mkdir -p $tmp/workflows"

python3 - <<'EOF' | "${compose[@]}" exec -T n8n sh -c "umask 077 && cat > $tmp/credentials.json"
import json, os
print(json.dumps([
    {
        "id": "prospectApiCreds",
        "name": "Prospecting API",
        "type": "httpHeaderAuth",
        "data": {"name": "X-API-Key", "value": os.environ["API_KEY"]},
    },
    {
        "id": "prospectTelegram",
        "name": "Telegram Prospection",
        "type": "telegramApi",
        "data": {
            "accessToken": os.environ["TELEGRAM_BOT_TOKEN"],
            "baseUrl": "https://api.telegram.org",
        },
    },
]))
EOF

for file in n8n/workflows/*.json; do
  sed "s/__TELEGRAM_CHAT_ID__/${TELEGRAM_CHAT_ID}/g" "$file" \
    | "${compose[@]}" exec -T n8n sh -c "cat > $tmp/workflows/$(basename "$file")"
done

"${compose[@]}" exec -T n8n n8n import:credentials --input="$tmp/credentials.json"
"${compose[@]}" exec -T n8n n8n import:workflow --separate --input="$tmp/workflows"

if $publish; then
  # W0 et W3 aussi : n8n n'exécute que la version publiée d'un sous-workflow.
  for id in "${workflow_ids[@]}"; do
    "${compose[@]}" exec -T n8n n8n publish:workflow --id="$id"
  done
  cleanup
  trap - EXIT
  # La publication par la CLI n'est prise en compte qu'au redémarrage.
  "${compose[@]}" restart n8n
  echo "workflows importés et publiés (W8 à 8 h et W2 toutes les heures, en jours ouvrés)"
else
  echo "workflows importés, non publiés : relancez avec --publish pour les activer"
fi
