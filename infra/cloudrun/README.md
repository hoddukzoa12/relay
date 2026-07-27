# Cloud Run deployment

Relay is live in `web3research` on `us-central1` (Iowa). This region is
intentional: the Cloud Run free tier is priced from `us-central1`; Seoul
(`asia-northeast3`) is not covered.

## Live services

| Service | URL | Access | Runtime identity |
|---|---|---|---|
| payments | https://payments-763kssfe2q-uc.a.run.app | IAM only | `relay-payments@web3research.iam.gserviceaccount.com` |
| commerce | https://commerce-763kssfe2q-uc.a.run.app | IAM only | `relay-commerce@web3research.iam.gserviceaccount.com` |
| shopping | https://shopping-763kssfe2q-uc.a.run.app | IAM only | `relay-shopping@web3research.iam.gserviceaccount.com` |
| buyer | **https://buyer-763kssfe2q-uc.a.run.app** | public | `relay-buyer@web3research.iam.gserviceaccount.com` |
| mcp | **https://mcp-1018608922006.us-central1.run.app/mcp** | public edge; API key required | `relay-mcp@web3research.iam.gserviceaccount.com` |

The three private services keep `ingress=all` because peer calls use their
`run.app` URLs, but Cloud Run rejects callers without `roles/run.invoker`.
The Python peer client obtains audience-bound Google ID tokens from the Cloud
Run metadata server. Buyer and MCP have `allUsers` invoker bindings, but MCP
rejects initialization, discovery, and tool calls unless `X-Relay-API-Key`
matches its Secret Manager value.

Every service uses 1 vCPU, 512 MiB, concurrency 20, and a maximum of two
instances. There is deliberately no `--min-instances` setting, so all five
services scale to zero.

## Exact deploy

Prerequisites:

- `gcloud` authenticated to an account that can administer project
  `web3research`;
- billing enabled and the Cloud Run, Cloud Build, Artifact Registry, IAM, and
  Secret Manager APIs enabled;
- `.env` populated from `.env.example`;
- `wallets/merchant.json` and `wallets/buyer.json` present locally;
- Node 20+, pnpm, and Python 3.11+.

Deployment changes cloud state and must not run without explicit approval.
After approval, run:

```bash
make setup
export PROJECT_ID=web3research

# Creates secrets only when absent. A repeat never adds billable versions.
./scripts/provision-cloudrun-secrets.sh

# Builds three images, deploys five services, wires URLs/IAM, and removes the
# Artifact Registry image copies after Cloud Run imports them.
./scripts/deploy-cloudrun.sh
```

`deploy-cloudrun.sh` refuses any region except `us-central1`. It uses the
checked-in [`cloudbuild.yaml`](cloudbuild.yaml), starts buyer with
`agentic_broker.buyer.server`, and starts shopping with the image default. It
sets all required non-secret values on all services and then wires:

- shopping → `PAYMENTS_SERVICE_URL` + `COMMERCE_SERVICE_URL`;
- buyer → `PAYMENTS_SERVICE_URL` + `COMMERCE_SERVICE_URL` +
  `SHOPPING_AGENT_URL`;
- buyer CORS → `https://solanagcp.myshopify.com` + both official buyer service
  hostnames;
- mcp → all four service URLs, with IAM invocation on the three private peers;
- mcp public edge → `CLERK_ISSUER`, `CLERK_JWKS_URL`, and
  `CLERK_SECRET_KEY=relay-clerk-secret-key:latest` for user OAuth plus
  `MCP_API_KEY=relay-mcp-api-key:latest` for trusted service clients, with both
  Cloud Run hostnames allowlisted for MCP SDK DNS-rebinding protection.

The deployment deletes the three Artifact Registry image packages after Cloud
Run imports the revisions. This avoids ongoing image-storage cost; a repeat
simply rebuilds them.

## Secret Manager layout

Never pass secret values through `--set-env-vars`. The provisioner creates six
active versions, within Secret Manager's account-wide free allowance:

| Secret | Mounted as | Service |
|---|---|---|
| `relay-wallets` | `/secrets/wallets.json` | payments |
| `relay-google-api-key` | `GOOGLE_API_KEY` | shopping, buyer |
| `relay-shopify-client-id` | `SHOPIFY_CLIENT_ID` | commerce |
| `relay-shopify-client-secret` | `SHOPIFY_CLIENT_SECRET` | commerce |
| `relay-clerk-secret-key` | `CLERK_SECRET_KEY` | buyer, mcp |
| `relay-mcp-api-key` | `MCP_API_KEY` | mcp |

`relay-wallets` is JSON with `MERCHANT_WALLET_SECRET` and
`BUYER_WALLET_SECRET`, both base58-encoded 64-byte Solana keypairs. Bundling the
two wallet values keeps the deployment within the free allowance; payments
loads the same two runtime names through `WALLET_SECRET_BUNDLE_PATH`. The local
wallet JSON files and `.env` are excluded by both `.gitignore` and
`.gcloudignore`.

## Verify

Health and access boundaries:

```bash
export PROJECT_ID=web3research
export REGION=us-central1
TOKEN="$(gcloud auth print-identity-token)"

curl -H "Authorization: Bearer $TOKEN" \
  https://payments-763kssfe2q-uc.a.run.app/health
curl -H "Authorization: Bearer $TOKEN" \
  https://commerce-763kssfe2q-uc.a.run.app/health
curl -H "Authorization: Bearer $TOKEN" \
  https://shopping-763kssfe2q-uc.a.run.app/health
curl https://buyer-763kssfe2q-uc.a.run.app/health
curl https://buyer-763kssfe2q-uc.a.run.app/

# Each private URL returns 403 when the Authorization header is omitted.
# The buyer root returns 404: the Shopify widget is the browser demo surface.
# MCP /health is public, while /mcp returns 401 without X-Relay-API-Key.
```

Region and scale-to-zero:

```bash
gcloud run services list \
  --project "$PROJECT_ID" \
  --region "$REGION" \
  --format='table(metadata.name,status.url)'

for service in payments commerce shopping buyer mcp; do
  gcloud run services describe "$service" \
    --project "$PROJECT_ID" \
    --region "$REGION" \
    --format='yaml(metadata.name,spec.template.metadata.annotations)'
done
# autoscaling.knative.dev/minScale is absent on every service.
```

MCP transport and live money path:

```bash
RELAY_MCP_URL=https://mcp-1018608922006.us-central1.run.app/mcp \
MCP_API_KEY="$MCP_API_KEY" \
  agents/.venv/bin/python scripts/mcp-client.py --purchase --refund
```

The current five-service deployment was verified on 2026-07-27 through MCP:

- amount: 3.45 devnet USDC;
- Shopify order: `gid://shopify/Order/8711153156382` (`#1015`);
- payment:
  https://explorer.solana.com/tx/FgQvnY6w5VAcdmk3C69QStb8h4acRKrebk7caq8uHLbEmTdRbrmvqarXWjpG29Yagf2ihWa53Hodkp6AkfzVcve?cluster=devnet;
- refund:
  https://explorer.solana.com/tx/2goDXD3xGeQLKpXYUYYanDjVVpsT2kv6kZgbS2ZUoPVTbP1HgFAKd1W2mzsSvXdTUxnrNSvYM2hvgPrJyjs8KjPh?cluster=devnet.

See
[`../../docs/evidence/issue-17-mcp-deployed.md`](../../docs/evidence/issue-17-mcp-deployed.md)
for the tool list, `/buy` regression, CORS, IAM, and scale-to-zero evidence.

Finish with:

```bash
pnpm -r typecheck
```
