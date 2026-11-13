# KERP local dev stack (T-0003). POSIX make; on Windows run it from Git Bash
# (make itself is not bundled with Git for Windows: e.g. `choco install make` or `scoop install make`).
#
#   make test-pg     bring the stack up and run the PostgreSQL RLS + audit-ledger tests (no skips)
#   make help        list all targets
#
# Settings below are dev-only defaults for the throwaway docker-compose stack and match CI's
# "Tests (PostgreSQL + RLS)" step. Override them in an optional, git-ignored `.env`
# (plain KEY=VALUE lines) or on the command line (`make test-pg PYTHON=.venv/bin/python`).

.DEFAULT_GOAL := help

-include .env

# Runtime role: NOSUPERUSER NOBYPASSRLS, so PostgreSQL row-level security applies.
KERP_DATABASE_URL ?= postgresql+psycopg://kerp_app:kerp_app@localhost:5432/kerp_test
# Superuser of the test database, only for tests that tamper behind the app's back.
KERP_TEST_OWNER_URL ?= postgresql+psycopg://kerp:kerp@localhost:5432/kerp_test
KERP_REDIS_URL ?= redis://localhost:6379/0
# No JWT key: outside production the backend generates an ephemeral one per process.

# Host ports of the compose services. Changing KERP_PG_PORT / KERP_REDIS_PORT means
# changing the URLs above as well.
KERP_PG_PORT ?= 5432
KERP_REDIS_PORT ?= 6379
KERP_MINIO_PORT ?= 9000
KERP_MINIO_CONSOLE_PORT ?= 9001
KERP_MINIO_ROOT_USER ?= kerp-minio
KERP_MINIO_ROOT_PASSWORD ?= kerp-minio-dev-only

# Every variable above reaches docker compose and pytest through the environment.
export

PYTHON ?= python
COMPOSE ?= docker compose
PG_TESTS ?= tests/test_rls_bypass.py tests/test_audit_ledger_pg.py tests/test_pg_extensions.py

.PHONY: help up down reset ps logs test-pg test-pg-all worker

help:
	@echo "KERP dev stack (PostgreSQL 16 + PostGIS + ltree, Redis 7, MinIO)"
	@echo "  make up           start the stack and wait until every service is healthy"
	@echo "  make down         stop the stack (data volumes are kept)"
	@echo "  make reset        stop the stack and delete its volumes (re-runs the DB init script)"
	@echo "  make ps | logs    service status | follow the logs"
	@echo "  make test-pg      up + run the RLS-bypass and audit-ledger tests; any skip fails"
	@echo "  make test-pg-all  up + run the whole backend suite on PostgreSQL, as CI's PG step"
	@echo "  make worker       build and start the SAQ job worker container (both queues)"

up:
	$(COMPOSE) up -d --wait

down:
	$(COMPOSE) down

reset:
	$(COMPOSE) down -v

ps:
	$(COMPOSE) ps

logs:
	$(COMPOSE) logs -f

test-pg: up
	cd backend && KERP_FAIL_ON_SKIP=1 $(PYTHON) -m pytest --no-cov $(PG_TESTS)

test-pg-all: up
	cd backend && $(PYTHON) -m pytest --no-cov

worker: up
	$(COMPOSE) --profile worker up -d --build worker
