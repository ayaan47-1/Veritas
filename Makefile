SHELL := /bin/bash

BACKEND_HOST ?= 0.0.0.0
BACKEND_PORT ?= 8001
FRONTEND_PORT ?= 3000
INGGEST_PORT ?= 8288
API_URL ?= http://127.0.0.1:$(BACKEND_PORT)
INNGEST_DEV_MODE ?= 1

POSTGRES_CONTAINER ?= veritas-postgres
POSTGRES_IMAGE ?= postgres:16
POSTGRES_PORT ?= 5432
POSTGRES_DB ?= veritas
POSTGRES_USER ?= veritas
POSTGRES_PASSWORD ?= veritas

.PHONY: help install install-backend install-frontend backend frontend inngest db-up db-down dev dev-all test lint build migrate

help:
	@echo "Available targets:"
	@echo "  make install          # Install backend + frontend dependencies"
	@echo "  make db-up            # Start local Postgres Docker container"
	@echo "  make db-down          # Stop local Postgres Docker container"
	@echo "  make backend          # Start FastAPI on $(BACKEND_HOST):$(BACKEND_PORT)"
	@echo "  make frontend         # Start Next.js on :$(FRONTEND_PORT)"
	@echo "  make inngest          # Start Inngest dev server on :$(INGGEST_PORT)"
	@echo "  make dev              # Start backend + frontend + inngest together"
	@echo "  make dev-all          # Start db + all app services"
	@echo "  make migrate          # Run Alembic migrations"
	@echo "  make test             # Run backend tests"
	@echo "  make lint             # Run frontend lint"
	@echo "  make build            # Run frontend production build"

install-backend:
	python3 -m pip install -r backend/requirements.txt

install-frontend:
	npm --prefix frontend install

install: install-backend install-frontend

backend:
	@lsof -ti:$(BACKEND_PORT) | xargs kill -9 2>/dev/null || true
	set -a && source backend/.env && set +a && INNGEST_DEV=$(INNGEST_DEV_MODE) uvicorn backend.app.main:app --reload --host $(BACKEND_HOST) --port $(BACKEND_PORT)

frontend:
	npm --prefix frontend run dev -- --port $(FRONTEND_PORT)

inngest:
	npx --yes inngest-cli@latest dev -u $(API_URL)/api/inngest --port $(INGGEST_PORT) --no-discovery

db-up:
	@if ! command -v docker >/dev/null 2>&1; then \
		echo "docker is required for db-up"; \
		exit 1; \
	fi
	@if docker inspect $(POSTGRES_CONTAINER) >/dev/null 2>&1; then \
		echo "Starting existing container: $(POSTGRES_CONTAINER)"; \
		docker start $(POSTGRES_CONTAINER) >/dev/null; \
	else \
		echo "Creating container: $(POSTGRES_CONTAINER)"; \
		docker run -d --name $(POSTGRES_CONTAINER) \
			-e POSTGRES_DB=$(POSTGRES_DB) \
			-e POSTGRES_USER=$(POSTGRES_USER) \
			-e POSTGRES_PASSWORD=$(POSTGRES_PASSWORD) \
			-p $(POSTGRES_PORT):5432 \
			$(POSTGRES_IMAGE) >/dev/null; \
	fi
	@echo "Postgres available on localhost:$(POSTGRES_PORT)"

db-down:
	@if ! command -v docker >/dev/null 2>&1; then \
		echo "docker is required for db-down"; \
		exit 1; \
	fi
	@if docker inspect $(POSTGRES_CONTAINER) >/dev/null 2>&1; then \
		echo "Stopping container: $(POSTGRES_CONTAINER)"; \
		docker stop $(POSTGRES_CONTAINER) >/dev/null; \
	else \
		echo "Container not found: $(POSTGRES_CONTAINER)"; \
	fi

dev:
	@lsof -ti:$(BACKEND_PORT) | xargs kill -9 2>/dev/null || true
	@set -euo pipefail; \
	trap 'kill 0' INT TERM EXIT; \
	(set -a && source backend/.env && set +a && INNGEST_DEV=$(INNGEST_DEV_MODE) uvicorn backend.app.main:app --reload --host $(BACKEND_HOST) --port $(BACKEND_PORT)) & \
	(npm --prefix frontend run dev -- --port $(FRONTEND_PORT)) & \
	(npx --yes inngest-cli@latest dev -u $(API_URL)/api/inngest --port $(INGGEST_PORT) --no-discovery) & \
	wait

dev-all: db-up dev

migrate:
	python3 -m alembic -c backend/alembic.ini upgrade head

test:
	python3 -m pytest -q backend/tests

lint:
	npm --prefix frontend run lint

build:
	npm --prefix frontend run build
