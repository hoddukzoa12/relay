#!/usr/bin/env bash
# Deploy all four services to Google Cloud Run.
# TEMPLATE — set PROJECT_ID / REGION and review secrets before running.
#
# Prereqs:
#   gcloud auth login && gcloud config set project "$PROJECT_ID"
#   gcloud services enable run.googleapis.com cloudbuild.googleapis.com
#   Store wallet keys + tokens in Secret Manager (see infra/cloudrun/README.md).
set -euo pipefail
cd "$(dirname "$0")/.."

PROJECT_ID="${PROJECT_ID:?set PROJECT_ID}"
REGION="${REGION:-asia-northeast3}"   # Seoul
REPO="gcr.io/${PROJECT_ID}"

build_and_deploy() {
  local name="$1" dockerfile="$2" context="$3" port="$4"
  echo "── building ${name} ────────────────────────────────"
  gcloud builds submit "$context" \
    --tag "${REPO}/${name}" \
    --gcs-source-staging-dir "gs://${PROJECT_ID}_cloudbuild/source" \
    ${dockerfile:+--config=/dev/stdin} <<EOF || \
  gcloud builds submit "$context" --tag "${REPO}/${name}"
steps:
  - name: gcr.io/cloud-builders/docker
    args: ['build','-f','${dockerfile}','-t','${REPO}/${name}','.']
images: ['${REPO}/${name}']
EOF
  echo "── deploying ${name} ───────────────────────────────"
  gcloud run deploy "$name" \
    --image "${REPO}/${name}" \
    --region "$REGION" \
    --port "$port" \
    --allow-unauthenticated \
    --set-env-vars "SOLANA_CLUSTER=devnet"
}

# TS services build from the repo root (need packages/shared).
build_and_deploy payments services/payments/Dockerfile . 8081
build_and_deploy commerce services/commerce/Dockerfile . 8082
# Python agents build from ./agents.
build_and_deploy shopping agents/Dockerfile agents 8091
build_and_deploy buyer    agents/Dockerfile agents 8090

cat <<'NOTE'

Next:
  * Wire the deployed URLs together via env vars on each service:
      payments  → (none)
      commerce  → SHOPIFY_* secrets
      shopping  → PAYMENTS_SERVICE_URL, COMMERCE_SERVICE_URL (payments/commerce URLs)
      buyer     → PAYMENTS_SERVICE_URL, SHOPPING_AGENT_URL
  * Mount wallet keys + GOOGLE_API_KEY + SHOPIFY token from Secret Manager,
    NOT plain --set-env-vars. See infra/cloudrun/README.md.
  * The buyer service URL is your public demo link.
NOTE
