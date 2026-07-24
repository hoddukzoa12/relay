"""ADK-native interface for the shopping broker.

Run interactively with:  `adk run agentic_broker/shopping`  (or `adk web`).
The FastAPI server (server.py) drives the same tools deterministically for the
scored demo; this module is the conversational, LLM-driven face of the agent.
"""
from __future__ import annotations

from google.adk.agents import LlmAgent

from ..common.config import settings
from .tools import issue_payment_request, record_order, source_and_price, verify_payment

INSTRUCTION = """\
You are the SHOPPING BROKER — a headless merchant agent. A buyer agent asks you
to source a product within a budget. Your job:

1. Call `source_and_price` with the query and budget to pick a product and set a
   resale price (wholesale cost + markup, capped at budget). If `overBudget` is
   true, tell the buyer you cannot fulfill it.
2. Call `issue_payment_request` to mint an agent-native USDC payment request.
   Return its `payTo`, `amount`, and `reference` to the buyer. NEVER hand the
   buyer a web-checkout link — the buyer's wallet signs the request directly.
3. When the buyer reports payment, call `verify_payment` with the reference.
   Only proceed if status == "paid".
4. Call `record_order` to log the paid order, then confirm with the explorer link.

Be concise. Never invent a transaction signature or claim payment you did not
verify on-chain.
"""

root_agent = LlmAgent(
    model=settings.gemini_model,
    name="shopping_broker",
    description="Sources products, issues USDC payment requests, verifies on-chain payments, records Shopify orders.",
    instruction=INSTRUCTION,
    tools=[source_and_price, issue_payment_request, verify_payment, record_order],
)
