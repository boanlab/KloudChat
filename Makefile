# KloudChat — the commands CI runs, so "green locally" and "green in CI" match.
#
#   make help     list targets
#   make check    everything CI checks, in the order CI checks it

SHELL := /usr/bin/env bash
WEB := apps/web
API := apps/api
COMPOSE := docker compose
BUILD := $(COMPOSE) -f docker-compose.yml -f docker-compose.build.yml
DEV := $(BUILD) -f docker-compose.dev.yml

.DEFAULT_GOAL := help
.PHONY: help up build dev down logs ps check lint-web build-web lint-api test-api \
        lint-scripts config migrate revision e2e smoke clean

help: ## List available targets
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) \
	  | awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}'

## ── Running ────────────────────────────────────────────────────────────────

up: ## Start the published images (web :5173, api :8100, db :5433)
	$(COMPOSE) up -d

build: ## Start with every image built from this checkout
	$(BUILD) up -d --build

dev: ## Build, plus the Vite overlay — web source edits reload live
	$(DEV) up -d --build

down: ## Stop the stack, keeping volumes
	$(COMPOSE) down

ps: ## Show container status
	$(COMPOSE) ps

logs: ## Follow API logs
	$(COMPOSE) logs -f api

## ── Checks ─────────────────────────────────────────────────────────────────

check: lint-web build-web lint-api test-api lint-scripts config ## Run every CI check

lint-web: ## oxlint the web app
	cd $(WEB) && npm run lint

build-web: ## Typecheck and bundle the web app
	cd $(WEB) && npm run build

lint-api: ## ruff the API
	cd $(API) && ruff check .

test-api: ## Unit tests for the API
	cd $(API) && pytest -q

lint-scripts: ## shellcheck the integration scripts
	shellcheck --severity=error scripts/*.sh scripts/lib/*.sh

config: ## Validate every compose file combination without starting anything
	KCHAT_JWT_SECRET=placeholder $(COMPOSE) -f docker-compose.yml config -q
	KCHAT_JWT_SECRET=placeholder $(BUILD) config -q
	KCHAT_JWT_SECRET=placeholder $(DEV) config -q

## ── Database ───────────────────────────────────────────────────────────────

migrate: ## Apply migrations to the running database
	$(COMPOSE) exec api alembic upgrade head

revision: ## Autogenerate a migration — make revision m="add widget table"
	@test -n "$(m)" || { echo 'usage: make revision m="short description"'; exit 2; }
	$(COMPOSE) exec api alembic revision --autogenerate -m "$(m)"

## ── Integration ────────────────────────────────────────────────────────────

smoke: ## API smoke test against a running stack (non-destructive)
	bash scripts/smoke-test.sh

e2e: ## Playwright suite against a running stack
	cd $(WEB) && npm run test:e2e

## ── Housekeeping ───────────────────────────────────────────────────────────

clean: ## Remove build output and test reports
	rm -rf $(WEB)/dist $(WEB)/test-results $(WEB)/playwright-report
	find $(API) -name __pycache__ -type d -prune -exec rm -rf {} +
