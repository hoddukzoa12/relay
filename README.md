# Agentic Resell Broker

> **Google Cloud × Solana AI Agentic Hackathon** · Track C: Multi-Agent Commerce

A buyer agent delegates a purchase; a **shopping (broker) agent** sources the
product, sets a resale price, and issues an **agent-native payment request**; the
buyer's wallet **signs it autonomously — no human click** — and settles in
**on-chain USDC on Solana devnet**. The broker verifies the payment on-chain by
`reference`, then records a paid order in Shopify. After purchase, the same
agent can look up, fulfill, track, and fully refund the order; refunds move USDC
back **merchant → buyer** on-chain.

**Why on-chain?** Two agents that don't trust each other, with no bank account,
no card, and no human in the loop, settle instantly and verifiably. That's the
one sentence the whole design defends.

**A2A로 협상 · AP2 mandate로 인가 · Solana Pay로 정산.** (USDC 온체인)

**계정도 비밀번호도 없다. 지갑이 곧 계정이고, 서명이 곧 로그인.**

---

## Architecture

```
          ┌──────────── SCORED (all live) ─────────────┐
          │                                             │
  ┌───────────────┐   A2A (HTTP)   ┌────────────────┐   │
  │  Buyer agent   │ ─────────────► │ Shopping agent │   │
  │ (Python · ADK) │ ◄───────────── │ (Python · ADK) │   │
  └──────┬─────────┘  payment req   └───┬────────┬───┘   │
         │ autonomous sign              │ verify │ order │
         ▼                              ▼        ▼       │
  ┌─────────────────────────┐   ┌──────────────┐ ┌──────────────┐
  │  payments (TS)          │   │ payments     │ │ commerce (TS)│
  │  @solana/web3.js + pay  │   │ findReference│ │ Shopify Admin│
  └───────────┬─────────────┘   │ validateXfer │ │ orderCreate  │
              │ USDC transfer    └──────┬───────┘ │ +markAsPaid  │
              ▼   (reference tag)       ▼         └──────────────┘
        ┌───────────────────────────────────────┐
        │        Solana devnet  (~400ms)         │  ← explorer tx = proof
        └───────────────────────────────────────┘
          │                                             │
          └────────── backstage (out of demo) ──────────┘
                 Leg 2: we buy the real item & ship
```

- **Leg 1** (buyer → broker, on-chain USDC) is the scored path — 100% live.
- **Leg 2** (broker → external merchant, card rails) is operational — off-stage.

See [`PRD.md`](./PRD.md) for the full product spec; the message flow is
PRD §5, the data contracts are PRD §6.

## Repo layout

```
agents/                 Python · Google ADK + Gemini
  agentic_broker/
    common/             config · contracts · service clients · Gemini helpers
    buyer/              delegated buyer API              → :8090
    shopping/           broker agent                     → :8091
    mcp/                remote Streamable HTTP MCP server → :8092
services/               TypeScript
  payments/             Solana Pay: create request · sign · verify → :8081
  commerce/             Shopify Admin API (mock-able)            → :8082
packages/shared/        canonical schemas + TS types (PRD §6)
infra/                  docker-compose · Cloud Run
scripts/                catalog seed · dev.sh · demo.sh · deploy-cloudrun.sh
wallets/                your solana keypairs (git-ignored)
```

## Prerequisites

- **Node ≥ 20** + **pnpm** (`corepack enable`)
- **Python ≥ 3.11**
- **Solana CLI** (for keypairs / airdrops) — you already have wallets + devnet USDC
- Optional now, needed for the full demo:
  - **Gemini API key** (free tier) — https://aistudio.google.com/apikey
  - **Shopify dev store** + Admin API token with order, product, and inventory
    read/write scopes
  - **gcloud** for Cloud Run

## Setup

