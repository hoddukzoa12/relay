# Shopify product-page agent widget

The Relay storefront uses authenticated `POST /web/buy`. It does **not** add to
cart or open Shopify checkout. Web checkout requires Clerk wallet sign-in and
one Phantom SPL Token `Approve`; later Solana Pay transfers are signed by the
buyer agent as delegate and debit the signed-in user's USDC ATA. Shopify remains
only the final order ledger.

The human-present web path and human-absent agent path intentionally differ:

- **Web:** Clerk login → one Approve with an on-chain limit → zero-click
  purchases from the user's wallet → optional Revoke.
- **MCP/A2A/CLI:** no Clerk or browser; the configured buyer-agent wallet pays
  autonomously exactly as before.

## 1. Allow the storefront origin

Add every storefront origin that may contain the widget to the repo-root `.env`.
Origins include the scheme and host only—no path and no trailing slash:

```dotenv
BUYER_CORS_ORIGINS=https://YOUR-STORE.myshopify.com,https://www.YOUR-CUSTOM-DOMAIN.com
CLERK_PUBLISHABLE_KEY=pk_test_...
CLERK_SECRET_KEY=sk_test_...
CLERK_ISSUER=https://YOUR-INSTANCE.clerk.accounts.dev
CLERK_JWKS_URL=https://YOUR-INSTANCE.clerk.accounts.dev/.well-known/jwks.json
```

`SHOPIFY_STORE_DOMAIN` is also converted to an `https://` origin and allowed
automatically when it is configured. `BUYER_CORS_ORIGINS` is still useful for a
custom storefront domain or a second preview domain. Restart the buyer service
after changing `.env`; CORS settings are loaded at process startup.

## 2. Run Relay and expose the buyer agent over HTTPS

Start the five-service stack:

```bash
./scripts/dev.sh
```

Keep that terminal running. In a second terminal, choose one tunnel.

### cloudflared

```bash
brew install cloudflared
cloudflared tunnel --url http://localhost:8090
```

Copy the printed URL, for example:

```text
https://random-words.trycloudflare.com
```

A quick tunnel gets a new hostname whenever it restarts, so update
`agent_base_url` in Shopify after a restart. A named Cloudflare Tunnel or a
deployed buyer service provides a stable URL.

### ngrok

```bash
brew install ngrok
ngrok config add-authtoken YOUR_NGROK_AUTHTOKEN
ngrok http 8090
```

Copy the HTTPS forwarding URL shown by ngrok, for example
`https://example.ngrok-free.app`.

Confirm the public endpoint before editing the theme:

```bash
curl https://YOUR-TUNNEL.example/health
curl https://YOUR-TUNNEL.example/wallet-balances
```

The first response should contain `"ok": true`; the second should show the buyer
and merchant addresses and their SOL/USDC balances.

Clerk must have Solana enabled as a Web3 sign-in provider. Because the same
Clerk instance is Shopify's OIDC provider, each storefront user also needs a
verified email; Shopify requires `email` and `email_verified: true`.

## 3. Add the self-contained widget to the product template

The widget source is
[`agents/agentic_broker/web/widget.html`](../agents/agentic_broker/web/widget.html).
It contains its markup, styles, and JavaScript, needs no build step, and uses a
Shadow DOM so theme styles do not leak into the widget.

The live preview is installed in the duplicate Shopify theme
**Horizon + Relay Agent (preview)** (theme ID `204473499934`) as
`sections/relay-agent-buy.liquid`. It points to the public buyer API at
`https://buyer-1018608922006.us-central1.run.app`.

### Fastest: paste the file directly

In Shopify Admin:

1. Go to **Online Store → Themes → Customize**.
2. Open the desired **Products → Default product** template.
3. Add a **Custom Liquid** section where the action should appear.
4. Paste the entire contents of `agents/agentic_broker/web/widget.html`.
5. Replace `https://YOUR-TUNNEL.trycloudflare.com` in the first element with the
   HTTPS URL printed by the tunnel, then save.

