#!/usr/bin/env bash
# Create the six active Secret Manager versions used by the Cloud Run stack.
# Existing secrets are left unchanged so a repeat does not create billable
# versions. Rotate deliberately in the console or with `gcloud secrets versions`.
set -euo pipefail
cd "$(dirname "$0")/.."

if [[ -f .env ]]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi

PROJECT_ID="${PROJECT_ID:-${GCP_PROJECT_ID:-}}"
: "${PROJECT_ID:?set PROJECT_ID}"
: "${GOOGLE_API_KEY:?GOOGLE_API_KEY is required in .env}"
: "${SHOPIFY_CLIENT_ID:?SHOPIFY_CLIENT_ID is required in .env}"
: "${SHOPIFY_CLIENT_SECRET:?SHOPIFY_CLIENT_SECRET is required in .env}"
: "${CLERK_SECRET_KEY:?CLERK_SECRET_KEY is required in .env}"
: "${MCP_API_KEY:?MCP_API_KEY is required in .env}"

MERCHANT_KEYPAIR="${MERCHANT_WALLET_KEYPAIR_PATH:-wallets/merchant.json}"
BUYER_KEYPAIR="${BUYER_WALLET_KEYPAIR_PATH:-wallets/buyer.json}"
WALLET_BUNDLE="$(
  node scripts/wallet-secret-bundle.mjs "$MERCHANT_KEYPAIR" "$BUYER_KEYPAIR"
)"

create_secret_once() {
  local name="$1"
  local value="$2"
  if gcloud secrets describe "$name" --project "$PROJECT_ID" >/dev/null 2>&1; then
    echo "Secret ${name} already exists; leaving its active versions unchanged."
    return
  fi
  printf '%s' "$value" |
    gcloud secrets create "$name" \
      --project "$PROJECT_ID" \
      --replication-policy automatic \
      --data-file=-
}

create_secret_once relay-wallets "$WALLET_BUNDLE"
create_secret_once relay-google-api-key "$GOOGLE_API_KEY"
create_secret_once relay-shopify-client-id "$SHOPIFY_CLIENT_ID"
create_secret_once relay-shopify-client-secret "$SHOPIFY_CLIENT_SECRET"
create_secret_once relay-clerk-secret-key "$CLERK_SECRET_KEY"
create_secret_once relay-mcp-api-key "$MCP_API_KEY"

echo "Cloud Run secrets are ready (6 active versions; within the free allowance)."
