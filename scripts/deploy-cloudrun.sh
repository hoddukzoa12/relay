#!/usr/bin/env bash
# Build and deploy the five Relay services to scale-to-zero Cloud Run.
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
REGION="${REGION:-${GCP_REGION:-us-central1}}"
if [[ "$REGION" != "us-central1" ]]; then
  echo "Refusing REGION=${REGION}: Relay must use us-central1 for the Cloud Run free tier." >&2
  exit 2
fi

required_vars=(
  SOLANA_RPC_URL
  USDC_MINT
  SHOPIFY_STORE_DOMAIN
  SHOPIFY_API_VERSION
  CLERK_PUBLISHABLE_KEY
  CLERK_ISSUER
  CLERK_JWKS_URL
  BROKER_MARKUP_PCT
  PAYMENT_REQUEST_TTL_MIN
)
for name in "${required_vars[@]}"; do
  if [[ -z "${!name:-}" ]]; then
    echo "Missing required non-secret deployment value: ${name}" >&2
    exit 2
  fi
done

AR_REPOSITORY="${AR_REPOSITORY:-relay-cloudrun}"
REGISTRY="${REGION}-docker.pkg.dev/${PROJECT_ID}/${AR_REPOSITORY}"
IMAGE_TAG="${IMAGE_TAG:-$(git rev-parse --short HEAD)-$(date -u +%Y%m%d%H%M%S)}"
PAYMENTS_IMAGE="${REGISTRY}/payments:${IMAGE_TAG}"
COMMERCE_IMAGE="${REGISTRY}/commerce:${IMAGE_TAG}"
AGENTS_IMAGE="${REGISTRY}/agents:${IMAGE_TAG}"

PAYMENTS_SA="relay-payments@${PROJECT_ID}.iam.gserviceaccount.com"
COMMERCE_SA="relay-commerce@${PROJECT_ID}.iam.gserviceaccount.com"
SHOPPING_SA="relay-shopping@${PROJECT_ID}.iam.gserviceaccount.com"
BUYER_SA="relay-buyer@${PROJECT_ID}.iam.gserviceaccount.com"
MCP_SA="relay-mcp@${PROJECT_ID}.iam.gserviceaccount.com"

ensure_service_account() {
  local short_name="$1"
  local display_name="$2"
  local email="${short_name}@${PROJECT_ID}.iam.gserviceaccount.com"
  if ! gcloud iam service-accounts describe "$email" \
    --project "$PROJECT_ID" >/dev/null 2>&1; then
    gcloud iam service-accounts create "$short_name" \
      --project "$PROJECT_ID" \
      --display-name "$display_name"
  fi
}

ensure_service_account relay-payments "Relay payments runtime"
ensure_service_account relay-commerce "Relay commerce runtime"
ensure_service_account relay-shopping "Relay shopping runtime"
ensure_service_account relay-buyer "Relay buyer runtime"
ensure_service_account relay-mcp "Relay MCP runtime"

if ! gcloud artifacts repositories describe "$AR_REPOSITORY" \
  --project "$PROJECT_ID" \
  --location "$REGION" >/dev/null 2>&1; then
  gcloud artifacts repositories create "$AR_REPOSITORY" \
    --project "$PROJECT_ID" \
    --location "$REGION" \
    --repository-format docker \
    --description "Ephemeral build images for the Relay Cloud Run demo"
fi

BUILD_SA="$(
  gcloud builds get-default-service-account --project "$PROJECT_ID"
)"
gcloud artifacts repositories add-iam-policy-binding "$AR_REPOSITORY" \
  --project "$PROJECT_ID" \
  --location "$REGION" \
  --member "serviceAccount:${BUILD_SA}" \
  --role roles/artifactregistry.writer \
  --quiet >/dev/null

grant_secret_access() {
  local secret="$1"
  local service_account="$2"
  if ! gcloud secrets describe "$secret" \
    --project "$PROJECT_ID" >/dev/null 2>&1; then
    echo "Missing Secret Manager secret ${secret}; run scripts/provision-cloudrun-secrets.sh." >&2
    exit 2
  fi
  gcloud secrets add-iam-policy-binding "$secret" \
    --project "$PROJECT_ID" \
    --member "serviceAccount:${service_account}" \
    --role roles/secretmanager.secretAccessor \
    --quiet >/dev/null
}

grant_secret_access relay-wallets "$PAYMENTS_SA"
grant_secret_access relay-shopify-client-id "$COMMERCE_SA"
grant_secret_access relay-shopify-client-secret "$COMMERCE_SA"
grant_secret_access relay-google-api-key "$SHOPPING_SA"
grant_secret_access relay-google-api-key "$BUYER_SA"
grant_secret_access relay-clerk-secret-key "$BUYER_SA"
grant_secret_access relay-mcp-api-key "$MCP_SA"

