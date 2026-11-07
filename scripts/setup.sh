#!/usr/bin/env bash
# One-shot setup for macOS / Linux / WSL.  Usage: ./scripts/setup.sh
set -euo pipefail
cd "$(dirname "$0")/.."
command -v python3 >/dev/null || { echo "Python 3.10+ is required"; exit 1; }
python3 -c 'import sys; assert sys.version_info >= (3,10), "Python 3.10+ required"'
python3 -m venv .venv
.venv/bin/pip install -U pip
.venv/bin/pip install -r backend/requirements-dev.txt
.venv/bin/pip install -e backend
[ -f .env ] || cp .env.example .env
if [ ! -f frontend/dist/index.html ]; then
  if command -v npm >/dev/null; then (cd frontend && npm install --no-audit --no-fund && npm run build); else echo "npm not found and no prebuilt UI present: install Node 18+ or use the API/CLI only"; fi
fi
echo "Setup done. Start with:  ./scripts/run.sh"
