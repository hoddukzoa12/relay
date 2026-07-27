#!/usr/bin/env bash
# Run the whole stack locally (no Docker). Ctrl-C stops everything.
# Prereqs: `make setup` (pnpm install + python venv), and a filled-in .env.
set -euo pipefail
cd "$(dirname "$0")/.."

if [ ! -d agents/.venv ]; then
  echo "Python venv missing. Run: make setup-py" >&2; exit 1
fi

pids=()
cleanup() { echo; echo "stopping…"; kill "${pids[@]}" 2>/dev/null || true; }
trap cleanup EXIT INT TERM

echo "starting payments (:8081) + commerce (:8082)…"
pnpm --filter @arb/payments dev & pids+=($!)
pnpm --filter @arb/commerce dev & pids+=($!)

sleep 2
echo "starting shopping agent (:8091) + buyer agent (:8090) + MCP (:8092)…"
( cd agents && ./.venv/bin/python -m agentic_broker.shopping.server ) & pids+=($!)
( cd agents && ./.venv/bin/python -m agentic_broker.buyer.server ) & pids+=($!)
( cd agents && ./.venv/bin/python -m agentic_broker.mcp.server ) & pids+=($!)

echo
echo "  payments  http://localhost:8081/health"
echo "  commerce  http://localhost:8082/health"
echo "  shopping  http://localhost:8091/health"
echo "  buyer UI  http://localhost:8090   ← open this"
echo "  MCP       http://localhost:8092/mcp (X-Relay-API-Key required)"
echo
wait
