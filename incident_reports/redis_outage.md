# Incident Report: Redis Outage During Active Watch Traffic

## Summary

During a simulated Redis outage, the control plane lost its external cache/pubsub layer but continued serving config reads and websocket notifications from the process-local fallback path.

## Impact

- No config data loss.
- No client restarts required.
- Read latency increased because hot reads fell back to Postgres.
- Multi-instance pubsub fanout would be degraded until Redis recovered.

## Root cause

Redis was treated as a best-effort accelerator. Once `PING` or later cache operations failed, the service downgraded to an in-memory fallback and surfaced the status through `/health/ready` and Prometheus.

## Detection

- `config_service_redis_available` drops to `0`.
- Operators can confirm via `/health/ready`.

## Remediation

1. Restore Redis.
2. Confirm health checks are green.
3. Resume normal fanout and cache hit behavior automatically; no manual replay is required.

## Follow-up

- Add a cross-instance subscription worker if the service is scaled horizontally.
- Add alerting on prolonged Redis unavailability.