```bash
# 1. Config
cp .env.example .env          # then edit — at minimum confirm USDC_MINT + wallet paths
# For wallet identity, also set CLERK_PUBLISHABLE_KEY, CLERK_SECRET_KEY,
# CLERK_ISSUER, and CLERK_JWKS_URL.
# For remote MCP, generate MCP_API_KEY and keep it out of source control.

# 2. Wallets (you already have these)
mkdir -p wallets
cp /path/to/merchant.json wallets/merchant.json
cp /path/to/buyer.json    wallets/buyer.json

# 3. Install
make setup                    # pnpm install + python venv (agents/.venv)

# 4. Sanity-check balances (SOL for fees + devnet USDC)
make check-wallets

# 5. Fallback only: seed the demo catalog when no supplier catalog is active
pnpm seed:catalog:fallback
```

> **USDC mint:** `.env` defaults to Circle's devnet USDC
> (`4zMMC9srt5Ri5X14GAgXhaHii3GnPAEERYPJgZJDncDU`). Make sure it matches the token
> you airdropped, or the transfer will move the wrong asset.

## Run it

**Option A — one command (Docker):**

```bash
make compose-up               # payments, commerce, shopping, buyer, MCP
curl http://localhost:8090/health
```

**Option B — local processes:**

```bash
./scripts/dev.sh              # starts all five; buyer API at http://localhost:8090
```

Use the Shopify storefront widget for the browser demo, or exercise the
autonomous agent path directly:

```bash
./scripts/demo.sh "wireless earbuds" 5
# or the buyer CLI:
cd agents && ./.venv/bin/python -m agentic_broker.buyer.cli --query "wireless earbuds" --budget 5
```

You get back a `txSignature` and an **explorer link** — the on-chain proof.

### Remote MCP

Relay is also an MCP server at `/mcp` using the production-oriented
**Streamable HTTP** transport. It is not a local-only stdio adapter. Every MCP
request must include `X-Relay-API-Key`; a missing or invalid key is rejected
before tool discovery or execution.

```json
{
  "mcpServers": {
    "relay": {
      "type": "http",
      "url": "http://localhost:8092/mcp",
      "headers": {
        "X-Relay-API-Key": "${MCP_API_KEY}"
      }
    }
  }
}
```

List and validate all seven tools with the official Python MCP client:

```bash
MCP_API_KEY="$MCP_API_KEY" \
  agents/.venv/bin/python scripts/mcp-client.py

# Run quote → autonomous pay → settle → lookup, then refund:
MCP_API_KEY="$MCP_API_KEY" \
  agents/.venv/bin/python scripts/mcp-client.py --purchase --refund
```

The MCP tools are `search_products`, `request_quote`, `authorize_payment`,
`settle`, `get_order_status`, `refund_order`, and `wallet_balances`. They are
thin wrappers over the same shared service clients and deterministic workflow
used by `/buy`; payment signing and on-chain verification are not duplicated.
See [`docs/MCP.md`](docs/MCP.md) for tool inputs, authentication, remote client
configuration, and deployment verification.

### Optional human wallet identity and delegation

The Shopify widget can add a human identity without making sign-in
mandatory for agents:

1. Select **Wallet sign-in**. Clerk performs Sign in with Solana and also acts
   as Shopify's configured OIDC identity provider.
2. The buyer backend validates the Clerk session JWT (RS256 signature, expiry,
   and issuer) against the cached JWKS, then reads the verified Solana wallet
   from the Clerk user record.
3. On **Delegate**, that same browser wallet signs exactly one AP2
   `IntentMandate` containing the price ceiling and 15-minute expiry.
4. The broker's separate agent wallet signs the `PaymentMandate` and sends
   USDC autonomously. There is no per-payment human approval and the human
   wallet never pays.
5. **My orders** queries Shopify orders whose `buyer_wallet` custom attribute
   matches the signed-in identity wallet.

The existing `POST /buy`, CLI, A2A, and MCP-style autonomous paths require no
Clerk session and retain the configured agent payment wallet behavior.

Shopify OIDC requires an email claim with `email_verified: true`. The demo Clerk
user therefore has both a Solana Web3 wallet and a verified email; wallet-only
users must collect/verify an email in Clerk before Shopify customer-account
login can complete.