COMMON_ENV=(
  "SOLANA_RPC_URL=${SOLANA_RPC_URL}"
  "SOLANA_RPC_URL_FALLBACK=${SOLANA_RPC_URL_FALLBACK:-}"
  "SOLANA_CLUSTER=devnet"
  "USDC_MINT=${USDC_MINT}"
  "USDC_DECIMALS=${USDC_DECIMALS:-6}"
  "COMMERCE_MOCK=false"
  "SHOPIFY_STORE_DOMAIN=${SHOPIFY_STORE_DOMAIN}"
  "SHOPIFY_API_VERSION=${SHOPIFY_API_VERSION}"
  "CLERK_PUBLISHABLE_KEY=${CLERK_PUBLISHABLE_KEY}"
  "NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY=${NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY:-${CLERK_PUBLISHABLE_KEY}}"
  "CLERK_ISSUER=${CLERK_ISSUER}"
  "CLERK_JWKS_URL=${CLERK_JWKS_URL}"
  "BROKER_MARKUP_PCT=${BROKER_MARKUP_PCT}"
  "PAYMENT_REQUEST_TTL_MIN=${PAYMENT_REQUEST_TTL_MIN}"
  "GEMINI_MODEL=${GEMINI_MODEL:-gemini-2.5-flash}"
  "GOOGLE_GENAI_USE_VERTEXAI=${GOOGLE_GENAI_USE_VERTEXAI:-false}"
)

env_argument() {
  local extra="$1"
  local joined=""
  local pair
  for pair in "${COMMON_ENV[@]}"; do
    joined="${joined}${joined:+@}${pair}"
  done
  if [[ -n "$extra" ]]; then
    joined="${joined}@${extra}"
  fi
  printf '^@^%s' "$joined"
}

build_image() {
  local image="$1"
  local dockerfile="$2"
  local context="$3"
  echo "Building ${image}"
  gcloud builds submit . \
    --project "$PROJECT_ID" \
    --config infra/cloudrun/cloudbuild.yaml \
    --substitutions "_IMAGE=${image},_DOCKERFILE=${dockerfile},_CONTEXT=${context}"
}

build_image "$PAYMENTS_IMAGE" services/payments/Dockerfile .
build_image "$COMMERCE_IMAGE" services/commerce/Dockerfile .
build_image "$AGENTS_IMAGE" agents/Dockerfile agents

deploy_service() {
  local name="$1"
  local image="$2"
  local port="$3"
  local service_account="$4"
  local access="$5"
  local extra_env="$6"
  local secrets="${7:-}"
  local command="${8:-}"
  local command_args="${9:-}"

  local flags=(
    run deploy "$name"
    --project "$PROJECT_ID"
    --image "$image"
    --region "$REGION"
    --platform managed
    --port "$port"
    --service-account "$service_account"
    --cpu 1
    --memory 512Mi
    --concurrency 20
    --max-instances 2
    --cpu-throttling
    --no-cpu-boost
    --ingress all
    --set-env-vars "$(env_argument "$extra_env")"
    --quiet
  )
  if [[ "$access" == "public" ]]; then
    flags+=(--allow-unauthenticated)
  else
    flags+=(--no-allow-unauthenticated)
  fi
  if [[ -n "$secrets" ]]; then
    flags+=(--set-secrets "$secrets")
  fi
  if [[ -n "$command" ]]; then
    flags+=("--command=${command}" "--args=${command_args}")
  fi
  gcloud "${flags[@]}"
}

grant_invoker() {
  local service="$1"
  local caller="$2"
  gcloud run services add-iam-policy-binding "$service" \
    --project "$PROJECT_ID" \
    --region "$REGION" \
    --member "serviceAccount:${caller}" \
    --role roles/run.invoker \
    --quiet >/dev/null
}

deploy_service \
  payments "$PAYMENTS_IMAGE" 8081 "$PAYMENTS_SA" private \
  "PAYMENTS_PORT=8081@WALLET_SECRET_BUNDLE_PATH=/secrets/wallets.json" \
  "/secrets/wallets.json=relay-wallets:latest"
deploy_service \
  commerce "$COMMERCE_IMAGE" 8082 "$COMMERCE_SA" private \
  "COMMERCE_PORT=8082" \
  "SHOPIFY_CLIENT_ID=relay-shopify-client-id:latest,SHOPIFY_CLIENT_SECRET=relay-shopify-client-secret:latest"

PAYMENTS_URL="$(
  gcloud run services describe payments \
    --project "$PROJECT_ID" --region "$REGION" --format='value(status.url)'
)"
COMMERCE_URL="$(
  gcloud run services describe commerce \
    --project "$PROJECT_ID" --region "$REGION" --format='value(status.url)'
)"

