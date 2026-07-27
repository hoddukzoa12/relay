# DSers autonomous catalog sourcing

Relay treats DSers as an additive supplier-catalog capability. The scored
Solana Pay path and every existing Shopify catalog product continue to work
when DSers is disabled, expired, rate-limited, or unavailable.

## One-time human OAuth bootstrap

DSers publishes OAuth 2.1 dynamic client registration, authorization code +
PKCE S256, refresh-token rotation, and RFC 8707 resource binding. It does not
offer `client_credentials`. A human therefore signs into DSers once in a local
browser; Cloud Run never opens a browser.

Prerequisites:

- a DSers account with the Relay Shopify store connected;
- Google Application Default Credentials that can create/update the target
  secret (`gcloud auth application-default login`);
- Secret Manager API enabled;
- `make setup` completed.

Run:

```bash
export GCP_PROJECT_ID=web3research
export DSERS_SECRET_PROJECT_ID="$GCP_PROJECT_ID"
export DSERS_SECRET_ID=relay-dsers-oauth
export DSERS_SECRET_ALIAS=relay-active

agents/.venv/bin/python scripts/bootstrap-dsers-oauth.py \
  --project "$DSERS_SECRET_PROJECT_ID" \
  --secret "$DSERS_SECRET_ID" \
  --alias "$DSERS_SECRET_ALIAS" \
  --verify-rotation
```

The script dynamically registers a public client, starts a loopback callback on
`127.0.0.1:8765`, opens DSers authorization, exchanges the returned code, and
writes the complete OAuth bundle as an immutable Secret Manager version. It
never prints either token. `--verify-rotation` then performs one real refresh,
promotes the rotated token, and constructs a fresh token manager to prove that
a restarted process can read the active grant.

If the grant is irrecoverably invalid, run the same command with `--replace`.
That flag is intentionally required when the active alias already exists.

## Cloud Run access

The shopping service reads and rotates the secret through the Secret Manager
API; it is not injected as an environment variable or mounted file. Grant
`relay-shopping` admin rights on this one secret only:

```bash
gcloud secrets add-iam-policy-binding "$DSERS_SECRET_ID" \
  --project "$DSERS_SECRET_PROJECT_ID" \
  --member \
  "serviceAccount:relay-shopping@${GCP_PROJECT_ID}.iam.gserviceaccount.com" \
  --role roles/secretmanager.admin
```

`scripts/deploy-cloudrun.sh` performs that exact secret-scoped binding when
`DSERS_SECRET_ID` is configured and passes only non-secret identifiers to the
shopping service. Deployment still requires explicit human approval.

## Rotation and concurrency

DSers refresh tokens are valid for 30 days and rotate on use. Relay stores
`client_id`, access token, rotated refresh token, expiry, scope, resource, and a
unique rotation ID in each secret version.

Before calling `/oauth/token`, an instance:

1. reads the parent secret and its ETag;
2. claims a bounded refresh lease in a Secret Manager annotation with an
   ETag-checked update;
3. re-reads the active token after acquiring the lease;
4. sends exactly one refresh request;
5. adds the rotated bundle as a new immutable version;
6. advances `relay-active` and clears the lease in one ETag-checked
   `UpdateSecret`.

A second Cloud Run instance waits for the lease, then re-reads the winner's
fresh access token instead of reusing the old refresh token. Ambiguous Secret
Manager writes are reconciled by rotation ID/version state before any retry.
Failures are logged and surfaced as `DSersSourcingUnavailable`; they never
fall through into a payment mutation.

## Sourcing workflow and guardrails

The commerce catalog is checked first. DSers is called only when no lexically
suitable, in-stock, positive-margin variant fits the budget:

```text
local catalog miss
  → dsers_find_product
  → exact-source-URL staging lookup
  → dsers_product_import (default maximum: 1/request)
  → dsers_product_preview (full variant cost/price/stock)
  → Relay positive-margin check
  → dsers_store_push (sell_immediately, force_push=false)
  → exact supplier-URL live-state lookup
  → Shopify product GID + exact SKU provenance/cost write + readback
```

Safety properties:

- adult, medical, counterfeit, tobacco, weapon, and surveillance queries are
  rejected before the first DSers call;
- the default per-request import cap is one;
- zero-price, below-cost, out-of-budget, or missing-economics previews never
  reach store push;
- a DSers/CloudFront 504 is reconciled through `dsers_import_list` or
  `dsers_my_products`; Relay never blindly repeats a mutation;
- no sourcing code calls `dsers_product_delete` or any Shopify deletion;
- provenance uses Shopify product GID, exact variant SKU, exact supplier URL,
  vendor `Relay DSers Autonomous`, and tags `relay:autonomous-sourced` /
  `relay:dsers`; titles are never identifiers;
- supplier cost, currency, capture date, route, supplier URL, and DSers IDs are
  written to the Admin-only `relay.*` variant metafields used by issue #52.

## Failure behavior

With an expired/missing DSers grant, `search_catalog` returns an explicit
`externalSourcing.status = "unavailable"` message and may show unchanged
products under `fallbackCatalog`. It never labels those products as matches.
An existing-catalog query still quotes, transfers devnet USDC, verifies by
Solana Pay reference, and records Shopify orders exactly as before.

`services/payments` is not involved in sourcing and remains unchanged.
