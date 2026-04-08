#!/usr/bin/env bash
set -euo pipefail

BASE_URL="${BASE_URL:-http://localhost:8080}"
ENVIRONMENT="${ENVIRONMENT:-prod}"
CLI="${CLI:-.venv/bin/configctl}"

"${CLI}" --base-url "${BASE_URL}" --environment "${ENVIRONMENT}" push \
  --name checkout-service.timeout \
  --schema-file examples/timeout.schema.json \
  --value-file examples/timeout.v1.json \
  --description "baseline timeout" \
  --label team=checkout \
  --label owner=platform

"${CLI}" --base-url "${BASE_URL}" --environment "${ENVIRONMENT}" push \
  --name checkout-service.timeout \
  --value-file examples/timeout.v2.json \
  --description "candidate timeout" \
  --label team=checkout \
  --label owner=platform

echo "Seeded checkout-service.timeout versions 1 and 2 in environment '${ENVIRONMENT}'."
