# Relay MCP server

Relay exposes its existing autonomous commerce workflow through the Model
Context Protocol. The endpoint uses remote **Streamable HTTP** at `/mcp`; there
is no stdio-only server and no Shopify checkout or human payment approval.

## Tools

| Tool | Inputs | Delegates to |
|---|---|---|
| `search_products` | `query`, `limit` (1–50) | commerce catalog |
| `request_quote` | `query`, `budget`, `ship_to`, optional `shipping_address` | buyer → shopping A2A quote, bound to the OAuth wallet when present |
| `authorize_payment` | `pay_to`, `amount`, `reference` | autonomous signer; OAuth user ATA or service-principal agent wallet |
| `settle` | `order_ref`, `reference`, `tx_signature` | shopping verification + Shopify ledger |
| `get_order_status` | `order_ref` | shopping order lifecycle |
| `refund_order` | `order_ref` | shopping full on-chain refund |
| `wallet_balances` | none | buyer display-only balance endpoint |

The intended money path is:

```text
request_quote → authorize_payment → settle → get_order_status
                                             └→ refund_order
```

`shipping_address` uses recipient `name`, `address1`, optional `address2`,
`city`, `province`, ISO-2 `country`, `zip`, and optional `phone`; it is signed
into the IntentMandate and is never inferred from `ship_to`. Even when supplied,
commerce omits Shopify `shippingAddress` unless the default-off
`SUPPLIER_FULFILLMENT_ENABLED` money gate is explicitly enabled.

Pass the quote fields through unchanged. `authorize_payment` signs and
broadcasts USDC autonomously. `settle` does not trust the supplied signature
alone: shopping re-verifies the amount, recipient, and reference on Solana
devnet before it records the Shopify order.

For an OAuth caller, both quote and payment derive the payer only from the
server-verified Clerk wallet. If its live delegation is missing, exhausted, or
too small, the tool returns without submitting a transfer:

```json
{
  "status": "approval-required",
  "reason": "SPL delegation is missing or revoked; approve the broker once.",
  "delegator": "<verified OAuth wallet>",
  "requiredAmount": { "amount": "2.21", "currency": "USDC" },
  "allowanceRemaining": { "amount": "0", "currency": "USDC" },
  "balance": { "amount": "3", "currency": "USDC" },
  "approvalUrl": "https://<store>/?relayAction=approve&relayAmount=2.21"
}
```

Open `approvalUrl` once and retry. Relay never silently retries an
authenticated user's request from the configured agent wallet.

## Authentication boundary

The Cloud Run MCP service is publicly routable so remote clients can reach it,
but the mounted protocol endpoint fails closed. User-facing clients authenticate
with a Clerk OAuth access token:

```http
Authorization: Bearer <Clerk OAuth access token>
```

Relay verifies the token signature and issuer against `CLERK_JWKS_URL`, checks
with Clerk that it is an active OAuth access token with the `openid` scope, and
loads the token subject's verified Solana web3 wallet from Clerk. A session JWT
or OIDC ID token is not accepted in place of the OAuth access token.

The server exposes RFC 9728 protected-resource metadata at:

```text
/.well-known/oauth-protected-resource/mcp
```

The `401` challenge points clients to that document, which points to
`CLERK_ISSUER`. Standards-aware MCP clients then discover Clerk's authorization
and token endpoints and use authorization code + S256 PKCE.

### Clerk setup

Before deployment, a human administrator must use the Clerk Dashboard's
**OAuth applications** settings to:

1. Enable dynamic OAuth client registration for MCP clients that require it.
2. Set dynamic-registration default scopes to `openid profile email`; some
   clients, including ChatGPT and Claude, omit `scope` when registering.
3. Keep JWT OAuth access tokens enabled so Relay can verify them with the
   configured JWKS.

