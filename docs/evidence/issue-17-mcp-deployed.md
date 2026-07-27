# Issue #17 — deployed MCP verification

Verified on 2026-07-27 against the approved Cloud Run deployment in
`web3research/us-central1`.

## Remote Streamable HTTP MCP

- endpoint:
  `https://mcp-1018608922006.us-central1.run.app/mcp`
- transport: stateless Streamable HTTP
- unauthenticated `tools/list`: HTTP `401`
  `{"error":"invalid or missing MCP API key"}`
- authenticated official Python MCP SDK tool list:
  `authorize_payment`, `get_order_status`, `refund_order`, `request_quote`,
  `search_products`, `settle`, `wallet_balances`
- MCP SDK DNS-rebinding protection: enabled with both Cloud Run hostnames
  allowlisted

## Deployed MCP money lifecycle

One remote MCP client session performed quote, autonomous buyer-wallet
signing/broadcast, settlement, Shopify lookup, and refund:

- amount: 3.45 devnet USDC
- Relay order: `ord_1d206740c6f44c358f2211ebfd6d71fa`
- Shopify order: `gid://shopify/Order/8711153156382` (`#1015`)
- lookup before refund: `PAID`, `UNFULFILLED`
- final financial status: `REFUNDED`
- payment:
  [FgQvnY6w…fzVcve](https://explorer.solana.com/tx/FgQvnY6w5VAcdmk3C69QStb8h4acRKrebk7caq8uHLbEmTdRbrmvqarXWjpG29Yagf2ihWa53Hodkp6AkfzVcve?cluster=devnet)
- refund:
  [2goDXD3x…js8KjPh](https://explorer.solana.com/tx/2goDXD3xGeQLKpXYUYYanDjVVpsT2kv6kZgbS2ZUoPVTbP1HgFAKd1W2mzsSvXdTUxnrNSvYM2hvgPrJyjs8KjPh?cluster=devnet)

## Deployed `/buy` regression

The original autonomous buyer API also completed on the refreshed stack:

- buyer API: `https://buyer-1018608922006.us-central1.run.app`
- result: `paid`
- Relay order: `ord_409c744c74734669ab11c26d2e55aee8`
- Shopify order: `gid://shopify/Order/8711154237726` (`#1016`)
- payment:
  [5PcwQQt6…a6xUe8t](https://explorer.solana.com/tx/5PcwQQt623RTUkx7Z7fBnHkiVS1MQ7sZpvC7tnjcWzYEwHZCCJpkKDnmkRzFnkjd8UaMy6v9nBmg5sEuMa6xUe8t?cluster=devnet)
- cleanup refund through MCP:
  [4KWneL6N…tpLA7a6](https://explorer.solana.com/tx/4KWneL6NTjXn9CC5cv18Tsm1LTavWqPokXt3zk6jBRtwRworWqqX1Mo36erer2ck6D4ZWUF6opab5j92mtpLA7a6?cluster=devnet)

The buyer service root returns `404`; the Shopify storefront widget remains
the browser demo surface. `BUYER_CORS_ORIGINS` contains:

```text
https://solanagcp.myshopify.com
https://buyer-763kssfe2q-uc.a.run.app
https://buyer-1018608922006.us-central1.run.app
```

Preflights from the storefront and project-number buyer URL echoed their exact
origin in `Access-Control-Allow-Origin`.

## Cloud Run inventory and cost controls

`gcloud run services list --project web3research --platform managed`:

| Service | Region | Latest ready revision | Min scale | Max scale | Concurrency |
|---|---|---|---|---|---|
| payments | `us-central1` | `payments-00002-s5z` | absent | 2 | 20 |
| commerce | `us-central1` | `commerce-00003-whw` | absent | 2 | 20 |
| shopping | `us-central1` | `shopping-00003-bg2` | absent | 2 | 20 |
| buyer | `us-central1` | `buyer-00007-mnx` | absent | 2 | 20 |
| mcp | `us-central1` | `mcp-00003-6sz` | absent | 2 | 20 |

All five services use 1 vCPU, 512 MiB, CPU throttling, and no min-instances
setting. Payments, commerce, and shopping have no `allUsers` invoker; buyer and
the API-key-protected MCP edge are public.

The MCP runtime references `relay-mcp-api-key:2`. Its unusable newline-bearing
first version was destroyed, leaving exactly one enabled MCP key version and
six active Relay secret versions in total. The approved rollout and corrective
MCP build removed all Artifact Registry image copies and Cloud Build source
archives after Cloud Run imported them, so no image-storage resource remains.

## Checks

```text
agents/.venv/bin/python -m pytest -q agents/tests
  26 passed

pnpm -r typecheck
  passed

bash -n scripts/deploy-cloudrun.sh scripts/provision-cloudrun-secrets.sh
  passed
```
