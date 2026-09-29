# Lodestar developer commands. On Windows without `make`, use ./make.ps1 <target>.

ifeq ($(OS),Windows_NT)
  PY_BIN := backend/.venv/Scripts
else
  PY_BIN := backend/.venv/bin
endif
PYTHON ?= python3
PY := $(PY_BIN)/python

.PHONY: setup dev backend frontend build run test lint format eval

setup:            ## Create the venv, install dependencies, download models
	$(PYTHON) -m venv backend/.venv
	$(PY) -m pip install --upgrade pip
	$(PY) -m pip install -e "backend[dev]"
	cd frontend && npm ci
	$(PY) scripts/setup_models.py

dev:              ## Backend (reload) on :8000 and Vite on :5173
	$(MAKE) -j2 backend frontend

backend:
	cd backend && ../$(PY) -m uvicorn --factory app.main:create_app --reload --host 127.0.0.1 --port 8000

frontend:
	cd frontend && npm run dev

build:            ## Build the frontend into frontend/dist (served by the backend)
	cd frontend && npm run build

run: build        ## Single process: http://127.0.0.1:8000
	cd backend && ../$(PY) -m uvicorn --factory app.main:create_app --host 127.0.0.1 --port 8000

test:             ## Backend tests, then frontend type-check + lint
	cd backend && ../$(PY) -m pytest
	cd frontend && npx tsc -b && npm run lint

lint:
	cd backend && ../$(PY) -m ruff check . ../scripts && ../$(PY) -m ruff format --check . ../scripts
	cd frontend && npm run lint && npm run format:check

format:
	cd backend && ../$(PY) -m ruff format . && ../$(PY) -m ruff check --fix .
	cd frontend && npm run format

eval:             ## Retrieval quality (hit@K, MRR) on examples/bookshelf
	$(PY) scripts/eval.py
