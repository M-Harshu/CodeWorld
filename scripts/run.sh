#!/usr/bin/env bash
cd "$(dirname "$0")/.."
set -a; [ -f .env ] && . ./.env; set +a
export CODEWORLD_UI_DIR="$PWD/frontend/dist" PYTHONPATH="$PWD/backend" CODEWORLD_ALLOW_LOCAL="${CODEWORLD_ALLOW_LOCAL:-1}"
exec .venv/bin/python -m uvicorn codeworld.api.main:app --port "${PORT:-8000}"
