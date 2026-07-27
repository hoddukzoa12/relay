"""Deterministic end-to-end purchase flow for the buyer agent.

quote (A2A) -> autonomous sign (payments) -> settle (A2A). Returns a trace so
callers can show every step, including the on-chain explorer link.
"""
from __future__ import annotations

from typing import Any

from ..common import llm
from ..common.contracts import (
    IntentMandate,
    StructuredShippingAddress,
    format_shipping_address,
)
from . import tools


def buy(
    query: str,
    budget: float,
    ship_to: str,
    *,
    intent_mandate: IntentMandate | None = None,
    identity_wallet: str | None = None,
    identity_email: str | None = None,
    human_customer: bool = False,
    shipping_address: StructuredShippingAddress | None = None,
) -> dict[str, Any]:
    if shipping_address:
        ship_to = format_shipping_address(shipping_address)
    # Step 1–3: request a quote / agent-native payment request.
    quote = tools.request_quote(
        query,
        budget,
        ship_to,
        intent_mandate,
        delegator=identity_wallet,
        shipping_address=shipping_address,
    )

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
        delegator=identity_wallet,
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
        human_customer=human_customer,
        customer_email=identity_email,
    )

    return {
        "ok": confirmation.get("status") == "paid",
        "intent": {
            "query": query,
            "budget": budget,
            "shipTo": ship_to,
            **(
                {
                    "shippingAddress": shipping_address.model_dump(
                        exclude_none=True
                    )
                }
                if shipping_address
                else {}
            ),
            **({"delegatedBy": identity_wallet} if identity_wallet else {}),
        },
        "quote": quote,
        "payment": payment,
        "confirmation": confirmation,
    }


def buy_from_text(
    text: str,
    *,
    identity_wallet: str | None = None,
    identity_email: str | None = None,
    human_customer: bool = False,
    shipping_address: StructuredShippingAddress | None = None,
) -> dict[str, Any]:
    """Parse a natural-language instruction, then run the purchase."""
    intent = llm.parse_purchase(text)
    return buy(
        intent["query"],
        intent["budget"],
        intent["shipTo"],
        identity_wallet=identity_wallet,
        identity_email=identity_email,
        human_customer=human_customer,
        shipping_address=shipping_address,
    )
