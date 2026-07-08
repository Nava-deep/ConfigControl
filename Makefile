PYTHON ?= .venv/bin/python
PIP ?= .venv/bin/pip

.PHONY: install test seed-demo docker-up docker-build docker-down

# Local Development Setup
install:
	python3 -m venv .venv
	$(PIP) install --upgrade pip
	$(PIP) install -e '.[dev]'

test:
	$(PYTHON) -m pytest -q

# Demo Commands
seed-demo:
	./scripts/seed-demo.sh

# Docker Infrastructure
docker-up:
	docker compose up -d

docker-build:
	docker compose up -d --build

docker-down:
	docker compose down -v
