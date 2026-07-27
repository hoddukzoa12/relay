# Agents (Python · Google ADK + Gemini)

Two agents plus one remote protocol adapter, in one installable package
(`agentic_broker`):

```
agentic_broker/
  common/      config, contracts (PRD §6 mirror), service clients, Gemini helpers
  shopping/    broker: source → price → issue request → verify → record order
  buyer/       delegated buyer: quote → autonomous sign → settle  (+ demo web UI)
  mcp/         Streamable HTTP tools over the same buyer/common primitives
```

Each agent has **two faces**:

- **FastAPI server** (`*/server.py`) — the deterministic path that drives the
  scored demo. Sourcing/pricing/intent-parsing use Gemini; the money path
  (issue → verify → record) runs in a fixed sequence for reproducibility.
- **ADK agent** (`*/agent.py`, `root_agent`) — the conversational, LLM-driven
  face. Run it with the ADK CLI:

  ```bash
  adk run agentic_broker/shopping     # or: agentic_broker/buyer
  adk web                             # browser playground for both
  ```

Both faces call the **same tool functions** (`*/tools.py`), so behaviour stays
consistent.

## Run

```bash
python -m venv .venv && ./.venv/bin/pip install -e .

# servers (need the TS payments/commerce services up — see repo README)
python -m agentic_broker.shopping.server   # :8091
python -m agentic_broker.buyer.server      # :8090  → open http://localhost:8090
MCP_API_KEY=local-secret \
  python -m agentic_broker.mcp.server       # :8092/mcp

# one-shot from the CLI
python -m agentic_broker.buyer.cli --query "wireless earbuds" --budget 25
python -m agentic_broker.buyer.cli --text "Buy me earbuds under 20 USDC"
```

Without `GOOGLE_API_KEY`, sourcing falls back to deterministic relevance over
real catalog candidates and intent parsing uses a deterministic parser, so the
pipeline still runs end-to-end without inventing products.

The MCP server is stateless Streamable HTTP, not stdio. Its `/health` route is
public, while every request under `/mcp` requires `X-Relay-API-Key`. See
[`../docs/MCP.md`](../docs/MCP.md) for client and lifecycle examples.
