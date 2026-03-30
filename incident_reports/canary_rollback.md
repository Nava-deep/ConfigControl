# Incident Report: Canary Error Rate Breach Triggered Auto Rollback

## Summary

A staged rollout moved 20% of `checkout-service` clients to version 2 of the timeout config. The synthetic `error_rate` metric crossed the configured threshold, and the service automatically rolled the target back to version 1.

## Timeline

1. Operator created version 2 with a higher timeout.
2. Operator started a 20% rollout with `threshold=0.01`.
3. Synthetic metric was updated to `0.02`.
4. Background canary monitor detected the breach and executed automatic rollback.
5. Clients received a rollback event and re-resolved to the stable config.

## What worked

- The rollback was pointer-based and completed without deleting history.
- Audit logs preserved the actor trail plus the automated rollback reason.
- Canary clients converged back to the stable version without a restart.

## Lessons

- Keep canary windows short in low-risk environments to reduce time-to-detect.
- Use per-target metrics so noisy neighbors cannot trigger unrelated rollbacks.
