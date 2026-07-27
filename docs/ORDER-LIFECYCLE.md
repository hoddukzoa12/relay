# Relay order lifecycle

Relay's autonomous path does not stop at payment:

```text
buyer → merchant payment
        ↓
lookup → fulfillment → demo tracking
        ↓
merchant → buyer full refund
```

## Agent endpoints

The shopping agent on `:8091` exposes:

| Method | Path | Result |
|---|---|---|
| `GET` | `/orders/{orderRef-or-name}` | `OrderStatus` with real SKU, amount, fulfillment/refund state, and on-chain proof |
| `POST` | `/orders/{orderRef-or-name}/refund` | `RefundResult` with payment and refund explorer links |
| `POST` | `/orders/{orderRef}/fulfill` | `FulfillmentResult` verified as `FULFILLED` in Shopify |
| `GET` | `/orders/{orderRef-or-name}/tracking` | `TrackingInfo` from the carrier-provider interface |

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

## Fulfillment and tracking

Fulfillment queries Shopify `fulfillmentOrders`, passes every open fulfillment
order to `fulfillmentCreate`, and verifies the aggregate order status becomes
`FULFILLED`.

The tracking layer implements a separable `CarrierTrackingProvider`.
`EasyPostTrackingProvider` calls EasyPost's official
[`POST /v2/trackers`](https://docs.easypost.com/docs/trackers) endpoint. Set
`EASYPOST_API_KEY` to exercise that adapter.

The current tracking number is EasyPost test value `EZ2000000002`. Every
tracking response sets `demo: true` and says explicitly that Relay did not
create or ship a real parcel. With no API key, the endpoint returns
`official_api_not_configured`; it never scrapes a marketplace account. A real
waybill from #36 can replace the number without changing the interface.

## Contracts

The lifecycle contracts are mirrored in:

- `packages/shared/src/index.ts`
- `agents/agentic_broker/common/contracts.py`
- `packages/shared/schemas/order-status.schema.json`
- `packages/shared/schemas/refund-result.schema.json`
- `packages/shared/schemas/fulfillment-result.schema.json`
- `packages/shared/schemas/tracking-info.schema.json`

## Live evidence

The issue #26 verification run, including both explorer links, token balance
deltas, single-signature refund-reference proof, Shopify refund status, and
fulfillment result, is recorded in
[`docs/evidence/issue-26-order-lifecycle.md`](evidence/issue-26-order-lifecycle.md).
