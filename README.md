# Config Control Plane

Centralized configuration management service with immutable versioning, typed-schema validation, websocket hot reload, staged rollouts, automatic canary rollback, RBAC audit logs, a Python SDK, and a small operator CLI.

## Why this project is high-signal

- Immutable versions + target-specific stable pointers model the kind of safety rails used in large infra teams.
- Deterministic canary routing and automatic rollback show rollout discipline, not just CRUD.
- Anonymous client failure telemetry shows how runtime issues feed back into the control plane without collecting raw crash dumps.
- The repo includes docs, CI, Docker Compose, Kubernetes manifests, Prometheus metrics, and incident reports so it reads like a serious systems project.

## Features

- REST API for create/list/get/version history
- JSON Schema validation on writes
- Websocket and long-poll watch endpoints with scoped subscriptions
- Rollback to any prior version
- Staged rollout engine with simulated canary metrics and manual promotion for partial rollouts
- RBAC through request headers (`admin`, `operator`, `reader`)
- Audit log trail for every mutation
- Typed Python SDK with TTL cache + last-known-good fallback
- Demo microservice showing live hot reload
- Anonymous client failure telemetry with stable fingerprints, version context, and server-side summaries
- Redis-backed multi-instance event fanout for websocket hot reload
- Prometheus metrics plus readiness/liveness probes

## Stack

- API: FastAPI
- Storage: Postgres in runtime, SQLite-compatible tests
- Cache and pubsub: Redis with graceful in-memory fallback
- Validation: `jsonschema`
- SDK/CLI: Python + `httpx` + `websockets`
- Observability: Prometheus

## Quickstart

```bash
docker compose up --build
```

The API comes up at [http://localhost:8080](http://localhost:8080) and Prometheus at [http://localhost:9090](http://localhost:9090).

### Local development

```bash
make install
make test
make run
```

## Demo flow

Create the baseline version:

```bash
.venv/bin/configctl push \
  --name checkout-service.timeout \
  --schema-file examples/timeout.schema.json \
  --value-file examples/timeout.v1.json \
  --description "baseline timeout"
```

Create a staged candidate:

```bash
.venv/bin/configctl push \
  --name checkout-service.timeout \
  --schema-file examples/timeout.schema.json \
  --value-file examples/timeout.v2.json \
  --description "candidate timeout"
```

Start the example microservice:

```bash
.venv/bin/config-demo-client --base-url http://localhost:8080
```

Start a canary rollout:

```bash
.venv/bin/configctl rollout \
  --name checkout-service.timeout \
  --target checkout-service \
  --percent 10 \
  --metric error_rate \
  --threshold 0.01 \
  --window 5
```

Start a partial rollout that you will promote manually:

```bash
.venv/bin/configctl rollout \
  --name checkout-service.timeout \
  --target checkout-service \
  --percent 10
```

Promote an active partial rollout after validation:

```bash
.venv/bin/configctl promote \
  --name checkout-service.timeout \
  --rollout-id <rollout-id>
```

Simulate a bad canary metric:

```bash
.venv/bin/configctl simulate-metric \
  --target checkout-service \
  --metric error_rate \
  --value 0.02
```

Inspect anonymous client failure summaries:

```bash
.venv/bin/configctl failure-summary \
  --name checkout-service.timeout \
  --window-minutes 60
```

List recent sanitized failure events:

```bash
.venv/bin/configctl failures --name checkout-service.timeout --limit 20
```

Inspect the audit trail:

```bash
.venv/bin/configctl audit --name checkout-service.timeout
```

## API surface

- `POST /configs`
- `GET /configs`
- `GET /configs/{name}?version=resolved|latest|<n>&target=<service>&client_id=<id>`
- `GET /configs/{name}/versions`
- `POST /configs/{name}/rollout`
- `POST /configs/{name}/rollouts/{rollout_id}/promote`
- `POST /configs/{name}/rollback`
- `POST /configs/{name}/schema/dry-run`
- `GET /audit?name=...`
- `POST /simulation/metrics`
- `POST /telemetry/failures`
- `GET /telemetry/failures`
- `GET /telemetry/failures/summary`
- `GET /watch/longpoll`
- `WS /watch/ws`
- `GET /metrics`
- `GET /health/live`
- `GET /health/ready`

## RBAC

RBAC is intentionally simple and explicit for demo purposes:

- `X-Role: reader` can read configs and audit logs
- `X-Role: operator` can create configs, roll out, roll back, and run dry-run validation
- `X-Role: admin` has full access

The service records `X-User-Id` in audit logs for all mutations.

Websocket clients must also send `X-User-Id` and `X-Role` headers. Reader subscriptions must be scoped by `config_name` or `target`.

## Anonymous Failure Telemetry

The Python SDK can report application failures back to the control plane without shipping raw stack traces or user identifiers.

What gets sent:

- config name and target
- failure source such as `demo-client` or `request-path`
- exception type
- stable fingerprint derived from exception type and frame names
- config version/source active at the time of failure
- anonymous installation ID generated locally by the SDK
- sanitized metadata such as runtime or safe numeric counters

What does not get stored:

- raw `client_id`
- raw stack traces
- arbitrary error messages
- user PII fields such as `email`, `token`, or `username`

The control plane hashes the anonymous installation ID server-side before storage and exposes aggregated summaries through `/telemetry/failures/summary`.

## Testing

```bash
make test
```

Current coverage focus:

- config creation and immutable version history
- hot-reload rollout notifications over websocket
- automatic canary rollback when synthetic metrics degrade
- anonymous client failure telemetry ingestion and summary aggregation
- SDK cache fallback when the control plane is unavailable

## Repo guide

- `README.md`
- `docs/architecture.md`
- `docs/failure_modes.md`
- `design_decisions.md`
- `incident_reports/redis_outage.md`
- `incident_reports/canary_rollback.md`

## Interview talking points

- Why immutable versions and pointer-based rollback are safer than in-place edits
- How deterministic client bucketing keeps canary cohorts stable
- Why Redis is treated as an optimization rather than a hard dependency
- How schema dry-run validation helps avoid breaking older versions during migrations
- Tradeoffs between websocket push, long polling, and periodic polling
