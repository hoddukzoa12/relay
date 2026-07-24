#!/usr/bin/env bash
# Scripted end-to-end purchase against a running stack.
# Usage: ./scripts/demo.sh "wireless earbuds" 25
set -euo pipefail

BUYER="${BUYER_AGENT_URL:-http://localhost:8090}"
QUERY="${1:-wireless earbuds}"
BUDGET="${2:-25}"

echo "▶ Delegating to buyer agent: '$QUERY' (budget ${BUDGET} USDC)"
echo "  buyer = $BUYER"
echo

RESPONSE=$(curl -sS -X POST "$BUYER/buy" \
  -H 'Content-Type: application/json' \
  -d "{\"query\":\"${QUERY}\",\"budget\":${BUDGET}}")

if command -v jq >/dev/null 2>&1; then
  echo "$RESPONSE" | jq .
  echo
  STATUS=$(echo "$RESPONSE" | jq -r '.confirmation.status // .reason')
  EXPLORER=$(echo "$RESPONSE" | jq -r '.confirmation.explorer // empty')
  echo "status:   $STATUS"
  [ -n "$EXPLORER" ] && echo "explorer: $EXPLORER"
else
  echo "$RESPONSE"
fi
