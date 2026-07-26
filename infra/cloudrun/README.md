# Cloud Run deployment

Four services, one region (Seoul = `asia-northeast3`):

| Service   | Port | Public? | Notes |
|-----------|------|---------|-------|
| payments  | 8081 | internal (or public for debug) | holds wallet keys — treat as sensitive |
| commerce  | 8082 | internal | holds Shopify client credentials |
| shopping  | 8091 | internal | broker agent |
| buyer     | 8090 | **public** | this URL is your demo link |

## Secrets (do NOT use `--set-env-vars` for these)

Store in Secret Manager and mount:

```bash
# wallet keypairs (base58 secret is easiest for Cloud Run)
gcloud secrets create merchant-wallet --data-file=- <<< "$MERCHANT_BASE58"
gcloud secrets create buyer-wallet    --data-file=- <<< "$BUYER_BASE58"
gcloud secrets create gemini-key      --data-file=- <<< "$GOOGLE_API_KEY"
gcloud secrets create shopify-client-id     --data-file=- <<< "$SHOPIFY_CLIENT_ID"
gcloud secrets create shopify-client-secret --data-file=- <<< "$SHOPIFY_CLIENT_SECRET"

# then, per service:
gcloud run services update payments --region asia-northeast3 \
  --set-secrets MERCHANT_WALLET_SECRET=merchant-wallet:latest,BUYER_WALLET_SECRET=buyer-wallet:latest
gcloud run services update shopping --region asia-northeast3 \
  --set-secrets GOOGLE_API_KEY=gemini-key:latest
gcloud run services update buyer --region asia-northeast3 \
  --set-secrets GOOGLE_API_KEY=gemini-key:latest
gcloud run services update commerce --region asia-northeast3 \
  --set-secrets SHOPIFY_CLIENT_ID=shopify-client-id:latest,SHOPIFY_CLIENT_SECRET=shopify-client-secret:latest \
  --set-env-vars COMMERCE_MOCK=false,SHOPIFY_STORE_DOMAIN=your-store.myshopify.com
```

Legacy stores may instead mount an existing static Admin API token as
`SHOPIFY_ADMIN_ACCESS_TOKEN`; when both auth paths are configured, the static
token takes precedence.

> On Cloud Run, supply wallets via `*_WALLET_SECRET` (base58), not the
> `*_KEYPAIR_PATH` files — there is no repo `./wallets` dir in the container.
> Convert an id.json to base58 with any bs58 tool, or:
> `python -c "import json,base58,sys; print(base58.b58encode(bytes(json.load(open(sys.argv[1])))).decode())" wallets/buyer.json`

## Wiring URLs

After the first deploy, set each service's outbound URLs to the deployed peers:

```bash
PAY=$(gcloud run services describe payments --region asia-northeast3 --format 'value(status.url)')
COM=$(gcloud run services describe commerce --region asia-northeast3 --format 'value(status.url)')
SHOP=$(gcloud run services describe shopping --region asia-northeast3 --format 'value(status.url)')

gcloud run services update shopping --region asia-northeast3 \
  --set-env-vars "PAYMENTS_SERVICE_URL=$PAY,COMMERCE_SERVICE_URL=$COM"
gcloud run services update buyer --region asia-northeast3 \
  --set-env-vars "PAYMENTS_SERVICE_URL=$PAY,SHOPPING_AGENT_URL=$SHOP"
```

The **buyer** service URL is the live demo link (judging bonus, PRD §8 ④).
