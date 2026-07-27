# Issue #36 — real supplier catalog local verification

Verified on 2026-07-27 against the five-service local stack, Shopify Admin API,
and Solana devnet. No Cloud Run deployment or Shopify theme mutation was
performed.

## Store and catalog preflight

- Shopify `shop.currencyCode`: `USD`
- ACTIVE supplier products: 6
- Supplier vendor: `SolanaGCP`
- `pnpm seed:catalog:fallback`: exited successfully with
  `skipped: true` because an ACTIVE non-Relay supplier catalog was present.
- Live catalog query selected an in-stock, priced variant with a non-empty SKU
  for every ACTIVE product.

## Autonomous purchase

Command:

```bash
./scripts/demo.sh "sports waterproof earbuds" 5
```

Result:

- Status: `paid`
- Product: `TWS F9-5 Earphone Bluetooth 5.3 Wireless Headphones Hifi Stereo Sports Waterproof Earbuds Headset Hearing Aids With Mic Handfree`
- Shopify variant: `gid://shopify/ProductVariant/59696201072926`
- SKU: `14:193#black`
- Catalog cost: `3.95 USD`
- Paid amount after 15% markup: `4.54 USDC`
- Order ref: `ord_c8c653475a704852a479a0c0acb5e5f9`
- Payment reference: `BRU46emz7vXiKbhysvhpMkYy8kUoxSRGyjHgtmrHFMnp`
- Transaction: `54nDbzuiuA4oJnuEtBMvEgVTVjr6r3bMs1aiDPQ5uRLc5gcAsXBXVgnrh4iNzNrvNn3e4oEEWQajYsRuJy3CipRf`
- Explorer:
  https://explorer.solana.com/tx/54nDbzuiuA4oJnuEtBMvEgVTVjr6r3bMs1aiDPQ5uRLc5gcAsXBXVgnrh4iNzNrvNn3e4oEEWQajYsRuJy3CipRf?cluster=devnet

Balance readback matched the transfer:

- Merchant: `31.60 → 36.14 USDC`
- Buyer: `8.40 → 3.86 USDC`

## Shopify order readback

Shopify Admin API returned:

- Order: `#1021` / `gid://shopify/Order/8711336722718`
- Financial status: `PAID`
- Order currency and amount: `4.54 USD`
- Line item variant: `gid://shopify/ProductVariant/59696201072926`
- Line item SKU: `14:193#black`
- Custom attributes preserved the same `order_ref`, payment reference,
  transaction signature, variant ID, and SKU.

The CartMandate emitted during the purchase contained that same variant ID and
SKU before payment, so catalog selection, authorization, settlement, and the
Shopify ledger all agree.

## Automated checks

```text
pnpm -r typecheck                      passed (shared, payments, commerce)
pnpm -r test                           20 passed (3 payments, 17 commerce)
pnpm test:seed-policy                  2 passed
cd agents && ./.venv/bin/python -m pytest -q
                                          42 passed
```

The commerce tests include a forced `KRW` mismatch that throws before an order
payload can be created, Shopify currency cache/refresh behavior, sellable
variant selection, and variant/SKU order binding.
