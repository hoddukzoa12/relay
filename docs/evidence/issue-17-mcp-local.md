# Issue #17 — local MCP verification

Verified locally on 2026-07-27 against the live Shopify catalog and Solana
devnet before the deployment approval gate opened. The later approved Cloud
Run proof is in
[`issue-17-mcp-deployed.md`](issue-17-mcp-deployed.md).

## Streamable HTTP and authentication

Endpoint: `http://127.0.0.1:8092/mcp`

An unauthenticated `tools/list` POST returned:

```text
HTTP 401
{"error":"invalid or missing MCP API key"}
```

The official Python MCP SDK connected with `X-Relay-API-Key` and listed:

```text
authorize_payment
get_order_status
refund_order
request_quote
search_products
settle
wallet_balances
```

The buyer service root now returns `404`; the service retains its public API
routes, while the Shopify widget is the only browser demo surface.

## Full MCP money lifecycle

One `scripts/mcp-client.py --purchase --refund` invocation completed quote,
autonomous signing/broadcast, settlement, Shopify lookup, and refund through
the MCP endpoint.

- amount: 3.45 devnet USDC
- real Shopify variant: `gid://shopify/ProductVariant/59695017197854`
- SKU: `RELAY-AUDIO-EARBUD-MINI`
- Relay order: `ord_e3c8491ce77a46b0ae3e8f3862f28c10`
- Shopify order: `gid://shopify/Order/8710973948190` (`#1013`)
- final financial status: `REFUNDED`
- payment:
  [5a9bjXhn…RbPdCp](https://explorer.solana.com/tx/5a9bjXhn5wdJ9D1PKr3rjCJaV7zTzP2D9gfCUFfbwxKdzNuYX4uxQ4uxo7sHVKniRiot1Kzyz4CAkcirM8RbPdCp?cluster=devnet)
- refund:
  [3YfBLu5V…ZYh6ak](https://explorer.solana.com/tx/3YfBLu5VNpuTZVivpx1kgXKsmgp6GtxximnB63ieWRWfS1CjAgZTRLRypJdbKqMVHFDZPBCQgnvFP1EkMKZYh6ak?cluster=devnet)

The order lookup returned the exact payment reference and proof before the
refund. The refund result returned a distinct reference and explorer proof,
and Shopify moved from `PAID` to `REFUNDED`.

## Existing `/buy` regression

The original autonomous buyer route also completed successfully:

- command: `BUYER_AGENT_URL=http://127.0.0.1:8090 ./scripts/demo.sh "wireless earbuds" 5`
- Shopify order: `gid://shopify/Order/8710973063454` (`#1012`)
- payment:
  [3gWLiEcz…mb1Jrk](https://explorer.solana.com/tx/3gWLiEcztd4M3sigNBPccjBEpovtLwL9P1tN3fgSvNPWyQpkbEbjhePQf8RsjYSSsraHMxxH6eS3EfPFLvmb1Jrk?cluster=devnet)
- cleanup refund through MCP:
  [3DmXBYiH…Yo1cXQ](https://explorer.solana.com/tx/3DmXBYiHEnqAr4vDXoL8maQT4XD6GEUPuMd9qde9UfSeSKt6Mzw65PTaqVFfGZc2j5egTAX1UzDe9vhm2wYo1cXQ?cluster=devnet)

## Static and unit verification

```text
pnpm -r typecheck
  packages/shared: passed
  services/payments: passed
  services/commerce: passed

agents/.venv/bin/python -m pytest -q agents/tests
  25 passed

bash -n scripts/deploy-cloudrun.sh \
  scripts/provision-cloudrun-secrets.sh scripts/dev.sh
  passed

python -m compileall agents/agentic_broker scripts/mcp-client.py
  passed

docker build --file agents/Dockerfile agents
  passed (Python 3.11 image with MCP 1.28.1)
```

## Pre-deployment read-only Cloud Run inventory

No Cloud Run or build mutation command was executed. A read-only inspection of
`web3research/us-central1` found the existing four services and no MCP service:

| Service | Current revision | Current image tag | Min scale |
|---|---|---|---|
| payments | `payments-00001-fqc` | `da63c2e-20260726163228` | absent |
| commerce | `commerce-00002-tmb` | `shopifyretry-20260726164747` | absent |
| shopping | `shopping-00002-nql` | `authdiag-20260726164143` | absent |
| buyer | `buyer-00003-rxb` | `authdiag-20260726164143` | absent |

At this pre-deployment checkpoint the account had five `relay-*` secrets and
four `relay-*` runtime service accounts. The later approved rollout added the
sixth secret and MCP runtime identity.
