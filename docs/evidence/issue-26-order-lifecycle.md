# Issue #26 live order-lifecycle evidence

Run date: 2026-07-27

Network: Solana devnet

Mint: Circle devnet USDC `4zMMC9srt5Ri5X14GAgXhaHii3GnPAEERYPJgZJDncDU`

## Payment and reverse refund

Order: `ord_9af6fd1416ec4a3badfc1d457eb5587c` / Shopify `#1007` /
`gid://shopify/Order/8710906642718`

Amount: `3.45 USDC`

- Buyer → merchant payment:
  [`4Pf19o…T6cj`](https://explorer.solana.com/tx/4Pf19oYm62m6ueBH7pvymWCcu1MWNjCxgma814cKhGamqdxqj7frgPMEdF4SQKTdcACLanp3XwJLmzNEDiPtT6cj?cluster=devnet)
- Merchant → buyer refund:
  [`3upc2a…7hBV`](https://explorer.solana.com/tx/3upc2aKy3c2xz6E9iDASmZDBFPLXqCi12Vm87GPbcBEuMCU65D9Q72wjXs1X4nyYyNBz9MunaxeeFNLAjNGQ7hBV?cluster=devnet)

Parsed token-balance deltas from the two finalized transactions:

| Transaction | Buyer USDC | Merchant USDC |
|---|---:|---:|
| Payment | `9.80 → 6.35` | `30.20 → 33.65` |
| Refund | `6.35 → 9.80` | `33.65 → 30.20` |

The first refund response reported `replayed: false`. An immediate second
request reported `replayed: true` and returned the exact same refund signature
`3upc2aKy3c2xz6E9iDASmZDBFPLXqCi12Vm87GPbcBEuMCU65D9Q72wjXs1X4nyYyNBz9MunaxeeFNLAjNGQ7hBV`.

The independent refund reference is
`FKRdnpbQAZuixqk9fjYKmnxm7NFDkqSMXQ3AJxobmvDu`. A devnet
`getSignaturesForAddress` query returned exactly one record:

```json
[
  {
    "signature": "3upc2aKy3c2xz6E9iDASmZDBFPLXqCi12Vm87GPbcBEuMCU65D9Q72wjXs1X4nyYyNBz9MunaxeeFNLAjNGQ7hBV",
    "err": null,
    "confirmationStatus": "finalized"
  }
]
```

Shopify lookup after the mutation returned:

```json
{
  "name": "#1007",
  "financialStatus": "REFUNDED",
  "refund": {
    "status": "refunded",
    "reference": "FKRdnpbQAZuixqk9fjYKmnxm7NFDkqSMXQ3AJxobmvDu",
    "txSignature": "3upc2aKy3c2xz6E9iDASmZDBFPLXqCi12Vm87GPbcBEuMCU65D9Q72wjXs1X4nyYyNBz9MunaxeeFNLAjNGQ7hBV",
    "explorer": "https://explorer.solana.com/tx/3upc2aKy3c2xz6E9iDASmZDBFPLXqCi12Vm87GPbcBEuMCU65D9Q72wjXs1X4nyYyNBz9MunaxeeFNLAjNGQ7hBV?cluster=devnet"
  }
}
```

An unpaid request (`ord_unpaid_refund_probe`) was refused with HTTP `409`:

```text
cannot be refunded because its original payment is not verified on-chain
(status=pending, reason=transaction_not_found)
```

## Lookup by orderRef and order name

Both `ord_9af6fd1416ec4a3badfc1d457eb5587c` and URL-encoded `%231007`
resolved the same real Shopify order. Before refund the response contained:

```json
{
  "name": "#1007",
  "financialStatus": "PAID",
  "fulfillmentStatus": "UNFULFILLED",
  "sku": "RELAY-AUDIO-EARBUD-MINI",
  "amount": "3.45",
  "paymentTx": "4Pf19oYm62m6ueBH7pvymWCcu1MWNjCxgma814cKhGamqdxqj7frgPMEdF4SQKTdcACLanp3XwJLmzNEDiPtT6cj",
  "refundStatus": "not_refunded"
}
```

## Shopify fulfillment

Separate paid order: `ord_8957a9326ebb4796816f0eaa0e6ce26d` / Shopify
`#1008` / `gid://shopify/Order/8710912606494`

`POST /orders/ord_8957a9326ebb4796816f0eaa0e6ce26d/fulfill` queried the
real Shopify fulfillment orders, called `fulfillmentCreate`, and returned:

```json
{
  "fulfillmentStatus": "FULFILLED",
  "trackingNumber": "EZ2000000002",
  "demo": true,
  "replayed": false
}
```

A second call returned `replayed: true`. A fresh Shopify order lookup confirmed
`financialStatus: PAID`, `fulfillmentStatus: FULFILLED`, and real SKU
`RELAY-AUDIO-USBC-WIRED`.

`EZ2000000002` is explicitly an EasyPost test value. This evidence does not
claim that a real parcel was shipped.
