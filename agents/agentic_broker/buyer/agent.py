"""ADK-native interface for the buyer agent.

Run interactively with:  `adk run agentic_broker/buyer`  (or `adk web`).
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any

from google.adk.agents import LlmAgent
from google.adk.tools import ToolContext

from ..common.config import settings
from . import tools as buyer_tools

INSTRUCTION = """\
You are Relay, a conversational BUYER agent acting for a human. You have
multi-turn memory and must use tools for every catalog, payment, and order fact.
Never invent products, stock, prices, order IDs, or transaction proofs.

Conversation policy:
1. A usable shopping request needs both a positive maximum budget in USDC and a
   shipping address. If either is missing, ask a short follow-up question. Do
   not silently substitute a default.
2. Once both are known, call `search_catalog`. Compare up to three suitable
   candidates in your own concise words, including meaningful price and stock
   differences. Use the exact tool results. For follow-ups such as "anything
   cheaper?" or "how much stock is left?", use the remembered results or call
   `search_catalog` again with a refined query/budget.
3. Searching and comparing is non-binding. Do not spend funds until the human
   explicitly selects an item or delegates the choice with language such as
   "buy it", "choose the cheapest", or "go ahead". A fully explicit initial
   command beginning with "buy" also counts as delegation.
4. After explicit delegation, call `request_quote` with the exact selected
   product title as the query, the user's maximum budget, and their shipping
   address. If the quote exceeds the budget, stop and explain.
5. Otherwise call `authorize_payment` with the quote's payTo, price.amount, and
   reference. This signs and sends USDC autonomously; never ask for a wallet
   click or send the human to Shopify checkout.
6. Immediately call `confirm_settlement` with the quote's orderRef/reference
   and the returned txSignature.
7. Only say a purchase completed when settlement status is exactly "paid".
   Include the product, amount, Shopify order ID, and Solana explorer link.
8. For order questions, call `get_order_status` with an orderRef or Shopify
   order name. Keep demo tracking claims clearly labeled if present, and never
   promise proactive notifications or future actions you cannot perform.

Be natural and concise. Use plain text only, without Markdown. The user should
see your own response text alongside structured product cards or payment
progress supplied by the API.
"""


def search_catalog(
    query: str,
    budget: float,
    tool_context: ToolContext,
) -> dict[str, Any]:
    """Search the live catalog for in-stock products within ``budget`` USDC."""
    result = buyer_tools.search_catalog(query, budget)
    tool_context.state["relay:last_search"] = result
    tool_context.state["relay:last_budget"] = budget
    return result


def request_quote(
    query: str,
    budget: float,
    ship_to: str,
    tool_context: ToolContext,
) -> dict[str, Any]:
    """Request an agent-native quote for an explicitly selected product."""
    quote = buyer_tools.request_quote(query, budget, ship_to)
    quoted_amount = Decimal(str(quote["price"]["amount"]))
    ceiling = Decimal(str(budget))
    if quoted_amount > ceiling:
        raise ValueError(
            f"quote {quoted_amount} USDC exceeds budget {ceiling} USDC"
        )
    tool_context.state["relay:last_budget"] = budget
    tool_context.state["relay:last_ship_to"] = ship_to
    tool_context.state["relay:last_quote"] = quote
    return quote


def authorize_payment(
    pay_to: str,
    amount: str,
    reference: str,
    tool_context: ToolContext,
) -> dict[str, Any]:
    """Autonomously sign the exact in-session quote after budget validation."""
    quote = tool_context.state.get("relay:last_quote")
    budget = tool_context.state.get("relay:last_budget")
    if not isinstance(quote, dict) or budget is None:
        raise ValueError("request_quote must succeed before authorize_payment")
    if (
        quote.get("payTo") != pay_to
        or quote.get("reference") != reference
        or str(quote.get("price", {}).get("amount")) != str(amount)
    ):
        raise ValueError("payment arguments do not match the active quote")
    if Decimal(str(amount)) > Decimal(str(budget)):
        raise ValueError(f"payment {amount} USDC exceeds budget {budget} USDC")

    payment = buyer_tools.authorize_payment(
        pay_to,
        amount,
        reference,
        quote.get("ap2Mandates"),
    )
    tool_context.state["relay:last_payment"] = payment
    return payment


def confirm_settlement(
    order_ref: str,
    reference: str,
    tx_signature: str,
    tool_context: ToolContext,
) -> dict[str, Any]:
    """Verify the exact in-session payment and record the Shopify order."""
    quote = tool_context.state.get("relay:last_quote")
    payment = tool_context.state.get("relay:last_payment")
    if not isinstance(quote, dict) or not isinstance(payment, dict):
        raise ValueError(
            "request_quote and authorize_payment must succeed before settlement"
        )
    if quote.get("orderRef") != order_ref or quote.get("reference") != reference:
        raise ValueError("settlement arguments do not match the active quote")
    if payment.get("txSignature") != tx_signature:
        raise ValueError("settlement signature does not match the active payment")

    mandates = {
        **quote.get("ap2Mandates", {}),
        **payment.get("ap2Mandates", {}),
    }
    confirmation = buyer_tools.confirm_settlement(
        order_ref,
        reference,
        tx_signature,
        mandates,
    )
    tool_context.state["relay:last_confirmation"] = confirmation
    return confirmation


def get_order_status(identifier: str) -> dict[str, Any]:
    """Get a Relay order by orderRef or Shopify order name."""
    return buyer_tools.get_order_status(identifier)


root_agent = LlmAgent(
    model=settings.gemini_model,
    name="buyer",
    description=(
        "Conversational buyer that searches a live catalog and autonomously "
        "settles delegated USDC purchases."
    ),
    instruction=INSTRUCTION,
    tools=[
        search_catalog,
        request_quote,
        authorize_payment,
        confirm_settlement,
        get_order_status,
    ],
)
