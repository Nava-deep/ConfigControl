# Design Decisions

## Why Python + FastAPI

The control plane needs fast iteration, clean typing, websocket support, and easy testability. FastAPI gave us a compact way to ship all of that while keeping the repo approachable for interviews.

## Immutable versions instead of in-place updates

Each `POST /configs` call creates a new version so rollbacks are constant-time pointer changes, audit history is complete, and clients can ask for exact historical versions during debugging.

## Stable pointer + active rollout

Instead of marking versions globally active, the service stores a stable pointer per `(config_name, environment, target)` and optionally overlays an active rollout. That makes canary resolution deterministic, prevents `staging` and `prod` from bleeding into each other, and keeps rollback logic simple.

## Deterministic client bucketing

Canary routing hashes `(config_name, environment, target, client_id)` into a 0-99 bucket. The same client lands in the same cohort every time, which is critical for reproducible debugging.

## Redis as an optimization, not a hard dependency

Redis accelerates cache reads and event fanout, but the control plane continues operating without it. This keeps the blast radius of Redis outages small and creates a strong talking point about graceful degradation.

## SQLite-compatible tests, Postgres runtime

The service is designed for Postgres in Compose/Kubernetes, but tests use SQLite so CI stays fast and deterministic. That tradeoff keeps the repo lightweight while still proving the core rollout semantics.

## Environment-aware telemetry

Failure telemetry is tagged with the config environment. Without that dimension, the same config key in `staging` and `prod` would collapse into the same incident view, which is misleading during rollout triage.