Dynamic registration is intentionally an administrator decision because it
opens a public client-registration endpoint. A known client may instead use a
pre-registered public OAuth application with PKCE and its exact redirect URI.
See Clerk's [MCP client guide](https://clerk.com/docs/guides/ai/mcp/connect-mcp-client)
and [OAuth implementation guide](https://clerk.com/docs/guides/configure/auth-strategies/oauth/how-clerk-implements-oauth).

### Payer and order ownership

Before `authorize_payment`, Relay passes only the wallet resolved server-side
from the Clerk token as the SPL `delegator`; the buyer agent signs as delegate
and pays transaction fees, but USDC leaves the user's ATA. On `settle`, the same
identity is forwarded to the private shopping service and Shopify records it as
`buyer_wallet`. OAuth-authenticated status and refund calls are limited to
orders whose `buyer_wallet` matches the caller.

The internal `X-Relay-Authenticated-Wallet` header binds settlement ownership
only; it cannot choose or override the already-submitted payment source. It is
not accepted as an MCP header or tool argument, and shopping must remain behind
Cloud Run IAM. A signed A2A human IntentMandate similarly requires `delegator`
to equal `signer_wallet`.

### Service API key

The shared API key remains only for trusted service-to-service or pure-agent
clients that legitimately have no user wallet:

```http
X-Relay-API-Key: <high-entropy service secret>
```

This compatibility path is a privileged service principal, not a user
credential. It keeps existing autonomous clients working and attributes their
orders to the configured Relay buyer wallet. Do not distribute it to end users;
rotate it if exposed. The secret remains stored as `relay-mcp-api-key` and is
injected only as `MCP_API_KEY`.

The payments, commerce, and shopping services remain private behind Cloud Run
IAM. The `relay-mcp` service account receives only `roles/run.invoker` on those
peers; it does not receive wallet secrets. Payments remains the sole service
that holds the buyer and merchant keypairs.

## Local client

Install dependencies, configure a development key, and run the stack:

```bash
make setup
export MCP_API_KEY="$(openssl rand -hex 32)"
./scripts/dev.sh
```

In a second terminal, use the included official-SDK client:

```bash
MCP_API_KEY="$MCP_API_KEY" \
  agents/.venv/bin/python scripts/mcp-client.py
```

It initializes a Streamable HTTP session and asserts that all seven tools are
present. For a user-attributed smoke test, pass a Clerk OAuth access token
obtained by an OAuth client:

```bash
MCP_OAUTH_TOKEN="<access-token>" \
  agents/.venv/bin/python scripts/mcp-client.py
```

The script requires exactly one of `MCP_OAUTH_TOKEN` and `MCP_API_KEY`. Add
`--purchase` for the autonomous payment path and `--refund` for the full
bidirectional on-chain lifecycle:

```bash
MCP_API_KEY="$MCP_API_KEY" \
  agents/.venv/bin/python scripts/mcp-client.py \
  --purchase --refund --query "wireless earbuds" --budget 5
```

To resume lookup or safely replay a refund after a client disconnect:

```bash
MCP_API_KEY="$MCP_API_KEY" \
  agents/.venv/bin/python scripts/mcp-client.py \
  --order-ref ord_example --refund
```

Generic OAuth-aware remote-client configuration needs only the resource URL;
the client discovers Clerk from Relay's protected-resource metadata:

```json
{
  "mcpServers": {
    "relay": {
      "type": "http",
      "url": "https://mcp-763kssfe2q-uc.a.run.app/mcp"
    }
  }
}
```

Client configuration syntax varies. For a browser-based inspector, set
`MCP_CORS_ORIGINS` and `MCP_ALLOWED_ORIGINS` to the inspector origin; the
browser sends the OAuth bearer header after PKCE completes. Relay keeps MCP SDK
DNS-rebinding protection enabled and allowlists both Cloud Run hostnames
through `MCP_ALLOWED_HOSTS`.

Unauthenticated protocol access must return `401`:

```bash
curl -i -X POST https://mcp-763kssfe2q-uc.a.run.app/mcp \
  -H 'Content-Type: application/json' \
  --data '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'
```

## Cloud Run deployment

Cloud deployment is an explicit approval-gated operation because it changes
billable project state. Do not enable Clerk dynamic registration or deploy this
change without the coordinator/human approval gate. The previous approved
2026-07-27 deployment:

- builds three images (`payments`, `commerce`, and the shared `agents` image);
- redeploys payments, commerce, shopping, and buyer from the current commit;
- deploys MCP from that same `agents` image;
- uses only project `web3research`, region `us-central1`;
- configures one vCPU, 512 MiB, concurrency 20, maximum two instances, and no
  minimum instances for every service;
- creates the `relay-mcp` runtime service account and sixth Secret Manager
  secret, `relay-mcp-api-key`;
- deletes Artifact Registry image copies and Cloud Build sources after Cloud
  Run imports the revisions.

Validate the deployed remote URL:

```bash
RELAY_MCP_URL="https://mcp-763kssfe2q-uc.a.run.app/mcp" \
MCP_API_KEY="$MCP_API_KEY" \
  agents/.venv/bin/python scripts/mcp-client.py --purchase --refund
```

After the OAuth configuration and deployment are approved, validate user
attribution with a real Clerk OAuth access token and a small devnet purchase:

```bash
RELAY_MCP_URL="https://mcp-763kssfe2q-uc.a.run.app/mcp" \
MCP_OAUTH_TOKEN="$MCP_OAUTH_TOKEN" \
  agents/.venv/bin/python scripts/mcp-client.py \
  --purchase --query "wireless earbuds" --budget 1
```

Then confirm the Shopify order's `buyer_wallet` custom attribute matches the
verified Clerk user's Solana wallet. The unauthenticated curl check above must
still return `401`.

The deployed purchase/refund proof and Cloud Run inventory are recorded in
[`evidence/issue-17-mcp-deployed.md`](evidence/issue-17-mcp-deployed.md).
