# Issue #60 — autonomous catalog lifecycle evidence

Date: 2026-07-27

Deployment: not performed

Shopify theme mutation: not performed

## Result

Relay now treats autonomously sourced products as a reversible working
catalog:

```text
source → publish → sell → fulfill → DRAFT
   ↑                                  │
   └──── revalidate and republish ────┘
```

The sourced-product metadata write changes the exact Shopify product GID to
`ACTIVE`, verifies vendor/tags/costs by readback, and places that verified
projection in a one-minute write-through bridge. All buyer, shopping, MCP, and
A2A discovery paths use the same commerce catalog endpoint, so a just-pushed
product is searchable while Shopify's list index catches up.

Catalog search and order-status lookup reconcile real Shopify fulfillments. A
product is changed to `DRAFT` only when all of these are true:

1. the fulfillment itself has `status=SUCCESS`;
2. one tracking record has both carrier and tracking number;
3. the fulfillment line item resolves to an exact Shopify product GID;
4. that product has vendor `Relay DSers Autonomous` or tag
   `relay:autonomous-sourced`.

There is no deletion mutation and no title matching. A later supplier request
revalidates preview cost, margin, budget, and inventory, then reuses the exact
non-deleted DSers/Shopify record and marks it `ACTIVE`.

## Live Shopify evidence

A read-only preflight inspected all 100-or-fewer Shopify products and all 33
Relay-tagged orders before the local commerce service was started.

- Six original `SolanaGCP` earphone products were `ACTIVE` and not autonomous.
- Four `[Relay Sourced]` products were `ACTIVE` and carried autonomous
  provenance.
- No autonomous product had a Shopify fulfillment with both `SUCCESS` and
  carrier + tracking number, so no autonomous product qualified for
  retirement.
- Order `#1026` had fulfillment `SUCCESS` but no carrier/tracking pair. Its
  human `SolanaGCP` product remained `ACTIVE`.
- Order `#1008` had fulfillment `SUCCESS` and carrier/tracking, but its product
  was human-managed (`vendor=Relay`, no autonomous tag). It was not mutated.
- Recent order `#1033` was `UNFULFILLED`,
  `supplierOrder.status=disabled`, `tracking=null`, with the explicit message
  that supplier fulfillment was disabled and no supplier order was created.

The issue branch's local commerce service then ran against live Shopify with
`supplierFulfillmentEnabled=false`.

- `GET /products?query=usb%20desk%20lamp&limit=5` returned sourced product
  `gid://shopify/Product/15839930024222` first with `status=ACTIVE`.
- `GET /orders/%231033` returned the truthful disabled supplier state and no
  tracking.
- A post-run readback showed the same six original `SolanaGCP` products
  `ACTIVE` and the same four autonomous products `ACTIVE`. No product was
  deleted or spuriously drafted.

This live store has no qualifying autonomous delivery signal, so the correct
proof is deliberately “no retirement performed.” The successful-retirement
branch is covered by injected GraphQL fixtures only; those fixtures never
write Shopify and are not presented as a real delivery.

## Automated safety evidence

Commerce tests cover:

- `SUCCESS + carrier + tracking number` as the minimum delivery signal;
- failed/missing/untracked fulfillments producing zero product reads/writes;
- an exact autonomously sourced product changing `ACTIVE → DRAFT`;
- the generated mutation containing only exact GID + `status=DRAFT`, with no
  delete operation;
- six human products remaining unchanged while one sourced product is drafted;
- immediate catalog write-through after sourcing and eviction after Shopify
  catches up;
- a DRAFT sourced product being restored to `ACTIVE` by the metadata path;
- an offselling non-deleted DSers record being republished without a duplicate.
- supplier relevance order winning over a cheaper later result after the budget
  safety filter.

The repository verification record is:

```text
pnpm --filter @arb/commerce test
30 passed

agents/.venv/bin/pytest -q agents/tests
95 passed

pnpm -r test
commerce: 30 passed
payments: 8 passed

pnpm -r typecheck
packages/shared, services/commerce, services/payments: passed

pnpm test:storefront-auth
8 passed

pnpm test:supplier-cost-policy
4 passed

pnpm test:seed-policy
2 passed

git diff --check
passed
```

`services/payments` and `sections/relay-agent-chat.liquid` are unchanged.
