# Issue #49 — Shopify-first login evidence

Prepared on 2026-07-27. The deterministic browser-auth branches are covered by
automation, but Chrome and Safari results remain intentionally pending until a
human approves the buyer-service and preview-theme updates. This branch did not
deploy a service or modify either remote Shopify theme.

## Implemented branch model

The storefront always loads Clerk and checks for an already-active session
before presenting a login action. The browser records the following events
without tokens, wallet addresses, email addresses, or user IDs:

| Event | Meaning |
|---|---|
| `clerk_session_active` / `source=initial` | Clerk was already visible; the widget starts signed in and hides its login button. |
| `shopify_login_required` | No Clerk session was visible; anonymous catalog use remains enabled. |
| `shopify_login_redirect` | The user chose the single Shopify login entry point. |
| `clerk_session_active` / `source=shopify_return` | The Shopify round trip exposed the Clerk session without another prompt. |
| `shopify_return_fallback` | The Shopify round trip completed, but the storefront still could not see a Clerk session. |
| `clerk_fallback_opened` | The widget opened the Clerk in-page fallback after that failed return check. |
| `clerk_session_active` / `source=in_page_fallback` | The fallback established the session. |

Events are retained in a 40-entry, browser-local ring buffer and also emitted as
`relay:auth-branch` window events. The widget exposes the current evidence:

```js
document.querySelector("relay-agent-chat").authEvidence()
```

The host element also exposes `data-auth-branch` and
`data-auth-branch-source`, which makes the active branch visible in DevTools
without opening any application token.

The Shopify redirect uses the Liquid-generated
`routes.storefront_login_url`, overwrites only its relative `return_to`, and
preserves preview and locale query parameters. Shopify documents
`/customer_authentication/login?return_to=...` as the supported theme flow:
<https://shopify.dev/docs/storefronts/themes/sign-in>.

## Automated evidence

Run:

```bash
pnpm test:storefront-auth
```

The tests cover:

1. a pre-existing Clerk session;
2. an active Clerk session whose registered wallet is not yet available;
3. no session, followed by the Shopify login redirect;
4. Shopify return without a Clerk session, followed by the in-page fallback;
5. Shopify return with a Clerk session.

They also assert that the unpublished preview query survives the login round
trip. The normal repository Python tests and TypeScript typecheck remain the
regression gates for the payment path.

## Why real-browser results are pending

The behavior being measured depends on top-level navigation across
`solanagcp.myshopify.com`, `shopify.com`, and the `pk_test_` Clerk development
domain, plus each browser's cookie policy. A headless result would not represent
the signed-in Chrome/Safari profiles used for the demo.

More importantly, the changed section and `auth-client.js` are not yet on the
remote preview. Running a browser against the current preview would measure the
previous revision, while updating the service or theme would violate this
issue's no-deploy rule. A human must approve both changes first.

## Human-approved Chrome and Safari procedure

Use only the unpublished preview theme:

- Allowed: `Horizon + Relay Agent (preview)`, ID `204473499934`
- Forbidden: live theme ID `204459704606`
- Preview:
  <https://solanagcp.myshopify.com/?preview_theme_id=204473499934>

After the buyer revision is approved and the section is synced to the preview
theme, repeat these steps in current Chrome and Safari:

1. Open a fresh private window and the preview URL. Do not authenticate yet.
2. Run `document.querySelector("relay-agent-chat").authEvidence()` in DevTools.
   Record the browser/version, cookie mode, event list, final branch, and source.
3. Submit `wireless earbuds under 10 dollars`. Confirm real catalog cards still
   render with no login.
4. Select a product's purchase action. Confirm it stops with “Sign in through
   Shopify before buying,” sends no `/web/buy`, and opens no Shopify checkout.
5. Select **Sign in with Shopify**. In the Network panel, confirm the first
   storefront route is `/customer_authentication/login`, then record the
   `shopify.com` and Clerk development-domain hops.
6. After returning to the same preview URL, inspect `authEvidence()` again.
   Record either:
   - `clerk_session_active` with `source=shopify_return`, with no second login
     prompt; or
   - `shopify_return_fallback` followed by `clerk_fallback_opened`.
7. Complete the fallback if it appears. Confirm the header says
   `○○님으로 로그인됨`, the widget login button is absent, and the browser
   records `source=in_page_fallback`.
8. Confirm Phantom wallet connection and SPL **Approve once** remain visible.
   These are payment authorization, not a second identity provider.
9. Verify the connected wallet equals the Clerk-verified wallet, approve a
   small devnet allowance, and complete the existing delegated-purchase check.
   Record the devnet explorer link without copying session tokens.

Record the actual results here:

| Browser | Version | Cookie mode | Return branch/source | Fallback opened | Anonymous search | Anonymous payment blocked | Signed-in header | Notes |
|---|---|---|---|---|---|---|---|---|
| Chrome | pending | pending | pending | pending | pending | pending | pending | Awaiting approved preview update |
| Safari | pending | pending | pending | pending | pending | pending | pending | Awaiting approved preview update |

## Identity-provider configuration check

The same human browser session should also verify the configuration; do not
infer it merely because a previous demo once worked:

1. In Shopify Admin, open **Settings → Customer accounts** and confirm the
   active identity provider is the intended Clerk OIDC connection.
2. In Clerk Dashboard, confirm the Shopify OIDC application/connection is
   enabled for the `learning-goshawk-93.clerk.accounts.dev` development
   instance.
3. Confirm the test user has both a verified email (`email_verified=true`,
   required by Shopify OIDC) and a registered Solana Web3 wallet.
4. Capture redacted screenshots showing provider names and enabled state only.
   Never capture client secrets, session tokens, wallet secret keys, or the
   Clerk secret key.

If the provider is absent or disabled, stop browser QA and have the dashboard
owner restore the existing Clerk OIDC connection before retrying. No application
code can safely substitute a Shopify customer token for the Clerk JWT and
server-verified Clerk wallet.