# Bind peer access before starting callers so IAM has time to propagate during
# the remaining deployments.
grant_invoker payments "$SHOPPING_SA"
grant_invoker payments "$BUYER_SA"
grant_invoker commerce "$SHOPPING_SA"
grant_invoker commerce "$BUYER_SA"
grant_invoker payments "$MCP_SA"
grant_invoker commerce "$MCP_SA"

deploy_service \
  shopping "$AGENTS_IMAGE" 8091 "$SHOPPING_SA" private \
  "SHOPPING_PORT=8091@PAYMENTS_SERVICE_URL=${PAYMENTS_URL}@COMMERCE_SERVICE_URL=${COMMERCE_URL}" \
  "GOOGLE_API_KEY=relay-google-api-key:latest"
SHOPPING_URL="$(
  gcloud run services describe shopping \
    --project "$PROJECT_ID" --region "$REGION" --format='value(status.url)'
)"
grant_invoker shopping "$BUYER_SA"
grant_invoker shopping "$MCP_SA"

deploy_service \
  buyer "$AGENTS_IMAGE" 8090 "$BUYER_SA" public \
  "BUYER_PORT=8090@PAYMENTS_SERVICE_URL=${PAYMENTS_URL}@COMMERCE_SERVICE_URL=${COMMERCE_URL}@SHOPPING_AGENT_URL=${SHOPPING_URL}" \
  "GOOGLE_API_KEY=relay-google-api-key:latest,CLERK_SECRET_KEY=relay-clerk-secret-key:latest" \
  python "-m,agentic_broker.buyer.server"
BUYER_URL="$(
  gcloud run services describe buyer \
    --project "$PROJECT_ID" --region "$REGION" --format='value(status.url)'
)"

gcloud run services update buyer \
  --project "$PROJECT_ID" \
  --region "$REGION" \
  --update-env-vars \
  "^@^BUYER_CORS_ORIGINS=https://${SHOPIFY_STORE_DOMAIN},${BUYER_URL}@BUYER_AGENT_URL=${BUYER_URL}" \
  --quiet

deploy_service \
  mcp "$AGENTS_IMAGE" 8092 "$MCP_SA" public \
  "MCP_PORT=8092@MCP_CORS_ORIGINS=${MCP_CORS_ORIGINS:-*}@PAYMENTS_SERVICE_URL=${PAYMENTS_URL}@COMMERCE_SERVICE_URL=${COMMERCE_URL}@SHOPPING_AGENT_URL=${SHOPPING_URL}@BUYER_AGENT_URL=${BUYER_URL}" \
  "MCP_API_KEY=relay-mcp-api-key:latest" \
  python "-m,agentic_broker.mcp.server"
MCP_URL="$(
  gcloud run services describe mcp \
    --project "$PROJECT_ID" --region "$REGION" --format='value(status.url)'
)"

# Cloud Run imports each deployed image. Delete the Artifact Registry copies so
# the demo keeps no billable image storage; a repeat rebuilds them.
if [[ "${CLEANUP_IMAGES:-true}" == "true" ]]; then
  for image in payments commerce agents; do
    gcloud artifacts docker images delete "${REGISTRY}/${image}" \
      --project "$PROJECT_ID" \
      --delete-tags \
      --quiet
  done
fi

if [[ "${CLEANUP_BUILD_SOURCES:-true}" == "true" ]] &&
  gcloud storage ls "gs://${PROJECT_ID}_cloudbuild/source/**" \
    --project "$PROJECT_ID" >/dev/null 2>&1; then
  gcloud storage rm \
    --project "$PROJECT_ID" \
    --recursive "gs://${PROJECT_ID}_cloudbuild/source"
fi

cat <<EOF

Relay is deployed in ${REGION} with scale-to-zero:
  payments  ${PAYMENTS_URL}  (IAM-authenticated)
  commerce  ${COMMERCE_URL}  (IAM-authenticated)
  shopping  ${SHOPPING_URL}  (IAM-authenticated)
  buyer     ${BUYER_URL}  (public demo)
  mcp       ${MCP_URL}/mcp  (public edge; X-Relay-API-Key required)

Runtime service accounts:
  payments  ${PAYMENTS_SA}
  commerce  ${COMMERCE_SA}
  shopping  ${SHOPPING_SA}
  buyer     ${BUYER_SA}
  mcp       ${MCP_SA}

No --min-instances setting is used. Artifact Registry image copies were
cleanup-enabled=${CLEANUP_IMAGES:-true}; Cloud Run retains the imported revisions.
Cloud Build source cleanup-enabled=${CLEANUP_BUILD_SOURCES:-true}.
EOF
