# Issue #42 — SPL wallet delegation evidence

Verified on 2026-07-27 against Solana devnet and the unpublished Shopify
preview theme. No Cloud Run service was deployed.

## Implementation invariants

- The authenticated web path is `POST /web/buy`; a request without a Clerk
  bearer session returns HTTP 401.
- The buyer server derives `delegator` only from the verified Clerk identity.
  Request models expose no client-authoritative wallet field.
- `payments` derives the user's USDC ATA, calls `getAccount` immediately before
  transfer, and checks owner, delegate authority, delegated amount, and balance.
- The client-supplied approval signature is verified as a confirmed SPL
  `approveChecked` instruction binding that owner, ATA, mint, and delegate before
  it is recorded in the IntentMandate.
- The application stores no allowance ledger. IntentMandate
  `allowanceRemaining` is explicitly an audit snapshot; the token account is the
  SSOT.
- Calls without `delegator` keep the existing agent-wallet source and
  zero-human-click MCP/A2A/CLI behavior.

## Real devnet Approve

The devnet helper created a fresh throwaway user wallet. It had zero SOL; the
buyer agent paid ATA rent and transaction fees. The user's signature authorized
one SPL Token Approve transaction with a 2.50 USDC limit.

- User / delegator:
  `HRqAVky6FX6yxDEzMre1Qz8H8bYskxGQ2sNHZKnpmeRy`
- User SOL: `0` lamports
- User USDC ATA:
  `69DcbkBrr5ydx6ce3ULXwaettHCP4r3mwr9JBpGS4D7J`
- Delegate / buyer agent:
  `7dwPd8gByoFPwfu8swgVFddzaSLyRARbmVuwposmxrr`
- Initial user USDC balance: `3.00`
- On-chain delegated amount: `2.50`
- Approve signature:
  `2egutH56v17sGaK9yhj1GUuap7JJUQXm1RTWeYK4hG4orgbfmR3ZUa1nzKZTGKagtwGVa5WSSZSstnVfAfdp5tHd`
- Explorer:
  <https://explorer.solana.com/tx/2egutH56v17sGaK9yhj1GUuap7JJUQXm1RTWeYK4hG4orgbfmR3ZUa1nzKZTGKagtwGVa5WSSZSstnVfAfdp5tHd?cluster=devnet>

The helper uses the same transaction shape returned to Phantom by
`POST /delegation/transaction`: user is the token-account owner signer, buyer
agent is fee payer. The remote Phantom click remains a post-deploy human QA
step because this issue explicitly forbids deploying the new backend.

## Real delegated purchase: user wallet decreased

The local authenticated web route was exercised with a test-only replacement
for Clerk token verification. The identity still entered the application as the
server-side `ClerkIdentity`; no wallet was accepted from the request body.

- Unauthenticated attempt: HTTP `401`
- Authenticated result: HTTP `200`, `status=paid`
- Product: New Wireless Earphones Bluetooth 5.0 … Single Headset
- Amount: `2.21 USDC`
- Payment signature:
  `2U29eU5asojSJeLfyD7UN4q3gygKqzxDP11vqTW1qNJtACSADc2HTiyMHa21MVQ6B3nakpmCt4vbFc9U2gvYvKyv`
- Explorer:
  <https://explorer.solana.com/tx/2U29eU5asojSJeLfyD7UN4q3gygKqzxDP11vqTW1qNJtACSADc2HTiyMHa21MVQ6B3nakpmCt4vbFc9U2gvYvKyv?cluster=devnet>
- Order ref: `ord_9e6d9a807d7a493780cf21826d0d7be5`
- Shopify order: `#1022` /
  `gid://shopify/Order/8711345602846`
- Shopify `buyer_wallet`:
  `HRqAVky6FX6yxDEzMre1Qz8H8bYskxGQ2sNHZKnpmeRy`

The transaction metadata proves the exact user balance movement:

| Field | On-chain value |
|---|---|
| Source token owner | `HRqAV…meRy` |
| Source ATA | `69Dcb…4D7J` |
| `preTokenBalances` | `3.00 USDC` |
| `postTokenBalances` | `0.79 USDC` |
| Delta | `-2.21 USDC` |
| Sole transaction signer / fee payer | buyer agent `7dwPd…xrr` |
| User signatures during purchase | `0` |
| Remaining delegated amount | `0.29 USDC` |

## Guard and revoke evidence

With `0.29 USDC` remaining, an authenticated web purchase with a `2.30 USDC`
budget returned HTTP 409 before quote/payment signing:

```text
Requested budget 2.30 USDC exceeds the on-chain delegated allowance
0.29 USDC; approve a higher limit.
```

The user then signed SPL Revoke:

- Revoke signature:
  `2m9otaYynv6k9QvtztpS4ihoHCqcC5oqT3EYRk16kA5oo23qCZTmc4eBw5xPqU4m6JQ3Jzspvkbg7wqUz12JF36w`
- Explorer:
  <https://explorer.solana.com/tx/2m9otaYynv6k9QvtztpS4ihoHCqcC5oqT3EYRk16kA5oo23qCZTmc4eBw5xPqU4m6JQ3Jzspvkbg7wqUz12JF36w?cluster=devnet>
- Readback: `active=false`, `allowanceRemaining=0`

A subsequent authenticated web purchase returned HTTP 409:

```text
SPL delegation is missing or revoked; approve the broker again.
```

## Agent-wallet fallback regression

After Revoke, the unchanged CLI route completed a separate real devnet purchase
without Clerk, Phantom, or `delegator`:

- Payment method / payer:
  `solana:7dwPd8gByoFPwfu8swgVFddzaSLyRARbmVuwposmxrr`
- Payment signature:
  `4sGQ9Ar1tcjT7jNHqRwewJWPXvCA3x2p77bCq3CoyfLXs5ApUHWT8SGMis2nT5zNLtY6Rumpq99QHcLMZ7kT2HML`
- Explorer:
  <https://explorer.solana.com/tx/4sGQ9Ar1tcjT7jNHqRwewJWPXvCA3x2p77bCq3CoyfLXs5ApUHWT8SGMis2nT5zNLtY6Rumpq99QHcLMZ7kT2HML?cluster=devnet>
- Shopify order:
  `gid://shopify/Order/8711348257054`

This proves the agent-native fallback remains functional with zero human clicks.

## Preview theme safety and QA

- Uploaded theme:
  `gid://shopify/OnlineStoreTheme/204473499934`
- Name: `Horizon + Relay Agent (preview)`
- Role: `UNPUBLISHED`
- Preview:
  <https://solanagcp.myshopify.com/?preview_theme_id=204473499934>
- The sync script hard-refuses theme `204459704606` and any `MAIN` role; the live
  theme was not targeted or modified.
- Browser QA showed **Wallet sign-in**, `50 USDC` default limit,
  **Approve once**, disabled **Revoke** before approval, and the explanatory
  `1 approval, then 0 clicks` copy.
- Anonymous preview search remained functional and returned three live Shopify
  catalog matches. The only console error came from the Shopify preview bar's
  Bugsnag session telemetry, not Relay.

The preview now references API routes added by this branch. Full remote
Approve/purchase QA must follow the explicitly human-approved Cloud Run deploy.

## Automated verification

- `pnpm -r typecheck` — pass
- Payments tests — 8 pass
- Commerce tests — 14 pass
- Python agent tests — 48 pass
- Theme JavaScript and Shopify section schema parse — pass
- All JSON contract schemas parse — pass
