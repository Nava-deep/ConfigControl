# Failure Modes

## Redis outage

- Reads still work because Postgres remains the source of truth.
- Clients keep using their on-disk last-known-good cache through the Python SDK.
- The API falls back to an in-memory cache for local notifications and simulated metrics.
- What degrades: cross-instance pubsub fanout and warm-cache hits.

## Postgres outage

- New writes and rollout transitions fail fast.
- Existing SDK clients keep serving cached configs until TTL expiry or process restart.
- Hot reload events stop because the control plane cannot persist new versions.
- Recovery action: restore Postgres, then replay intended config changes through the CLI.

## Network partition between client and control plane

- `ConfigClient.get_typed()` returns the cached last-known-good value if the fetch fails.
- Websocket watch reconnects with backoff.
- Once connectivity returns, the client re-fetches the resolved config and converges without a restart.

## Canary regression

- The rollout stays active until the canary window elapses or a threshold breach is detected.
- A threshold breach triggers rollback to `from_version`, emits a notification event, and writes an audit log entry tagged `config.rollout.auto_rollback`.
