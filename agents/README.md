# Agents (Python · Google ADK + Gemini)

Two agents, one installable package (`agentic_broker`):

```
agentic_broker/
  common/      config, contracts (PRD §6 mirror), service clients, Gemini helpers
  shopping/    broker: source → price → issue request → verify → record order
  buyer/       delegated buyer: quote → autonomous sign → settle  (+ demo web UI)
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

# one-shot from the CLI
python -m agentic_broker.buyer.cli --query "wireless earbuds" --budget 25
python -m agentic_broker.buyer.cli --text "Buy me earbuds under 20 USDC"
```

Without `GOOGLE_API_KEY`, the Gemini helpers fall back to deterministic stubs so
the pipeline still runs end-to-end — set the key to get real AI sourcing/parsing.