To embed the same autonomous flow in a Shopify product-page Custom Liquid
section, follow [`docs/DEMO-shopify-embed.md`](docs/DEMO-shopify-embed.md). The
storefront widget calls the buyer agent over HTTPS; it never opens Shopify
checkout.

## The scored path (Leg 1), step by step

This maps to PRD §5, steps 5–8 (the judging core):

1. **Buyer → Shopping** A2A JSON-RPC `POST /a2a` `message/send` — sends an
   `IntentMandate` as an A2A DataPart. It is signed by the authenticated human
   wallet when present, otherwise by the configured buyer agent wallet. The
   original `POST /a2a/quote` route remains available for REST compatibility.
2. **Shopping** queries live Shopify variants, removes out-of-stock and
   marked-up-over-budget candidates, then uses Gemini (or deterministic
   relevance) only to rank real SKUs. It calls
   **payments** `POST /payment-requests` → mints a fresh `reference` pubkey and
   returns a merchant-wallet-signed `CartMandate` containing the unchanged
   [PaymentRequest](packages/shared/schemas/payment-request.schema.json) plus
   the selected real SKU and variant ID.
3. **Buyer** verifies the CartMandate, signs a bound `PaymentMandate`, then calls
   **payments** `POST /pay` — the wallet **signs and broadcasts** a USDC SPL
   transfer tagged with the `reference` key. **No human approval.**
4. **Buyer → Shopping** A2A `message/send` carries the `PaymentMandate` and
   `txSignature`; the legacy `POST /a2a/settle` route is still available.
5. **Shopping** calls **payments** `POST /verify` → `@solana/pay` `findReference`
   locates the tx and `validateTransfer` confirms **amount + recipient + reference**
   on-chain, trustlessly.
6. **Shopping** calls **commerce** `POST /orders` → Shopify `orderCreate` with
   the real `variantId` and the broker resale `priceSet`, then
   `orderMarkAsPaid`, and returns an
   [OrderConfirmation](packages/shared/schemas/order-confirmation.schema.json)
   with the explorer link.

The critical invariant: the buyer is handed **an agent-native payment request,
never a Shopify web-checkout link** — so the wallet can sign without a human
click. That's what makes it *autonomous* (PRD §7).

## Post-purchase lifecycle

Relay exposes the complete agent-owned lifecycle:

```text
payment → lookup → fulfillment → tracking → full refund
```

- `GET /orders/{orderRef-or-name}` on the shopping agent returns financial and
  fulfillment state, real SKU line items, the paid amount, and payment/refund
  explorer proof. `shopping/tools.py:get_order_status` is the reusable primitive
  for the MCP `get_order_status` tool in #17.
- `POST /orders/{orderRef}/fulfill` creates Shopify fulfillment records from
  `fulfillmentOrders` and attaches a carrier + tracking number.
- `GET /orders/{orderRef-or-name}/tracking` uses a replaceable official EasyPost
  Tracker API adapter when `EASYPOST_API_KEY` is configured.
- `POST /orders/{orderRef-or-name}/refund` first re-verifies the original Solana
  Pay transfer, then returns the full USDC amount from merchant to buyer and
  records the refund proof in Shopify.

The included tracking number (`EZ2000000002`) is an EasyPost test value.
**It is demo data, not a real parcel or shipping claim.** See
[`docs/ORDER-LIFECYCLE.md`](docs/ORDER-LIFECYCLE.md) for endpoint details,
idempotency boundaries, and the real devnet evidence.

## Deploy (Cloud Run)

Live deployment (`web3research`, `us-central1`):

| Service | URL | Access |
|---|---|---|
| payments | https://payments-763kssfe2q-uc.a.run.app | IAM only |
| commerce | https://commerce-763kssfe2q-uc.a.run.app | IAM only |
| shopping | https://shopping-763kssfe2q-uc.a.run.app | IAM only |
| buyer | **https://buyer-763kssfe2q-uc.a.run.app** | public API |
| mcp | **https://mcp-1018608922006.us-central1.run.app/mcp** | public edge; API key required |

