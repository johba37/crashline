#!/usr/bin/env bash
# Starts the service: uvicorn on 127.0.0.1:$BACKEND_PORT (8650), the API and the indexer in one process.
# Reads backend/config.json (BACKEND_CONFIG overrides) and backend/.env if present
# (DEMO_TOKEN, DEMO_KEY, TEACHER_DEVICE, ...). BACKEND_DB overrides the SQLite path.
set -euo pipefail
cd "$(dirname "$0")"
if [ -f .env ]; then set -a; . ./.env; set +a; fi
PORT="${BACKEND_PORT:-8650}"
exec .venv/bin/uvicorn app.main:app --host 127.0.0.1 --port "$PORT" --log-level "${LOG_LEVEL:-info}" --no-access-log
