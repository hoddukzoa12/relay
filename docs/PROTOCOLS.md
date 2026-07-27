# Protocol reference — A2A · AP2 · x402

Concrete spec notes + how they map to Relay. Backs issue **#9** (A2A/AP2) and
**#6** (x402). Judging criteria ③ (protocol integration) and ② (Google stack).

> **Positioning.** Shopify's own Solana Pay is a *human web-checkout* (scan QR,
> approve in wallet). We are agent-native: the wallet signs with no human click.
> We lead the "차세대 결제 프로토콜" story with **Google's own stack** — A2A + AP2 —
> and use **x402** as the on-chain execution surface (AP2's crypto extension).
> **Solana Pay stays the guaranteed live-tx rail; these are additive.**

**A2A로 협상 · AP2 mandate로 인가 · Solana Pay로 정산.** (USDC 온체인)

---

## A2A (Agent2Agent) — JSON-RPC 2.0 binding, v0.3.0

**Agent Card** — public JSON at `https://<host>/.well-known/agent-card.json` (no auth):

```json
{
  "protocolVersion": "0.3.0",
  "name": "Relay Shopping Broker",
  "description": "Headless merchant broker: sources, prices, settles USDC on Solana.",
  "url": "https://<host>/a2a",
  "preferredTransport": "JSONRPC",
  "version": "0.1.0",
  "provider": { "organization": "Relay", "url": "https://github.com/hoddukzoa12/relay" },
  "capabilities": { "streaming": false, "pushNotifications": false },
  "defaultInputModes": ["application/json", "text/plain"],
  "defaultOutputModes": ["application/json", "text/plain"],
  "skills": [
    { "id": "quote",  "name": "Quote",  "description": "Source a product and issue an agent-native USDC payment request (CartMandate).", "tags": ["commerce","payments"], "examples": ["Buy wireless earbuds under 5 USDC"] },
    { "id": "settle", "name": "Settle", "description": "Verify the on-chain USDC payment and record the order.", "tags": ["payments","solana"] }
  ]
}
```

**Transport:** HTTP `POST` a single JSON-RPC 2.0 endpoint. Methods:
`message/send`, `message/stream`, `tasks/get`, `tasks/list`, `tasks/cancel`,
`tasks/resubscribe`, `tasks/pushNotificationConfig/{set,get,list,delete}`,
`agent/getAuthenticatedExtendedCard`.

**Message:** `{ messageId, role: "user"|"agent", parts: [Part], taskId?, contextId?, kind: "message", metadata? }`

**Part** (discriminated by `kind`):
- `{ "kind": "text", "text": "..." }`
- `{ "kind": "data", "data": { ... } }`   ← carries mandates
- `{ "kind": "file", "file": { "mimeType", "name", "fileWithUri" | "fileWithBytes" } }`

**Task:** `{ id, contextId, status: { state, message?, timestamp? }, artifacts?, history?, kind: "task" }`
**TaskState:** `submitted | working | input-required | completed | canceled | failed | rejected | auth-required`

`message/send` request → response example:

```json
// request
{ "jsonrpc":"2.0","id":"1","method":"message/send",
  "params": { "message": { "kind":"message","messageId":"m1","role":"user",
    "parts": [ {"kind":"data","data":{"ap2.mandates.IntentMandate":{ /* … */ }}} ] } } }
// response
{ "jsonrpc":"2.0","id":"1","result": { "kind":"task","id":"t1","contextId":"c1",
    "status": {"state":"completed"},
    "artifacts": [ {"parts":[{"kind":"data","data":{"ap2.mandates.CartMandate":{ /* … */ }}}]} ] } }
```

---

## AP2 (Agent Payments Protocol) — rides on A2A

Google Cloud + Coinbase (2025-09). Mandates are **verifiable digital credentials**
carried as A2A **DataParts**, keyed by type, e.g.
`{ "kind":"data", "data": { "ap2.mandates.PaymentMandate": { … } } }`.

**Actors** (AP2 → Relay):
- Shopping Agent (user side) → our **buyer** agent
- Merchant Agent + Merchant Payment Agent → our **shopping** broker
- Payment Credential Provider → the **payments** service (holds the wallet)

**The three mandates**

| Mandate | Signed by | Contains (modeled on `ap2.types.mandate`) |
|---|---|---|
| **IntentMandate** | buyer agent, attesting the verified human identity + live on-chain SPL delegation (principal-free compatibility paths omit delegation fields) | `natural_language_description`, price ceiling, `intent_expiry`, plus `delegator`, `delegateAuthority`, `allowanceRemaining`; browser approval flows also record `approvalTxSignature` |
| **CartMandate** | **merchant** (shopping) | `contents`: real catalog cart_items `[{sku,variant_id,name,price}]`, `total`, `currency`, shipping/tax, `refund_period`, `cart_expiry`, `merchant_name` |
| **PaymentMandate** | user (buyer) | payment method token, `amount`+`currency`, `merchant_name`, payer info, `timestamp`; bound to the Cart/Intent mandate |

**Human-present web flow:** Clerk resolves the verified Solana wallet → that
wallet signs one SPL Token `Approve`, setting the buyer-agent wallet as delegate
with a fixed USDC limit → the buyer agent records the approval transaction and a
live allowance snapshot in its IntentMandate → the merchant returns a signed
CartMandate → the buyer agent issues a PaymentMandate and spends directly from
the human wallet's USDC ATA as delegate. The agent remains transaction fee payer,
so the user needs no SOL and sees zero wallet prompts on later purchases.

The allowance SSOT is always the token account. Immediately before every
authenticated-user transfer, `payments` calls `getAccount(sourceAta)` and checks the owner,
delegate, delegated amount, and USDC balance. A signed mandate snapshot never
authorizes money by itself. Revoke or an exhausted/insufficient allowance blocks
the purchase with an `approval-required` response containing an executable
storefront approval URL. It never falls through to the broker buyer wallet.

### Who pays

Payer selection follows the authenticated principal, not the transport:

| Situation | USDC source |
|---|---|
| Web chat with a verified Clerk session | verified user's wallet via SPL delegation |
| MCP with verified Clerk OAuth | verified user's wallet via SPL delegation |
| A2A carrying a verified/signed human identity | that signed user's wallet via SPL delegation |
| MCP API-key service principal with no user | configured buyer-agent wallet |
| CLI, rehearsal, or legacy request with no human principal | configured buyer-agent wallet |
| Broker supplier purchase (Leg 2) | External DSers/AliExpress account via Shopify auto-ordering, only when the default-off supplier money gate and complete structured address are both present |

The buyer-agent wallet remains transaction signer/delegate and fee payer for a
user-funded Leg 1 purchase, so repeat purchases need zero user signatures.
Only principal-free API-key/CLI/rehearsal paths may use it as the USDC source.
An authenticated user with no delegation, an exhausted limit, or insufficient
balance receives the approval URL and no payment transaction is submitted.

> Align exact field names to the reference types in
> `google-agentic-commerce/AP2` (`src/ap2/types/mandate.py`) at implementation time.

---

## x402 — on-chain settlement (AP2 crypto extension)

Coinbase × Google × Ethereum Foundation × MetaMask. Flow:

1. Agent requests a resource → server replies **HTTP 402** with structured payment
   instructions (price, accepted tokens, recipient, network).
2. Agent builds a signed payment payload and **retries with it attached in a
   header**.
3. The resource server hands the payload to a **facilitator**, which verifies
   signature + balance + no replay, then **settles** on-chain.

**Networks/assets:** **Solana (USDC, SPL)** via a **free Coinbase CDP facilitator**;
also Base, Polygon. → an HTTP wrapper over the same USDC-on-Solana we already settle.

> Confirm exact field names against the x402 spec (`accepts` /
> `paymentRequirements`: `scheme`, `network`, `maxAmountRequired`, `resource`,
> `payTo`, `asset`; `X-PAYMENT` header) before implementing #6.

---

## Mapping to Relay code

| Current (REST) | A2A/AP2 target |
|---|---|
| `PurchaseIntent {query,budget,shipTo,shippingAddress?}` | **IntentMandate** (buyer-signed; structured address is signed when supplied, and web mandates also bind the Clerk wallet's SPL delegation proof) |
| `PaymentRequest {title,price,payTo,reference}` | **CartMandate** (merchant-signed) |
| buyer autonomous pay authorization | **PaymentMandate** (buyer-signed) → settled via Solana Pay tx (guaranteed) or x402 (#6) |
| `POST /a2a/quote`, `/a2a/settle` (REST) | JSON-RPC `message/send` with mandate **DataParts** (add endpoint; keep REST for compat) |
| `GET /a2a/agent-card` (ad-hoc) | `GET /.well-known/agent-card.json` (spec fields) |
| — | **Signing:** reuse the Solana wallet ed25519 keys (`nacl.sign.detached`). Buyer signs Intent/Payment; merchant signs Cart. The payments service (TS, holds keys) exposes sign/verify. This ties AP2 mandates to the on-chain identity — clean integration story. |

## Sources
- [A2A spec v0.3.0](https://a2a-protocol.org/v0.3.0/specification/) · [a2aproject/A2A](https://github.com/a2aproject/A2A)
- [Announcing AP2 — Google Cloud](https://cloud.google.com/blog/products/ai-machine-learning/announcing-agents-to-payments-ap2-protocol) · [AP2 illustrated guide](https://arthurchiao.art/blog/ap2-illustrated-guide/) · [google-agentic-commerce/AP2](https://github.com/google-agentic-commerce/AP2)
- [Google AP2 + x402 — Coinbase](https://www.coinbase.com/developer-platform/discover/launches/google_x402)
