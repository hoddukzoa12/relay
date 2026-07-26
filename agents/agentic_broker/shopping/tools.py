"""Shopping-agent tools — the building blocks used by BOTH the ADK agent
(LLM-driven) and the deterministic broker orchestrator (server-driven).

All functions take/return primitives or JSON-serializable dicts so they work
directly as ADK function tools.
"""
from __future__ import annotations

from typing import Any

from ..common import llm, service_clients
from ..common.config import settings


def _usd(x: float) -> str:
    return f"{x:.2f}"


def source_and_price(query: str, budget_amount: float) -> dict[str, Any]:
    """Source a product for `query` and set a resale price.

    Uses Gemini to pick a concrete product + wholesale cost, then applies the
    broker markup. The price is capped at the shopper's budget.

    Returns: {"title", "cost", "price", "overBudget"}.
    """
    offer = llm.source_offer(query, budget_amount)
    cost = float(offer["cost"])
    price = round(cost * (1 + settings.markup_pct / 100.0), 2)
    over_budget = cost > budget_amount
    if price > budget_amount and not over_budget:
        price = round(budget_amount, 2)  # thin the margin rather than lose the sale
    return {"title": offer["title"], "cost": cost, "price": price, "overBudget": over_budget}


def issue_payment_request(
    product_id: str, title: str, price: float, order_ref: str
) -> dict[str, Any]:
    """Issue an agent-native Solana Pay payment request via the payments service.

    Returns the PaymentRequest dict (PRD §6).
    """
    return service_clients.payments_create_request(product_id, title, _usd(price), order_ref)


def verify_payment(reference: str) -> dict[str, Any]:
    """Verify the on-chain USDC payment for `reference`.

    Returns {"status", "txSignature", "explorer", "amount", "reason"} —
    status is one of pending | paid | expired | invalid, and reason is a
    machine-readable failure reason or None.
    """
    return service_clients.payments_verify(reference)


def record_order(
    order_ref: str,
    product_id: str,
    title: str,
    amount: str,
    buyer_address: str,
    ship_to: str,
    tx_signature: str,
    explorer: str,
) -> dict[str, Any]:
    """Record the paid order in Shopify (orderCreate + orderMarkAsPaid).

    Returns {"shopifyOrderId", "name", "mocked"}.
    """
    return service_clients.commerce_create_order(
        {
            "orderRef": order_ref,
            "productId": product_id,
            "title": title,
            "amount": amount,
            "buyerAddress": buyer_address,
            "shipTo": ship_to,
            "txSignature": tx_signature,
            "explorer": explorer,
        }
    )
