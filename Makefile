.PHONY: setup ui test serve dev index-demo clean
PY ?= python3
VENV = .venv
BIN = $(VENV)/bin

setup:            ## create venv + install backend and frontend deps
	$(PY) -m venv $(VENV)
	$(BIN)/pip install -U pip
	$(BIN)/pip install -r backend/requirements-dev.txt
	cd frontend && npm install --no-audit --no-fund

ui:               ## build the UI (served by the API at http://localhost:8000)
	cd frontend && npm run build

test:             ## run the backend test suite
	cd backend && ../$(BIN)/python -m pytest

serve:            ## API + built UI on http://localhost:8000
	cd backend && CODEWORLD_UI_DIR=../frontend/dist CODEWORLD_ALLOW_LOCAL=1 ../$(BIN)/python -m uvicorn codeworld.api.main:app --port 8000

dev:              ## API on :8000 (run `cd frontend && npm run dev` in a 2nd terminal -> http://localhost:5173)
	cd backend && CODEWORLD_ALLOW_LOCAL=1 ../$(BIN)/python -m uvicorn codeworld.api.main:app --port 8000 --reload

index-demo:       ## index the bundled demo repo from the command line
	cd backend && ../$(BIN)/python -m codeworld index tests/fixtures/shop_app

clean:
	rm -rf .pytest_cache frontend/dist
