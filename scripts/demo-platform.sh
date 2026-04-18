#!/usr/bin/env bash
set -euo pipefail

BASE_URL="${BASE_URL:-http://localhost:8080}"
ENVIRONMENT="${ENVIRONMENT:-prod}"
CLI="${CLI:-.venv/bin/configctl}"

echo "Seeding platform demo configs..."
./scripts/seed-demo.sh

echo
echo "Starting staged rollout for payment-service.flags at 1%..."
ROLL_1=$("${CLI}" --base-url "${BASE_URL}" --environment "${ENVIRONMENT}" rollout \
  --name payment-service.flags \
  --target payment-service \
  --percent 1)
echo "${ROLL_1}"
ROLL_ID=$(echo "${ROLL_1}" | python3 -c 'import json,sys; print(json.load(sys.stdin)["rollout_id"])')

echo
echo "Advancing rollout to 10%..."
"${CLI}" --base-url "${BASE_URL}" --environment "${ENVIRONMENT}" advance \
  --name payment-service.flags \
  --rollout-id "${ROLL_ID}" \
  --percent 10

echo
echo "Simulating healthy metric for payment-service..."
"${CLI}" --base-url "${BASE_URL}" --environment "${ENVIRONMENT}" simulate-metric \
  --target payment-service \
  --metric error_rate \
  --value 0.002

echo
echo "Promoting payment-service.flags rollout to 100%..."
"${CLI}" --base-url "${BASE_URL}" --environment "${ENVIRONMENT}" advance \
  --name payment-service.flags \
  --rollout-id "${ROLL_ID}" \
  --percent 100

echo
echo "Starting canary rollout for recommendation-service.tuning at 10%..."
"${CLI}" --base-url "${BASE_URL}" --environment "${ENVIRONMENT}" rollout \
  --name recommendation-service.tuning \
  --target recommendation-service \
  --percent 10 \
  --metric error_rate \
  --threshold 0.01 \
  --window 5

echo
echo "Simulating metric degradation to trigger auto rollback..."
"${CLI}" --base-url "${BASE_URL}" --environment "${ENVIRONMENT}" simulate-metric \
  --target recommendation-service \
  --metric error_rate \
  --value 0.03

echo
echo "Platform demo complete. Review audit logs and telemetry next."
