# Agentic Resell Broker

> **Google Cloud × Solana AI Agentic Hackathon** · Track C: Multi-Agent Commerce

A buyer agent delegates a purchase; a **shopping (broker) agent** sources the
product, sets a resale price, and issues an **agent-native payment request**; the
buyer's wallet **signs it autonomously — no human click** — and settles in
**on-chain USDC on Solana devnet**. The broker verifies the payment on-chain by
`reference`, then records a paid order in Shopify.

**Why on-chain?** Two agents that don't trust each other, with no bank account,
no card, and no human in the loop, settle instantly and verifiably. That's the
one sentence the whole design defends.

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
    buyer/              delegated buyer (+ demo web UI)  → :8090
    shopping/           broker agent                     → :8091
services/               TypeScript
  payments/             Solana Pay: create request · sign · verify → :8081
  commerce/             Shopify Admin API (mock-able)            → :8082
packages/shared/        canonical schemas + TS types (PRD §6)
infra/                  docker-compose · Cloud Run
scripts/                dev.sh · demo.sh · deploy-cloudrun.sh
wallets/                your solana keypairs (git-ignored)
```

## Prerequisites

- **Node ≥ 20** + **pnpm** (`corepack enable`)
- **Python ≥ 3.11**
- **Solana CLI** (for keypairs / airdrops) — you already have wallets + devnet USDC
- Optional now, needed for the full demo:
  - **Gemini API key** (free tier) — https://aistudio.google.com/apikey
  - **Shopify dev store** + Admin API access token
  - **gcloud** for Cloud Run

## Setup

```bash
# 1. Config
cp .env.example .env          # then edit — at minimum confirm USDC_MINT + wallet paths

# 2. Wallets (you already have these)
mkdir -p wallets
cp /path/to/merchant.json wallets/merchant.json
cp /path/to/buyer.json    wallets/buyer.json

# 3. Install
make setup                    # pnpm install + python venv (agents/.venv)

# 4. Sanity-check balances (SOL for fees + devnet USDC)
make check-wallets
```

> **USDC mint:** `.env` defaults to Circle's devnet USDC
> (`4zMMC9srt5Ri5X14GAgXhaHii3GnPAEERYPJgZJDncDU`). Make sure it matches the token
> you airdropped, or the transfer will move the wrong asset.

## Run it

**Option A — one command (Docker):**

```bash
make compose-up               # payments, commerce, shopping, buyer
open http://localhost:8090    # demo console
```

**Option B — local processes:**

```bash
./scripts/dev.sh              # starts all four; buyer UI at http://localhost:8090
```

Then either click **"에이전트에게 구매 위임"** in the UI, or:

```bash
./scripts/demo.sh "wireless earbuds" 25
# or the buyer CLI:
cd agents && ./.venv/bin/python -m agentic_broker.buyer.cli --query "wireless earbuds" --budget 25
```

You get back a `txSignature` and an **explorer link** — the on-chain proof.

## The scored path (Leg 1), step by step

This maps to PRD §5, steps 5–8 (the judging core):

1. **Buyer → Shopping** `POST /a2a/quote` — "buy X, budget Y, ship to Z".
2. **Shopping** sources a product + price (Gemini), then calls
   **payments** `POST /payment-requests` → mints a fresh `reference` pubkey and
   returns a [PaymentRequest](packages/shared/schemas/payment-request.schema.json).
3. **Buyer** calls **payments** `POST /pay` — the wallet **signs and broadcasts**
   a USDC SPL transfer, tagging it with the `reference` key. **No human approval.**
4. **Buyer → Shopping** `POST /a2a/settle` with the `txSignature`.
5. **Shopping** calls **payments** `POST /verify` → `@solana/pay` `findReference`
   locates the tx and `validateTransfer` confirms **amount + recipient + reference**
   on-chain, trustlessly.
6. **Shopping** calls **commerce** `POST /orders` → Shopify `orderCreate` +
   `orderMarkAsPaid`, and returns an
   [OrderConfirmation](packages/shared/schemas/order-confirmation.schema.json)
   with the explorer link.

The critical invariant: the buyer is handed **an agent-native payment request,
never a Shopify web-checkout link** — so the wallet can sign without a human
click. That's what makes it *autonomous* (PRD §7).

## Deploy (Cloud Run)

```bash
export PROJECT_ID=your-gcp-project
./scripts/deploy-cloudrun.sh
```

Secrets (wallet keys, Gemini key, Shopify token) go through Secret Manager, and
service URLs are wired together after the first deploy — see
[`infra/cloudrun/README.md`](infra/cloudrun/README.md). The **buyer** service URL
is your public demo link.

## Judging-criteria map (PRD §8)

| Criterion | Where |
|---|---|
| ① Product intro (target/problem/model/architecture) | `PRD.md` + this README |
| ② GitHub (reproducible code + README) | this repo; `make setup && make compose-up` |
| ③ 3-min demo (real payment, end-to-end) | `scripts/demo.sh` → explorer tx |
| ④ Innovation / AI use / infra | headless payment endpoint · Gemini sourcing · Solana Pay + Cloud Run |

**Definition of Done (PRD §12):** a real devnet USDC tx visible on the explorer,
zero human clicks at payment, a `paid` Shopify order, a live URL, ≤3-min repro.

## Progressive setup (works before everything is ready)

- **No Gemini key?** Sourcing/parsing fall back to deterministic stubs — the flow
  still completes. Set `GOOGLE_API_KEY` for real AI.
- **No Shopify yet?** `COMMERCE_MOCK=true` (default) returns a mock order id — the
  on-chain payment is still 100% real. Flip to `false` once your store is ready.
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
- The payment-request store is process-local (in-memory); a restart forgets
  pending requests. Fine for the demo; use Firestore/Redis for production.
- Devnet only. Do not point this at mainnet without an escrow/settlement review.

## License

MIT — see [LICENSE](LICENSE).
