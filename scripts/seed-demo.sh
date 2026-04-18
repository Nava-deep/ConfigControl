#!/usr/bin/env bash
set -euo pipefail

BASE_URL="${BASE_URL:-http://localhost:8080}"
ENVIRONMENT="${ENVIRONMENT:-prod}"
CLI="${CLI:-.venv/bin/configctl}"

seed_pair() {
  local name="$1"
  local schema_file="$2"
  local v1_file="$3"
  local v2_file="$4"
  local team="$5"
  local description_prefix="$6"

  "${CLI}" --base-url "${BASE_URL}" --environment "${ENVIRONMENT}" push \
    --name "${name}" \
    --schema-file "${schema_file}" \
    --value-file "${v1_file}" \
    --description "${description_prefix} baseline" \
    --label team="${team}" \
    --label owner=platform

  "${CLI}" --base-url "${BASE_URL}" --environment "${ENVIRONMENT}" push \
    --name "${name}" \
    --value-file "${v2_file}" \
    --description "${description_prefix} candidate" \
    --label team="${team}" \
    --label owner=platform
}

seed_pair "payment-service.flags" "examples/payment-flags.schema.json" "examples/payment-flags.v1.json" "examples/payment-flags.v2.json" "payments" "payment flags"
seed_pair "recommendation-service.tuning" "examples/recommendation-tuning.schema.json" "examples/recommendation-tuning.v1.json" "examples/recommendation-tuning.v2.json" "recommendations" "recommendation tuning"
seed_pair "rate-limiter-service.policy" "examples/rate-limiter-policy.schema.json" "examples/rate-limiter-policy.v1.json" "examples/rate-limiter-policy.v2.json" "traffic" "rate limiter policy"
seed_pair "checkout-service.timeout" "examples/timeout.schema.json" "examples/timeout.v1.json" "examples/timeout.v2.json" "checkout" "checkout timeout"

echo "Seeded platform demo configs in environment '${ENVIRONMENT}'."
