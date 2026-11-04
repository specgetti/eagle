IMAGE ?= quay.io/hmoghani/eagle
RUNNER_IMAGE ?= quay.io/hmoghani/eagle-agent-runner
TAG ?= dev
CONTAINER ?= podman
# OpenShift nodes are usually x86_64; override for a local arm64 cluster.
PLATFORM ?= linux/amd64

.PHONY: setup backend-dev worker-dev mock-dev frontend-dev lint format test test-frontend \
	openapi image runner-image images images-push

setup:
	cd backend && uv sync
	cd frontend && npm install
	uv run --project backend pre-commit install

backend-dev:
	cd backend && uv run uvicorn --factory eagle.main:create_app --reload --port 8000

# Runs reviews as local subprocesses (needs the agent CLIs on PATH).
worker-dev:
	cd backend && uv run eagle worker

# Full UI flow without any model: canned reviews from the mock executor.
mock-dev:
	cd backend && EAGLE_EXECUTOR=mock uv run eagle worker

frontend-dev:
	cd frontend && npm run dev

lint:
	cd backend && uv run ruff check . && uv run ruff format --check .

format:
	cd backend && uv run ruff check --fix . && uv run ruff format .

test:
	cd backend && uv run pytest

test-frontend:
	cd frontend && npm test

openapi:
	cd backend && uv run eagle openapi --out ../frontend/openapi.json

image:
	$(CONTAINER) build --platform $(PLATFORM) -f Containerfile -t $(IMAGE):$(TAG) .

runner-image:
	$(CONTAINER) build --platform $(PLATFORM) -f agent-runner/Containerfile -t $(RUNNER_IMAGE):$(TAG) .

images: image runner-image

images-push:
	$(CONTAINER) push $(IMAGE):$(TAG)
	$(CONTAINER) push $(RUNNER_IMAGE):$(TAG)