To reproduce from the prepared `.env` and local throwaway wallets:

```bash
make setup
export PROJECT_ID=web3research
./scripts/provision-cloudrun-secrets.sh
./scripts/deploy-cloudrun.sh

BUYER_AGENT_URL=https://buyer-763kssfe2q-uc.a.run.app \
  ./scripts/demo.sh "wireless earbuds" 5
```

The deploy is hard-pinned to `us-central1`, uses scale-to-zero (no minimum
instances), locks the three backend services behind IAM, protects MCP tools
with a Secret Manager-backed shared secret, and removes imported
image copies from Artifact Registry to avoid ongoing storage cost. Wallet keys,
Gemini, Shopify client credentials, and the Clerk secret are all supplied
through Secret Manager—not plain environment flags. See
[`infra/cloudrun/README.md`](infra/cloudrun/README.md) for the exact secret
layout, access checks, and teardown-free verification commands.

Latest deployed proof (2026-07-27): **paid** 3.45 devnet USDC for real Shopify
variant `gid://shopify/ProductVariant/59695017197854`, recorded as paid order
`gid://shopify/Order/8709779915038`.
[View the Solana transaction](https://explorer.solana.com/tx/5Agbz4X5RByc2jMWzd9mH3WuZQkFnqy736UFthMcj2uTrGpaUz6StfaU7sjwx6kfrGJk5cM9rjn9VbvCTyd8AsRy?cluster=devnet).

## Judging-criteria map (PRD §8)

| Criterion | Where |
|---|---|
| ① Product intro (target/problem/model/architecture) | `PRD.md` + this README |
| ② GitHub (reproducible code + README) | this repo; `make setup && make compose-up` |
| ③ 3-min demo (real payment, end-to-end) | `scripts/demo.sh` → explorer tx |
| ④ Innovation / AI use / infra | headless payment endpoint · Gemini sourcing · Solana Pay + Cloud Run |

**Definition of Done (PRD §12):** complete — a real devnet USDC tx is visible
on the explorer, payment required zero human clicks, Shopify recorded a paid
order, the buyer has a live HTTPS URL, and the scripted repro completes in
under three minutes.

## Progressive setup (works before everything is ready)

- **No Gemini key?** Sourcing uses deterministic relevance over the same
  catalog candidates; it never invents a product. Intent parsing also has a
  deterministic fallback.
- **No Shopify yet?** `COMMERCE_MOCK=true` (default) serves a fixed demo catalog
  and returns a mock order id — the on-chain payment is still 100% real. Flip
  to `false` and run `pnpm seed:catalog:fallback` only when no supplier
  catalog is active.
- **Wallets are required** for a real tx (you have them).

## Troubleshooting

| Symptom | Fix |
|---|---|
| `Missing required env var` | copy `.env.example` → `.env` and fill it |
| `No key configured for buyer` | set `BUYER_WALLET_KEYPAIR_PATH` (or `_SECRET`) |
| transfer fails / `insufficient funds` | `make check-wallets`; airdrop SOL for fees + confirm USDC mint |
| verify stuck on `pending` | devnet lag; the broker polls — retry `/verify`, or RPC failover kicks in |
| wrong asset moved | `USDC_MINT` in `.env` doesn't match your airdropped token |

## Security notes

- Wallet keys and API tokens live only in `.env` / Secret Manager — never
  committed (`.gitignore` enforces this).
- MCP is publicly routable for remote clients, but the complete protocol
  endpoint (including tool discovery) fails closed unless `X-Relay-API-Key`
  matches `relay-mcp-api-key`. Payments, commerce, and shopping remain private
  behind Cloud Run IAM.
- Payment, refund, and fulfillment compare-and-set state is process-local
  (in-memory); a restart forgets payment-service request state. Shopify custom
  attributes preserve completed ledger proofs, but Firestore/Redis is required
  before production.
- Devnet only. Do not point this at mainnet without an escrow/settlement review.

## License

MIT — see [LICENSE](LICENSE).
