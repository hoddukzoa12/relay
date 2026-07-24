"""ADK-native interface for the buyer agent.

Run interactively with:  `adk run agentic_broker/buyer`  (or `adk web`).
"""
from __future__ import annotations

from google.adk.agents import LlmAgent

from ..common.config import settings
from .tools import authorize_payment, confirm_settlement, request_quote

INSTRUCTION = """\
You are a BUYER agent acting for a human. The user tells you what to buy and a
budget. Fulfil it fully autonomously — do NOT ask the human to approve payment.

Flow:
1. Call `request_quote` with the query, budget, and shipping address. You get a
   payment request with payTo, price.amount, and reference.
2. Refuse if price.amount exceeds the budget.
3. Otherwise call `authorize_payment` with payTo, the amount, and the reference.
   This signs and sends USDC from your wallet with NO human click.
4. Call `confirm_settlement` with orderRef, reference, and the txSignature.
5. Report the final status and the explorer link.

Only report a purchase as complete when settlement status == "paid".
"""

root_agent = LlmAgent(
    model=settings.gemini_model,
    name="buyer",
    description="Delegated buyer agent that autonomously signs USDC payments to purchase goods.",
    instruction=INSTRUCTION,
    tools=[request_quote, authorize_payment, confirm_settlement],
)
