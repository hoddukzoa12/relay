"""Deterministic end-to-end purchase flow for the buyer agent.

quote (A2A) -> autonomous sign (payments) -> settle (A2A). Returns a trace so
the demo UI can show every step, including the on-chain explorer link.
"""
from __future__ import annotations

from typing import Any

from ..common import llm
from ..common.contracts import IntentMandate
from . import tools


def buy(
    query: str,
    budget: float,
    ship_to: str,
    *,
    intent_mandate: IntentMandate | None = None,
    identity_wallet: str | None = None,
) -> dict[str, Any]:
    # Step 1–3: request a quote / agent-native payment request.
    quote = tools.request_quote(query, budget, ship_to, intent_mandate)

    # Buyer-side guardrail: never overpay the budget it was delegated.
    price = float(quote["price"]["amount"])
    if price > budget:
        return {
            "ok": False,
            "reason": f"quote {price} USDC exceeds budget {budget} USDC",
            "quote": quote,
        }

    # Step 4–6: the wallet signs autonomously and broadcasts the USDC transfer.
    payment = tools.authorize_payment(
        quote["payTo"],
        quote["price"]["amount"],
        quote["reference"],
        quote.get("ap2Mandates"),
    )

    # Step 7–11: hand proof to the broker; it verifies on-chain + records the order.
    settlement_mandates = {
        **quote.get("ap2Mandates", {}),
        **payment.get("ap2Mandates", {}),
    }
    confirmation = tools.confirm_settlement(
        quote["orderRef"],
        quote["reference"],
        payment["txSignature"],
        settlement_mandates,
    )

    return {
        "ok": confirmation.get("status") == "paid",
        "intent": {
            "query": query,
            "budget": budget,
            "shipTo": ship_to,
            **({"delegatedBy": identity_wallet} if identity_wallet else {}),
        },
        "quote": quote,
        "payment": payment,
        "confirmation": confirmation,
    }


def buy_from_text(text: str) -> dict[str, Any]:
    """Parse a natural-language instruction, then run the purchase."""
    intent = llm.parse_purchase(text)
    return buy(intent["query"], intent["budget"], intent["shipTo"])
