# Platform Use Cases

## Why this looks like an internal platform

This repository is not framed as a single-service config API. It is framed as a shared internal platform used by multiple backend teams to safely ship runtime changes.

The primary example services are:
- `payment-service.flags`
- `recommendation-service.tuning`
- `checkout-service.timeout`

## Payment service

This service uses the platform to change:
- Strong Customer Authentication toggles
- payment timeout behavior
- network retry behavior

Why it matters:
- payment behavior changes are operationally risky
- config changes need staged rollout, audit, and rollback
- even a small bad flag change can increase checkout failures

## Recommendation service

This service uses the platform to tune:
- ranking model selection
- exploration percentage
- fallback behavior

Why it matters:
- recommendation quality changes can silently degrade business metrics
- staged rollout helps validate a new tuning profile before full promotion
- automatic rollback is critical when ranking changes hurt error rate or latency

## Checkout service

This service uses the platform to manage:
- timeout behavior
- network retry controls
- conservative fallback behavior during degraded operation

Why it matters:
- checkout behavior changes are operationally sensitive
- clients can continue operating with last-known-good config when the control plane is unavailable

## Staged canary progression

This project now supports a more realistic staged rollout flow:
1. start at `1%`
2. advance to `10%`
3. advance to `100%`

That matches how internal platforms reduce rollout risk:
- start tiny
- observe synthetic metrics
- widen exposure only when healthy
- auto-rollback when metrics degrade

## Failure simulation story

Two stories are especially useful:

### Control plane unavailable

- SDK keeps serving cached last-known-good config
- client enters safe mode
- operators can still understand degraded behavior through health and metrics

### Bad config deployed

- rollout begins as a canary
- synthetic metric degrades
- canary monitor auto-rolls back
- audit log and notification trail explain exactly what happened
