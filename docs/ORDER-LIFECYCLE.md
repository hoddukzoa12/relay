# Relay order lifecycle

Relay separates autonomous payment from a real-money supplier handoff:

```text
buyer → merchant payment → Shopify paid order
                              ├─ gate OFF → supplierOrder:disabled
                              ├─ incomplete address → supplierOrder:blocked
                              └─ gate ON + complete shippingAddress
                                      → DSers automation → supplierOrder:pending
                              ↓ real Shopify SUCCESS fulfillment
                         carrier + tracking number present
                              ↓ sourced product only
                     Shopify product ACTIVE → DRAFT
        ↓
      lookup
        ↓
merchant → buyer full refund
```

## Agent endpoints

The shopping agent on `:8091` exposes:

| Method | Path | Result |
|---|---|---|
| `GET` | `/orders/{orderRef-or-name}` | `OrderStatus` with real SKU, amount, supplier/refund state, and on-chain proof |
| `POST` | `/orders/{orderRef-or-name}/refund` | `RefundResult` with payment and refund explorer links |
| `POST` | `/orders/{orderRef}/fulfill` | `409` while no real supplier order exists |
| `GET` | `/orders/{orderRef-or-name}/tracking` | `409` while no real supplier shipment exists |

The commerce service on `:8082` owns Shopify lookup and mutations. The payments
service on `:8081` owns the on-chain `POST /refunds` primitive.

## Refund safety

Refunds are full-only; callers cannot supply an amount or recipient.

1. Resolve Shopify metadata and the original Solana Pay reference.
2. Re-run `findReference` + `validateTransfer` against the original amount,
   merchant recipient, USDC mint, and reference.
3. Acquire the refund compare-and-set (`not_refunded → refunding`) before
   signing anything.
4. Sign one merchant-funded SPL transfer to the configured buyer wallet with a
   separate refund reference.
5. Store the submitted signature before broadcast and reconcile ambiguous RPC
   failures without reopening the CAS.
6. Call Shopify `refundCreate`, then merge the refund signature, reference, and
   explorer URL into order custom attributes.

Concurrent calls share one in-flight promise. Sequential replays return the
stored signature. If USDC moves but the Shopify mutation fails, the shopping
agent retries the same payment-service refund and receives the existing
signature before repairing Shopify; it does not transfer again.

Shopify `refundCreate` additionally uses its official `@idempotent` directive on
API versions that support it. Older configured API versions rely on the order
financial status plus Relay's process CAS and custom-attribute proof.

## Supplier order money gate

The Shopify store already has DSers/AliExpress automatic ordering enabled.
DSers could not act on earlier Relay orders because Relay stored only a free
text `ship_to` custom attribute while Shopify `shippingAddress` was empty.
Relay now carries a buyer-supplied structured address through PurchaseIntent,
the signed AP2 IntentMandate, broker state, and the commerce order input.

Commerce writes `shippingAddress` only when both conditions hold:

1. `SUPPLIER_FULFILLMENT_ENABLED=true`;
2. recipient name, address1, city, Shopify-compatible province/state code,
   ISO-2 country, and ZIP/postal code are complete and not obvious placeholders.

Address2 and phone are optional. The legacy free-text `shipTo` is retained, and
is derived from the structured address when one is present. Missing fields are
never defaulted or invented.

> **Financial consequence:** enabling the flag can cause the external
> DSers/AliExpress account to charge the actual supplier cost (currently about
> USD 2–4.70) for every new paid order with a complete address. The default is
> `false`; rehearsals must keep it false.

The supplier state is explicit:

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

| Status | Meaning |
|---|---|
| `disabled` | Money gate off; Shopify `shippingAddress` omitted and no supplier order requested |
| `blocked` | Gate on, but address incomplete/placeholder; `shippingAddress` still omitted |
| `pending` | Complete address submitted to Shopify; DSers outcome and supplier ref are not yet confirmed |
| `submitted` / `confirmed` / `failed` | Reserved for a future authoritative DSers readback |

The official DSers MCP surface still has no purchase-order or tracking tool, so
Relay cannot synchronously prove the downstream DSers result or obtain its
reference. It therefore never upgrades `pending` merely because Shopify
accepted the order. Relay also does not call Shopify `fulfillmentCreate` or
synthesize a waybill. Fulfill and tracking endpoints fail closed with `409`
until authoritative supplier/shipment evidence exists.

The old EasyPost test adapter was removed from the commerce service. A Shopify
fulfillment status by itself is not tracking proof. Relay returns tracking only
when Shopify contains both a non-empty carrier and tracking number; that value
is labeled `provider: "shopify"` and `demo: false`. Otherwise the tracking
endpoint returns `409`. The future real source is the waybill DSers synchronizes
back into Shopify fulfillment; do not reintroduce EasyPost as a synthetic
substitute.

## Catalog retirement after real fulfillment

Order lookup and catalog search reconcile Shopify's real fulfillment records.
Relay treats only `fulfillment.status=SUCCESS` plus a non-empty carrier and
tracking number as the retirement signal. It follows the fulfillment line item
to an exact Shopify product GID, reads that product, and changes `ACTIVE` to
`DRAFT` only when the vendor is `Relay DSers Autonomous` or the product carries
tag `relay:autonomous-sourced`.

There is no deletion path and no title matching. A human-curated product,
including the original six catalog products, is read but never mutated. A
missing/failed fulfillment or a fulfillment without real tracking causes no
product read or write; the existing `supplierOrder` state remains the honest
explanation of why no downstream delivery was proven.

Drafting preserves the order's product reference and is reversible. A later
request for the same supplier item revalidates current DSers economics and
inventory, then republishes the exact non-deleted product as `ACTIVE`.

## Supplier-cost and margin evidence

Supplier costs are a dated DSers MCP snapshot stored in Shopify variant
metafields that are Admin-only. The sync validates exact product and variant
GIDs, vendor, and SKU, and never title-matches. Public catalog/chat/order
projections do not expose the supplier cost or projected margin.

For each Shopify order, Relay stores the selected snapshot and a projected
gross-margin record in private order custom attributes. The basis is explicitly
`snapshot_usd_usdc_parity_excludes_shipping_tax`; it is not realized profit and
does not include shipping, tax, refunds, or exchange-rate movement.

## Contracts

The lifecycle contracts are mirrored in:

- `packages/shared/src/index.ts`
- `agents/agentic_broker/common/contracts.py`
- `packages/shared/schemas/structured-shipping-address.schema.json`
- `packages/shared/schemas/intent-mandate.schema.json`
- `packages/shared/schemas/order-status.schema.json`
- `packages/shared/schemas/supplier-order.schema.json`
- `packages/shared/schemas/refund-result.schema.json`
- `packages/shared/schemas/fulfillment-result.schema.json`
- `packages/shared/schemas/tracking-info.schema.json`

## Live evidence

The historical issue #26 verification run, including both explorer links,
token balance deltas, single-signature refund-reference proof, Shopify refund
status, and its explicitly demo-only fulfillment result, is recorded in
[`docs/evidence/issue-26-order-lifecycle.md`](evidence/issue-26-order-lifecycle.md).
The current DSers capability/authentication spike, supplier-cost persistence,
and truthful Leg 2 behavior are recorded in
[`docs/evidence/issue-52-dsers-mcp.md`](evidence/issue-52-dsers-mcp.md).
