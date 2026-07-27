# Issue #53 — autonomous DSers sourcing evidence

Date: 2026-07-27  
Network: Solana devnet  
Deployment: not performed

## Result

Relay autonomously sourced a product that was absent from the original
earbuds-only catalog, published it through DSers to Shopify, grounded its
margin in an admin-only supplier-cost snapshot, and purchased it with an
agent-signed USDC transfer.

| Evidence | Value |
| --- | --- |
| Request | `usb desk lamp`, budget `5 USDC` |
| DSers import item | `2081627211183555264` |
| DSers product | `2081627655939031040` |
| Supplier product | AliExpress `1005010477996975` |
| Supplier snapshot | `2.47 USD`, 13 units, captured 2026-07-27 for US |
| Shopify catalog price | `2.84 USD` |
| Relay quoted/paid price | `3.27 USDC` |
| Shopify product | `gid://shopify/Product/15839930024222` |
| Shopify variant | `gid://shopify/ProductVariant/59697183588638`, SKU `<none>` |
| Provenance | vendor `Relay DSers Autonomous`; tags `relay:autonomous-sourced`, `relay:dsers` |
| Order reference | `ord_b92ba2b0659645d28166d4437a255e12` |
| Payment reference | `99XaX66Vor94FebfbYELXvEm3V23rpKuMVozmmF3TiCC` |
| Transaction | `536R4Avd6vWFgNaGUZ4UaockW53Sbw5diwWYtNe2DoRbY48Hdi3EfLT8y9Hy5Mp3jSP8mVdGaKefUGnVML8U14DS` |
| Explorer | [Solana devnet transaction](https://explorer.solana.com/tx/536R4Avd6vWFgNaGUZ4UaockW53Sbw5diwWYtNe2DoRbY48Hdi3EfLT8y9Hy5Mp3jSP8mVdGaKefUGnVML8U14DS?cluster=devnet) |
| Shopify order | `gid://shopify/Order/8711641530654`, `#1029`, `PAID` |

The order API readback returned the sourced lamp as its single line item,
amount `3.27 USDC`, the same payment reference and transaction signature, and
`financialStatus=PAID`.

After rebasing PR #54, a live catalog readback returned supplier cost `2.47`,
catalog price `2.84`, and broker quote `3.27`. The public buyer projection
returned `3.27` without `supplierCost` or `supplierUrl`.

## OAuth bootstrap and rotation

The one-time browser bootstrap used OAuth authorization code + PKCE against
DSers, then wrote the grant to
`projects/web3research/secrets/relay-dsers-oauth`. No authorization code, PKCE
verifier, access token, or refresh token was printed.

- Bootstrap created immutable secret version 1.
- `--verify-rotation` performed a real refresh, created version 2, and made a
  freshly constructed token manager read the new grant.
- Secret metadata recorded active numeric version `2`, active rotation ID, and
  alias `relay-active=2`.
- For the rollback test, only the friendly alias was temporarily pointed at
  version 1. `read_active()` still selected numeric version 2 and verified its
  rotation ID, proving an alias-only rollback cannot resurrect the consumed
  refresh token. The alias was immediately restored to version 2.
- Refresh ownership uses an ETag-protected Secret Manager annotation lease.
  Rotation persists a new immutable version and advances the numeric pointer,
  rotation ID, and alias in one ETag compare-and-set. A 401 loser reloads the
  winner rather than replaying the consumed token.

The human-run and recovery procedure is documented in
[`docs/DSERS-SOURCING.md`](../DSERS-SOURCING.md).

## Mutation and safety checks

- The local catalog is checked for lexical relevance before DSers is called.
- Prohibited adult, medical, counterfeit, tobacco, weapon, and surveillance
  queries are rejected before supplier search.
- Autonomous imports are capped at one per request.
- Import and push ambiguous failures are reconciled by exact supplier URL
  reads; Relay never blindly retries a 504 mutation.
- An already pushed item is reused after state read, preventing the proof run
  from creating a second Shopify product.
- Live push requires an in-stock variant, positive supplier margin, and an
  in-budget sell price. Relay first reads DSers's non-mutating confirmation
  envelope, then sends one confirmed push without `force_push`.
- DSers's exact `store_handle` is resolved to a Shopify product GID. Supplier
  costs are bound to the exact live variant SKU. There is no title matching and
  no product deletion path.
- Supplier metafields are refused if Shopify defines them as
  storefront-readable. The public buyer catalog strips the supplier snapshot.
- `services/payments` was not modified, and autonomous Solana Pay settlement
  remains independent of DSers.

## Authentication-failure fallback

A second shopping process was started with a deliberately nonexistent Secret
Manager secret. A request for absent product `ukulele capo` produced:

- shopping `/catalog/source`: HTTP 503 with explicit DSers-unavailable state;
- buyer `products`: empty (no unrelated earbud was presented as a match);
- buyer `externalSourcing.status`: `unavailable`;
- buyer `fallbackCatalog`: three existing, in-budget catalog products;
- no payment request and no transfer.

This proves a broken DSers grant cannot take down catalog browsing or the
existing autonomous USDC path.

## Verification

All checks ran after rebasing `origin/main` at PR #54:

```text
agents/.venv/bin/pytest -q agents/tests
82 passed

pnpm -r test
commerce: 24 passed
payments: 8 passed

pnpm -r typecheck
packages/shared, services/commerce, services/payments: passed

pnpm test:supplier-cost-policy
4 passed

git diff --check
passed
```

No Cloud Run deployment or Shopify theme mutation was performed.
