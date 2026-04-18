# Interview Guide

## Stronger resume bullets

- Built a centralized configuration control plane in Python with 19 API endpoints, immutable version history, and environment-aware target resolution across `dev`, `staging`, and `prod`, enabling safe runtime config changes without service restarts.
- Implemented deterministic 1%-100% canary rollouts with promotion and automatic rollback, Redis-backed cross-instance fanout, and SDK-based last-known-good fallback, protecting clients during bad config pushes and Redis outages.
- Added production-style operational safeguards including RBAC audit logs, anonymous failure telemetry, 13 Prometheus metrics, Docker Compose, Kubernetes manifests, GitHub Actions CI, and 560 automated tests covering rollout, delivery, failure scenarios, and helper-layer correctness.

## Interview questions and model answers

### 1. Why use immutable config versions instead of editing the current row?

Immutable versions make rollback, audit, and incident debugging much safer. Instead of mutating the active value in place, the system writes a new version and updates a stable pointer. That lets operators inspect exact historical versions, diff changes, and revert quickly without losing provenance.

### 2. How do you resolve a config for a client during a rollout?

The service resolves the stable assignment for `(config_name, environment, target)`, checks for an active rollout, and deterministically buckets the client by hashing `(config_name, environment, target, client_id)`. That keeps the same client pinned to the same cohort across repeated requests.

### 3. Why separate stable assignments from rollout state?

Stable assignments capture the durable steady state, while rollouts represent temporary transition state. Keeping them separate simplifies promotion and rollback because promotion updates the stable pointer and rollback restores the previous pointer without rewriting history.

### 4. What happens if Redis goes down?

Postgres remains the source of truth, so version reads and writes still work. The API falls back to in-memory notification delivery for the local instance, while cross-instance fanout degrades. SDK clients continue serving cached last-known-good values, so applications stay functional even though hot reload becomes less complete across replicas.

### 5. What happens if Postgres goes down?

Writes and rollout transitions fail fast because the control plane cannot safely mutate source-of-truth state. Existing SDK clients continue using cached config until the cache expires or the process restarts. This is a fail-closed posture for control-plane writes and a fail-soft posture for data-plane reads.

### 6. Why support both WebSocket and long-poll?

WebSocket provides low-latency hot reload for long-lived services, while long-poll is simpler and works in more restrictive network environments. Supporting both gives better client compatibility and makes the delivery path easier to reason about under degraded conditions.

### 7. How would you scale this system horizontally?

Run multiple stateless API replicas behind a load balancer, share Postgres for durable state, and use Redis pubsub for cross-instance notification fanout. If usage grows further, I would add read replicas for analytics-heavy endpoints, partition telemetry storage, and possibly separate the watch delivery path from write-heavy control-plane traffic.

### 8. What consistency tradeoff did you make around client caching?

The SDK prefers availability over perfect freshness during outages. It uses a TTL cache and falls back to last-known-good config if fetches fail. That means clients may temporarily serve stale config, but they avoid failing closed on a control-plane dependency outage.

### 9. How do you keep `staging` and `prod` from bleeding into each other?

Environment is modeled explicitly in config versions, assignments, rollouts, audit logs, notifications, SDK fetches, and telemetry summaries. That prevents operational confusion where a staging rollout or failure report could otherwise appear to affect production.

### 10. How would you evolve this into a true internal platform service?

The next steps would be formal schema migrations, OIDC or JWT-backed auth, approval workflows for sensitive configs, region or tenant-aware targeting, alert-backed SLO dashboards, and integration with a real metrics pipeline for rollout health evaluation instead of synthetic signals.

### 11. What is the most dangerous failure mode in a config control plane?

A bad config can often break production faster than a bad deploy because it changes runtime behavior immediately. That is why immutable versions, dry-run validation, audit logs, canary rollouts, and fast rollback are more important here than raw CRUD throughput.

### 12. How would you reduce load on the control plane at very large scale?

I would increase SDK-side caching sophistication, use stronger cache invalidation or snapshot delivery, shard watch traffic, precompute stable snapshots for hot keys, and separate write APIs from read-heavy distribution endpoints. The goal would be to keep the control plane authoritative while pushing most steady-state reads to cheaper paths.
