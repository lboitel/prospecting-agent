# prospecting-agent

Agent de prospection B2B : n8n pour l'orchestration, une API Python (FastAPI + OpenAI) pour la recherche, la qualification et la rédaction. Chaque email est validé par un humain avant l'envoi. Les données restent sur le VPS.

- **Architecture et choix techniques** : [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)
- **Offre, cible, ton** (à remplir) : [playbook/](playbook/)

## Démarrage rapide

```bash
# Tests (aucun appel LLM réel)
cd api && uv sync && uv run pytest

# Pile complète en local
cp .env.example .env   # N8N_DOMAIN=localhost, renseigner les secrets
docker compose -f docker-compose.yml -f docker-compose.dev.yml up --build

# Workflows n8n (après création du compte n8n et du bot Telegram)
./scripts/n8n-import.sh --publish
```

Déploiement sur VPS, sauvegardes et exploitation : voir [§ 11 de l'architecture](docs/ARCHITECTURE.md#11-déploiement-et-exploitation).
