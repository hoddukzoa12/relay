# Issue #57 — public chat authentication gate

Verified locally on 2026-07-27 against the live Shopify catalog and Solana
devnet. No Cloud Run service, Shopify theme, or payments-service code was
changed or deployed.

## Root cause and server-side boundary

`POST /chat` intentionally accepts anonymous requests so catalog search and
comparison remain public. It previously passed `identity_wallet=None` into the
conversation runner and relied on a `ContextVar` around the caller to distinguish
that storefront request from a principal-free agent call.

Google ADK's synchronous `Runner.run` starts a plain background
`threading.Thread`. Python context variables are not copied into that thread, so
the payment tools observed no storefront context and selected the legitimate
CLI/service-principal agent-wallet fallback. This matches the deployed
transaction reported in the issue.

The repaired boundary does not depend on the prompt or thread context:

- the FastAPI route still resolves any bearer token with Clerk and passes only
  the verified wallet to the conversation service;
- every ADK run writes a server-owned `chat_request`, verified wallet, and
  approval proof into the tool context state, explicitly clearing both values
  on every anonymous turn;
- `request_quote`, `authorize_payment`, and `confirm_settlement` each check that
  state before calling any lower-level tool;
- `request_quote` recreates the storefront context inside the actual ADK tool
  thread and verifies the approval transaction on-chain;
- `authorize_payment` always passes the verified wallet as `delegator`;
- deterministic fallback and partial-turn settlement recovery apply the same
  authentication gate.

The refusal is executable JSON: `paymentBlocked=true`,
`authRequired=true`, `status=auth-required`, and `action=sign-in`. The response
does not expose a quote, payment, confirmation, or transaction signature.

## Exact unauthenticated reproduction

The local stack was started with `./scripts/dev.sh`. An anonymous search first
established the same multi-turn state as the issue:

```http
POST http://localhost:8090/chat
Content-Type: application/json

{
  "sessionId": "issue57_repro_20260727",
  "message": "Find wireless earbuds under 5 USDC. My shipping address is Name: Issue 57 Test; Address1: 123 Main St; City: Arlington; Province: VA; Country: US; ZIP: 22201. Only search and compare; do not buy."
}
```

The response was HTTP 200, called only `search_catalog`, and returned three
live in-stock products at 4.21, 2.13, and 4.69 USDC. After selecting the first
without buying, the issue's request was repeated verbatim and without an
`Authorization` header:

```http
POST http://localhost:8090/chat
Content-Type: application/json

{
  "sessionId": "issue57_repro_20260727",
  "message": "그걸로 구매해줘."
}
```

Observed result:

```json
{
  "toolCalls": ["request_quote"],
  "quote": null,
  "payment": null,
  "confirmation": null,
  "paymentBlocked": true,
  "authRequired": true,
  "paymentGate": {
    "status": "auth-required",
    "authRequired": true,
    "paymentBlocked": true,
    "action": "sign-in"
  }
}
```

The model replied that login was required. The model was allowed to attempt the
tool call; the backend tool boundary refused it before quote issuance or money
movement.

`make check-wallets` read the buyer-agent ATA from devnet immediately before and
after the attempt:

| Wallet | Before | After | Delta |
|---|---:|---:|---:|
| Buyer agent `7dwPd8g…xrr` | 16.60 USDC | 16.60 USDC | **0.00 USDC** |
| Merchant `H8Zzkb…kkkM` | 21.74 USDC | 21.74 USDC | **0.00 USDC** |

No payment proof or Shopify order was produced by the rejected request.

## Authenticated chat still pays from the user

The existing devnet helper created a fresh git-ignored throwaway user, funded
2.20 USDC, and approved the buyer agent as SPL delegate. The funding transfer is
an explicit test setup transaction, separate from the purchase:

- Verified user: `AV16HkRnuAeQy84wYxjPuyr9KNbrCvYToKNNab7ns8SN`
- User ATA: `3aYJd3aM9jaU3tkaNFPqG2jVF1zXbZdu4FgFjaKSZSo4`
- Setup funding transaction:
  `5ktZh7kjTKccSSx5tRxW7BaPZxrLmbbJrPgsVaYxYntpsmmuQnZJaey4bjfgVYQMQPUEVFjvPNCxoP7LXSi2AbqC`