The file already reads `product.title` and defaults the budget to `25` USDC.
Change the `budget-usdc` default or edit it in the rendered widget for the demo.

### Reusable theme snippet + exact Custom Liquid block

For reuse across product templates, open **Online Store → Themes → Edit code**,
add a snippet named `relay-agent-buy.liquid`, and paste the entire contents of
`agents/agentic_broker/web/widget.html` into that snippet.

Then paste this exact block into a product-template **Custom Liquid** section,
changing only the HTTPS URL and demo destination:

```liquid
{% assign relay_budget = product.price | divided_by: 100.0 %}
{% render 'relay-agent-buy',
  agent_base_url: 'https://YOUR-TUNNEL.trycloudflare.com',
  product_query: product.title,
  budget_usdc: relay_budget,
  ship_to: 'Google Startup Campus, Seoul, KR'
%}
```

If the storefront price is not denominated one-to-one with the intended USDC
demo budget, replace `budget_usdc: relay_budget` with a fixed maximum such as
`budget_usdc: 25`.

The widget's configurable element is:

```html
<relay-agent-buy
  agent-base-url="https://YOUR-TUNNEL.trycloudflare.com"
  product-query="wireless earbuds"
  budget-usdc="25"
  ship-to="Google Startup Campus, Seoul, KR"
></relay-agent-buy>
```

`agent-base-url` must be the buyer service, not the shopping, payments, Shopify,
or checkout URL.

## 4. Verify CORS before the demo

Use the exact origin visible in the storefront browser address bar:

```bash
curl -i -X OPTIONS 'https://YOUR-TUNNEL.example/buy' \
  -H 'Origin: https://YOUR-STORE.myshopify.com' \
  -H 'Access-Control-Request-Method: POST' \
  -H 'Access-Control-Request-Headers: authorization,content-type'
```

The response should include:

```text
access-control-allow-origin: https://YOUR-STORE.myshopify.com
access-control-allow-methods: GET, POST, OPTIONS
```

If that header is absent, add the browser's exact `location.origin` value to
`BUYER_CORS_ORIGINS` and restart `./scripts/dev.sh`.

## HTTPS and mixed-content rule

Shopify storefronts load over HTTPS. Browsers therefore block widget requests
to `http://localhost:8090` before CORS is even evaluated. The widget detects
this setup and reports that an HTTPS buyer-agent URL is required. Use the HTTPS
tunnel URL or a deployed HTTPS buyer service; changing CORS cannot make an HTTP
endpoint callable from an HTTPS storefront.

Do not point the button at a Shopify checkout URL as a workaround. That would
reintroduce human checkout approval and break the autonomous payment path.

## Demo checklist

1. `make check-wallets` shows buyer SOL for fees and enough Circle devnet USDC.
2. `./scripts/dev.sh` is running and the HTTPS tunnel health check passes.
3. The storefront origin is present in `BUYER_CORS_ORIGINS`.
4. The chat shows `[ SIGN IN TO BUY ]`; anonymous catalog search still works.
5. Select **Wallet sign-in** and show the backend-verified human wallet address.
6. Enter the maximum USDC amount (default `50`) and select **Approve once**.
   Phantom signs one SPL Token Approve transaction; the agent pays its SOL fee.
7. Choose one or more products without another wallet prompt. The header shows
   the remaining live on-chain allowance, and **Revoke** removes it at any time.
8. Show the inline states: `[ A2A RECEIVED ]` → `[ SIGNED ]` →
   `[ ON-CHAIN ]` → `[ PAID ]`.
9. Open the explorer proof and show that the user's pre/post USDC token balance
   decreased while the Shopify order's `buyer_wallet` is that Clerk wallet.
10. Try an over-limit purchase and a purchase after Revoke; both must stop with
   an approval/reapproval message before any transfer is signed.
11. Open **My orders** to show the Shopify order filtered by the signed-in wallet.

The Shopify storefront widget is the browser demo surface. If the theme or
tunnel is unavailable, restore it rather than pointing users at the buyer
service root; the buyer service exposes APIs only.
