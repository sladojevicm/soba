# soba — developer targets. Docker images are built from the repo root with
# the Dockerfiles under docker/; Python targets use the repo venv.
PYTHON ?= .venv/bin/python
COMPOSE ?= docker compose
TAG ?= local

.PHONY: help build build-api build-pipeline frontend-check up down test lint loadtest

help:
	@echo "build           build soba-api, soba-pipeline and run the frontend-check"
	@echo "build-api       docker/api.Dockerfile      -> soba-api:$(TAG)"
	@echo "build-pipeline  docker/pipeline.Dockerfile -> soba-pipeline:$(TAG) (CUDA base, ~7 GB)"
	@echo "frontend-check  rebuild frontend/dist in node:20 and diff against the committed bundle"
	@echo "up / down       docker compose --profile cpu (api + redis + orchestration worker, mock mode)"
	@echo "test            pytest (full suite; open3d-dependent tests fail without the recon extra)"
	@echo "lint            ruff check (E9 + F only, see [tool.ruff] in pyproject.toml)"
	@echo "loadtest        k6 scenarios — lands with feat/load-testing"

build: build-api build-pipeline frontend-check

build-api:
	docker build -f docker/api.Dockerfile -t soba-api:$(TAG) .

build-pipeline:
	docker build -f docker/pipeline.Dockerfile -t soba-pipeline:$(TAG) .

frontend-check:
	docker build -f docker/frontend-check.Dockerfile --target check -t soba-frontend-check:$(TAG) .

up:
	SOBA_QUEUE_URL=redis://redis:6379/0 SOBA_WORKER_INPROC=0 $(COMPOSE) --profile cpu up --build

down:
	$(COMPOSE) --profile cpu --profile gpu down

test:
	$(PYTHON) -m pytest -q

lint:
	$(PYTHON) -m ruff check .

loadtest:
	@echo "make loadtest: the k6 scenarios land with feat/load-testing (.claude/AGENTS.md agent 7)." >&2
	@exit 2
