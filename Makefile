PYTHON ?= .venv/bin/python
PIP ?= .venv/bin/pip

.PHONY: install run test test-unit test-integration verify demo-client seed-demo docker-up docker-down

install:
	python3 -m venv .venv
	$(PIP) install --upgrade pip
	$(PIP) install -e '.[dev]'

run:
	$(PYTHON) -m uvicorn app.main:app --host 0.0.0.0 --port 8080 --reload

test:
	$(PYTHON) -m pytest -q

test-unit:
	$(PYTHON) -m pytest -q tests/unit

test-integration:
	$(PYTHON) -m pytest -q tests/integration

verify:
	$(PYTHON) -m compileall app tests
	$(PYTHON) -m pytest -q

demo-client:
	$(PYTHON) -m app.demo_client --base-url http://localhost:8080

seed-demo:
	./scripts/seed-demo.sh

docker-up:
	docker compose up --build

docker-down:
	docker compose down -v
