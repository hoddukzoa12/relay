# Issue #39 Phase 1 — chat storefront evidence

Verified on 2026-07-27 against the unpublished duplicate theme only.

## Theme safety and rendered landing page

- Preview theme: `Horizon + Relay Agent (preview)`
  (`gid://shopify/OnlineStoreTheme/204473499934`, role `UNPUBLISHED`)
- Live theme excluded by the sync script:
  `Horizon` (`204459704606`)
- Preview URL:
  <https://solanagcp.myshopify.com/?preview_theme_id=204473499934>
- The theme was not published.
- `pnpm theme:chat` re-read both remote files and confirmed that they match this
  checkout:
  - `sections/relay-agent-chat.liquid`
  - `templates/index.json`

A password-authenticated fetch of the final preview returned 203,047 bytes and
showed:

- exactly one `<relay-agent-chat>` landing section;
- `agent-base-url="https://buyer-1018608922006.us-central1.run.app"`;
- 15 current Shopify catalog products embedded by Liquid;
- real `SolanaGCP` catalog listings with images, prices, and live availability.

The browser flow for `wireless earbuds under 10 dollars` rendered these three
in-stock real-catalog candidates:

1. New Wireless Earphones Bluetooth 5.0 … — $1.85
2. TWS Fone Bluetooth Earphones … — $3.64
3. New TWS Bluetooth Headphones 9D Stereo … — $3.66

The chat retained the user message, candidate response, delegation message,
progress trace, and final confirmation client-side. The display-only devnet
balance RPC returned HTTP 429 during QA; the buyer `/health` fallback correctly
kept the payment rail marked ready, and the purchase still completed.

## Real devnet purchase

The storefront's **Let agent choose** action delegated the first real catalog
candidate. No wallet prompt or Shopify checkout was opened after delegation.

- Product: New Wireless Earphones Bluetooth 5.0 Headphones With Mic in-Ear
  Sports Waterproof TWS Earbuds Bluetooth Handsfree Single Headset
- Catalog price: 1.85
- Final quote: 2.21 USDC
- Payment approval clicks: 0
- Status trace: `A2A RECEIVED → SIGNED → ON-CHAIN → PAID`
- Signature:
  `5hSJhysjguhXzM5ae6S2mbj4iF5KkjRQ5icgeaoP2FRRDUNz8mqCVhTDvfZpmi1PsjonMb4dixub6uCKqAovBfw`
- Explorer:
  <https://explorer.solana.com/tx/5hSJhysjguhXzM5ae6S2mbj4iF5KkjRQ5icgeaoP2FRRDUNz8mqCVhTDvfZpmi1PsjonMb4dixub6uCKqAovBfw?cluster=devnet>
- Shopify order:
  `gid://shopify/Order/8711185793310`

The Shopify order ID was returned only after the shopping agent verified the
unique payment reference on-chain.

## Regression checks

- `pnpm -r typecheck` — pass
- Payments tests — 3 pass
- Commerce tests — 14 pass
- Python agent tests — 19 pass
- Storefront CORS preflight for `POST /buy` — HTTP 200 with
  `access-control-allow-origin: https://solanagcp.myshopify.com`
- Existing `POST /buy` and all agent/payment contracts are unchanged.
- No buyer server, MCP, shopping, payments, commerce, or shared-contract source
  was changed.