- Approval transaction:
  `4J9XVL45bQeH1ZNKvk5rznyswjxhVK3Jx2KyjJNrbCur31zAgMzA5BWigX5ywcFARMGQsZSFHgyJt4N6jGKs6PA3`
- Approval explorer:
  <https://explorer.solana.com/tx/4J9XVL45bQeH1ZNKvk5rznyswjxhVK3Jx2KyjJNrbCur31zAgMzA5BWigX5ywcFARMGQsZSFHgyJt4N6jGKs6PA3?cluster=devnet>

`POST /chat` was then exercised locally with a test-only replacement for Clerk
token verification. The wallet still entered the application only as the
server-created `ClerkIdentity`; no wallet selector was accepted from the
request body. The authenticated request completed:

```text
search_catalog → request_quote → authorize_payment → confirm_settlement
```

- Amount: 2.13 USDC
- Order ref: `ord_dc4b09350c4041489492d5c655594775`
- Shopify order: `gid://shopify/Order/8711766409502`
- Payment transaction:
  `2L3DMsTZYeLyP91TgLoKdjAaSBLiwj4mB9Cz9CdUw4x2KweWSo51ZCEv4TUfNsLgCb3wRF4pWBspvXjcgH47p6vs`
- Explorer:
  <https://explorer.solana.com/tx/2L3DMsTZYeLyP91TgLoKdjAaSBLiwj4mB9Cz9CdUw4x2KweWSo51ZCEv4TUfNsLgCb3wRF4pWBspvXjcgH47p6vs?cluster=devnet>

The confirmed transaction metadata proves the payer selection:

| On-chain field | Before | After | Delta |
|---|---:|---:|---:|
| Verified user USDC | 2.20 | 0.07 | **-2.13** |
| Merchant USDC | 21.74 | 23.87 | **+2.13** |
| Buyer-agent USDC after test funding | 14.40 | 14.40 | **0.00** |

The sole transaction signer and fee payer was the buyer agent
`7dwPd8g…xrr`; the source token owner was the verified user
`AV16Hk…s8SN`. The user supplied zero purchase signatures after the one-time SPL
approval.

## A2A audit

No additional human-present path that silently selects the buyer-agent USDC
source was found:

| Endpoint/path | Principal rule | Money behavior |
|---|---|---|
| Buyer service | Exposes only A2A agent-card discovery; no buyer `POST /a2a` purchase handler | No A2A payment entry point |
| Shopping `POST /a2a` human IntentMandate | Requires `delegator == signer_wallet` and a valid Ed25519 wallet signature | Issues a quote only |
| Shopping `POST /a2a` agent IntentMandate | Requires the configured buyer-agent mandate signature | Principal-free agent compatibility path |
| Shopping A2A PaymentMandate | Requires the buyer-agent signature, cart/intent binding, exact amount, and delegated payer match | Verifies a transfer already submitted by the buyer; never calls `/pay` |
| Legacy `/a2a/quote` and `/a2a/settle` | Principal-free agent compatibility routes | Quote issuance or reference-bound on-chain verification only; never initiates payment |
| MCP OAuth | Clerk OAuth wallet from authentication middleware | Verified user delegation |
| MCP API key, CLI, rehearsal, `/buy` | Explicit service/agent principal | Preserved buyer-agent wallet fallback |

The only Python calls to `payments_pay` remain in buyer tools. The public human
chat reaches those tools only through the new verified-principal gate. A
regression test also verifies that a forged human A2A signature fails before
quote creation.

## Automated verification

- Focused buyer/MCP/A2A security tests: 52 passed
- Full Python agents with the local DSers target cleared for test isolation:
  93 passed
- `pnpm -r typecheck`: pass
- Payments: 8 passed
- Commerce: 24 passed
- Storefront auth: 6 passed
- Shopify seed policy: 2 passed
- DSers supplier-cost policy: 4 passed
- All shared JSON schemas parse successfully
- `git diff --check`: pass

No canonical contract changed, so the TypeScript/Pydantic/JSON contract mirrors
remain unchanged. `services/payments` and
`sections/relay-agent-chat.liquid` were not modified.
