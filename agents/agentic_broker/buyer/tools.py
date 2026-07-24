"""Buyer-agent tools — shared by the ADK agent and the deterministic flow."""
from __future__ import annotations

from typing import Any

from ..common import service_clients


def request_quote(query: str, budget: float, ship_to: str) -> dict[str, Any]:
    """Ask the shopping agent (A2A) for a product + agent-native payment request.

    Returns the PaymentRequest dict.
    """
    intent = {
        "query": query,
        "budget": {"amount": f"{budget:.2f}", "currency": "USDC"},
        "shipTo": ship_to,
    }
    return service_clients.a2a_quote(intent)


def authorize_payment(pay_to: str, amount: str, reference: str) -> dict[str, Any]:
    """Autonomously sign & send the USDC payment — NO human approval (PRD §7 DoD).

    Returns {"txSignature", "explorer"}.
    """
    return service_clients.payments_pay(pay_to, amount, reference)


def confirm_settlement(order_ref: str, reference: str, tx_signature: str) -> dict[str, Any]:
    """Hand the payment proof to the shopping agent (A2A) and get the order back.

    Returns the OrderConfirmation dict.
    """
    return service_clients.a2a_settle(
        {"orderRef": order_ref, "reference": reference, "txSignature": tx_signature}
    )
