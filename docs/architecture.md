# Architecture

```mermaid
flowchart LR
    CLI["CLI / Operators"] --> API["FastAPI Control Plane"]
    SDK["Python SDK / Demo Client"] --> API
    API --> PG["Postgres\n(version history, assignments, rollout state, audit logs, telemetry)"]
    API --> REDIS["Redis\n(read-through cache, pubsub, simulated metrics)"]
    API --> WS["WebSocket + Long Poll Hub"]
    REDIS --> RELAY["Redis Event Bridge"]
    RELAY --> WS
    WS --> SDK
    API --> CANARY["Background Canary Monitor"]
    CANARY --> PG
    CANARY --> REDIS
    API --> METRICS["Prometheus /metrics"]
```

## Request flow

1. Operators create immutable versions with `POST /configs`.
2. The service validates the value against JSON Schema before persisting it.
3. Version data lands in Postgres and is mirrored to Redis when available.
4. Stable assignments are tracked per `(config_name, environment, target)`.
5. `POST /configs/{name}/rollout` creates an active rollout from the current stable version to the newest staged version.
6. Clients resolve configs with deterministic bucketing on `(config_name, environment, target, client_id)`, so the same client stays pinned to the same canary/stable choice.
7. The Redis event bridge relays config events across API replicas so websocket subscribers stay current in horizontally scaled deployments.
8. SDK clients can send anonymized failure reports with config version and environment context when request paths fail.
9. The canary monitor checks synthetic metrics and automatically promotes or rolls back.
10. Every mutating action writes an audit row with actor, action, environment, version, and details.

## Data model

- `config_versions`: immutable version history, schemas, labels, and environment-aware metadata.
- `config_assignments`: stable pointer per config, environment, and target.
- `rollouts`: active/promoted/rolled_back rollout records.
- `audit_logs`: RBAC-aware operator history for compliance and forensics.
- `client_failure_events`: anonymous runtime failure telemetry grouped by fingerprint, environment, and target.
