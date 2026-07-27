# Issue #52 — DSers spike, real-cost evidence, and gated Leg 2 handoff

Investigated and implemented locally on 2026-07-27. No Cloud Run deployment,
payment-service change, product deletion, supplier purchase, fulfillment, or
tracking mutation was performed.

## Authentication spike

The live DSers authorization-server metadata returned:

```json
{
  "issuer": "https://mcp.dsers.com",
  "authorization_endpoint": "https://mcp.dsers.com/oauth/authorize",
  "token_endpoint": "https://mcp.dsers.com/oauth/token",
  "registration_endpoint": "https://mcp.dsers.com/oauth/register",
  "grant_types_supported": ["authorization_code", "refresh_token"],
  "token_endpoint_auth_methods_supported": ["none"],
  "code_challenge_methods_supported": ["S256"]
}
```

The [official DSers MCP guide](https://mcp.dsers.com/get-started) requires OAuth
2.1 + PKCE and says the client opens a browser for DSers sign-in. It explicitly
rejects pasted API keys or hand-written authorization headers. Refresh tokens
are valid for 30 days and rotate when used, but obtaining the initial grant
still requires the interactive authorization-code flow and durable secret
storage. The metadata offers neither `client_credentials` nor a service-account
grant, so a fresh headless Cloud Run instance cannot bootstrap itself.

## Capability result

The official MCP tool catalog exposes 13 tools for:

- store discovery and inventory policy;
- supplier product search;
- import-list/live-product/supplier inspection;
- product import, rule validation/update, and staging deletion;
- store push and two-phase supplier SKU remapping.

It exposes no supplier purchase-order, checkout, fulfillment, shipment, or
tracking tool. Direct MCP-based ordering therefore remains impossible.

A later store-level finding changed the implementation scope: Shopify already
has DSers/AliExpress automatic ordering enabled. Earlier Relay orders could not
enter it because `shippingAddress` was empty; Relay had stored only a free-text
`ship_to` custom attribute. Leg 2 can therefore be handed off without a direct
DSers API call by creating a paid Shopify order with a real structured address.
Because this can charge real supplier costs, the handoff is protected by a
default-off environment gate and was not exercised in rehearsal.

## Supplier-cost snapshot

The committed [`scripts/dsers-supplier-costs.json`](../../scripts/dsers-supplier-costs.json)
contains 6 exact Shopify product GIDs and 18 exact variant GIDs captured from
DSers MCP:

- source: `dsers_mcp_snapshot`;
- captured at: `2026-07-27`;
- supplier currency: `USD`;
- route context: ship from `CN`, ship to `US`;
- safety binding: Shopify product GID + variant GID + vendor + SKU, never title.

`pnpm sync:supplier-costs` performs a read-only preflight. The separately
reviewed `--apply` run wrote 144 unstructured `relay.*` variant metafields and
read every value back successfully. The script refuses any protected metafield
definition whose Storefront access is not `NONE`.

DSers reported its store default currency as KRW even though Shopify's canonical
store currency is USD. Relay therefore never trusts the DSers store-currency
field and never applies an automatic repricing rule from it. Supplier costs and
projected margin remain Admin-only: buyer catalog responses, storefront HTML,
chat responses, and wallet-order projections expose only the public Shopify
catalog price and the charged USDC amount.

## Runtime behavior

Sourcing now requires the selected variant's Admin-only cost snapshot. The
reviewed Shopify USD catalog price remains the resale-price basis; DSers cost is
kept independent for margin evidence because DSers's store-currency context was
unsafe. The known F9 variant proves the distinction:

```text
Shopify variant: gid://shopify/ProductVariant/59696201072926
SKU:             14:193#black
supplier cost:   3.96 USD
catalog basis:   3.95 USD
sale amount:     4.54 USDC
projected margin: 0.58 USDC / 12.78%
basis: snapshot_usd_usdc_parity_excludes_shipping_tax
```

The paid Shopify ledger stores the cost snapshot and projected margin. Supplier
handoff state is determined before the irreversible Shopify mutation:

```json
{
  "supplierOrder": {
    "provider": "dsers",
    "status": "disabled",
    "ref": null,
    "message": "Supplier fulfillment is disabled; no structured Shopify shipping address or supplier order was created."
  }
}
```

`disabled` means the money gate is off. `blocked` means the gate is on but the
address is missing, incomplete, or an obvious placeholder; both omit Shopify
`shippingAddress`. Only `SUPPLIER_FULFILLMENT_ENABLED=true` plus recipient name,
address1, city, province/state, ISO-2 country, and ZIP/postal code produces
`shippingAddress`; that order is reported as `pending`, with `ref: null`,
because Relay still has no authoritative DSers order readback.

The buyer ADK agent must collect every required field and ask again when one is
missing. The deterministic fallback accepts explicit `Name`, `Address1`,
`City`, `Province`, `Country`, and `ZIP` fields and never substitutes the
configured demo destination.

> **Real financial effect:** with the gate enabled, each paid purchase with a
> complete structured address can flow into the connected DSers/AliExpress
> automation and charge roughly USD 2–4.70. The default is `false`; no enabled
> run or real supplier purchase was performed in this task.

`POST /orders/{orderRef}/fulfill` and
`GET /orders/{orderRef-or-name}/tracking` still return `409` without
authoritative downstream evidence; no fake supplier reference, fulfillment
success, parcel, or tracking number is generated. If Shopify later contains
both a real carrier and tracking number, Relay exposes that exact record as
`provider: "shopify"` and `demo: false`; the intended source is a real waybill
synced back by DSers, not a synthetic EasyPost fallback.

If a future direct DSers mutation is added, any ambiguous timeout—including the
observed class of CloudFront `504` responses—must be reconciled with an order
status lookup before retry. Blind retries are prohibited because they can
duplicate a supplier purchase.

## Verification

Final automated verification:

```text
pnpm test:supplier-cost-policy                 4 passed
pnpm sync:supplier-costs                       dry-run passed
pnpm -r typecheck                              passed
pnpm -r test                                   28 passed (8 payments, 20 commerce)
cd agents && ./.venv/bin/python -m pytest -q   64 passed
Shopify Admin supplier-cost apply/readback     6 products, 18 variants, 144 metafields
live catalog readback                          F9 supplier cost 3.96 USD
```

The final local five-service run executed:

```bash
./scripts/demo.sh "sports waterproof earbuds" 5
```

It selected Shopify variant
`gid://shopify/ProductVariant/59696201072926` / SKU `14:193#black`, paid
`4.54 USDC`, verified the transfer by its Solana Pay reference, and created
Shopify order `#1028` / `gid://shopify/Order/8711573373214`.

- order ref: `ord_78e2a32e087a48b9af36c05bde6452b1`
- payment reference: `2879GJPtXam8TA3CbY3J6rpCU4xhE3wm8thHzK3TTARP`
- transaction: `4KATSPvAZQmCJbZJ3P2pbHvV7sCsYayCJCYPCHj28AkcT5F2SmbNeqeugrg2G79LMLrWZ6n4i5sdKMDpFCAKs3eD`
- [Solana devnet explorer](https://explorer.solana.com/tx/4KATSPvAZQmCJbZJ3P2pbHvV7sCsYayCJCYPCHj28AkcT5F2SmbNeqeugrg2G79LMLrWZ6n4i5sdKMDpFCAKs3eD?cluster=devnet)

Shopify Admin readback showed `PAID` / `UNFULFILLED`, supplier cost `3.96
USD`, projected margin `0.58 USDC` / `12.78%`, an empty `shippingAddress`, and
an empty supplier order ref. This run predated the store-auto-ordering
discovery and used the then-current `not_connected` label; it did not create a
supplier order. Shopping-agent readback returned `tracking: null`, and both
fulfillment and tracking calls returned HTTP `409`.

An earlier pre-final regression run exposed that pricing had accidentally used
the DSers snapshot as the markup basis and paid `4.55 USDC`
([devnet transaction](https://explorer.solana.com/tx/3yXVCaZPgopCXxFwLJivkvQDMjrY8y92wcySL7M19rNGhhC7GExMrgc6jwNGStvR7Fndvc8kXfnLDjMtiECehh1r?cluster=devnet),
Shopify order `gid://shopify/Order/8711565312286`). That was corrected before
the final run: the reviewed Shopify USD catalog price is the sale basis, while
the independent DSers snapshot is margin evidence only.

## Existing order #1026 audit

Coordinator asked whether this worker caused Shopify order `#1026` to become
fulfilled without tracking. Admin readback shows:

```text
order created:       2026-07-27T05:19:19Z
fulfillment created: 2026-07-27T05:46:00Z
status:              FULFILLED / fulfillment SUCCESS
trackingInfo:        []
```

This worker's two live Issue #52 orders were `#1027` and `#1028` at
approximately 06:03–06:04Z. It did not call the live fulfillment endpoint for
`#1026`; the only live fulfillment/tracking probes made here targeted `#1028`
after synthetic fulfillment had been removed, and both returned `409`.
