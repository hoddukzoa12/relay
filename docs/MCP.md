# Relay MCP server

Relay exposes its existing autonomous commerce workflow through the Model
Context Protocol. The endpoint uses remote **Streamable HTTP** at `/mcp`; there
is no stdio-only server and no Shopify checkout or human payment approval.

## Tools

| Tool | Inputs | Delegates to |
|---|---|---|
| `search_products` | `query`, `limit` (1–50) | commerce catalog |
| `request_quote` | `query`, `budget`, `ship_to` | buyer → shopping A2A quote |
| `authorize_payment` | `pay_to`, `amount`, `reference` | payments autonomous signer |
| `settle` | `order_ref`, `reference`, `tx_signature` | shopping verification + Shopify ledger |
| `get_order_status` | `order_ref` | shopping order lifecycle |
| `refund_order` | `order_ref` | shopping full on-chain refund |
| `wallet_balances` | none | buyer display-only balance endpoint |

The intended money path is:

```text
request_quote → authorize_payment → settle → get_order_status
                                             └→ refund_order
```

Pass the quote fields through unchanged. `authorize_payment` signs and
broadcasts USDC autonomously. `settle` does not trust the supplied signature
alone: shopping re-verifies the amount, recipient, and reference on Solana
devnet before it records the Shopify order.

## Authentication boundary

The Cloud Run MCP service is publicly routable so remote clients can reach it,
but the mounted protocol endpoint fails closed unless every request includes:

```http
X-Relay-API-Key: <high-entropy shared secret>
```

This check covers initialization, tool discovery, and tool calls. `/health` is
the only public route and exposes no key or wallet material. The secret is
stored as `relay-mcp-api-key` in Secret Manager and injected only into the MCP
runtime as `MCP_API_KEY`. Never put the key in a URL, commit it, or send it to
an untrusted MCP host.

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
present. Add `--purchase` for the autonomous payment path and `--refund` for
the full bidirectional on-chain lifecycle:

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

Generic remote-client configuration:

```json
{
  "mcpServers": {
    "relay": {
      "type": "http",
      "url": "https://MCP_SERVICE_URL/mcp",
      "headers": {
        "X-Relay-API-Key": "${RELAY_MCP_API_KEY}"
      }
    }
  }
}
```

Client configuration syntax varies, but the URL and header are the same. For a
browser-based inspector, set `MCP_CORS_ORIGINS` to the inspector origin (or `*`
for a short-lived demo) and configure the same header.

Unauthenticated protocol access must return `401`:

```bash
curl -i -X POST https://MCP_SERVICE_URL/mcp \
  -H 'Content-Type: application/json' \
  --data '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'
```

## Cloud Run deployment

Cloud deployment is an explicit approval-gated operation because it changes
billable project state. The prepared deployment:

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

After approval and deployment, validate the remote URL:

```bash
RELAY_MCP_URL="https://MCP_SERVICE_URL/mcp" \
MCP_API_KEY="$MCP_API_KEY" \
  agents/.venv/bin/python scripts/mcp-client.py --purchase --refund
```

Capture the tool list, paid and refund explorer URLs, Shopify order ID, and the
Cloud Run region/min-scale inspection in the pull request evidence.
