#!/bin/bash
# Exécuté une seule fois, à la création du volume Postgres.
# Une base et un utilisateur par service : n8n ne peut pas lire les prospects, et inversement.
set -euo pipefail

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" \
  -v n8n_password="$N8N_DB_PASSWORD" \
  -v app_password="$APP_DB_PASSWORD" <<-'EOSQL'
	CREATE USER n8n WITH PASSWORD :'n8n_password';
	CREATE DATABASE n8n OWNER n8n;
	REVOKE ALL ON DATABASE n8n FROM PUBLIC;

	CREATE USER prospecting WITH PASSWORD :'app_password';
	CREATE DATABASE prospecting OWNER prospecting;
	REVOKE ALL ON DATABASE prospecting FROM PUBLIC;
EOSQL
