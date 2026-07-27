#!/usr/bin/env bash
# Scripted end-to-end purchase against a running stack.
# Usage: ./scripts/demo.sh "wireless earbuds" 5
set -euo pipefail

BUYER="${BUYER_AGENT_URL:-http://localhost:8090}"
QUERY="${1:-wireless earbuds}"
BUDGET="${2:-5}"

echo "▶ Delegating to buyer agent: '$QUERY' (budget ${BUDGET} USDC)"
echo "  buyer = $BUYER"
echo "  계정도 비밀번호도 없다. 지갑이 곧 계정이고, 서명이 곧 로그인."
echo "  CLI path stays agent-only; use the Shopify widget for Clerk + one-time IntentMandate consent."
echo

RESPONSE=$(curl -sS -X POST "$BUYER/buy" \
  -H 'Content-Type: application/json' \
  -d "{\"query\":\"${QUERY}\",\"budget\":${BUDGET}}")

if command -v jq >/dev/null 2>&1; then
  if ! echo "$RESPONSE" | jq -e . >/dev/null 2>&1; then
    echo "buyer returned a non-JSON error response:" >&2
    printf '%s\n' "$RESPONSE" >&2
    exit 1
  fi
  echo "$RESPONSE" | jq .
  echo
  STATUS=$(echo "$RESPONSE" | jq -r '.confirmation.status // .reason')
  EXPLORER=$(echo "$RESPONSE" | jq -r '.confirmation.explorer // empty')
  echo "status:   $STATUS"
  [ -n "$EXPLORER" ] && echo "explorer: $EXPLORER"
else
  echo "$RESPONSE"
fi
