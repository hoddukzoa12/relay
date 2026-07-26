"""Buyer-agent tools — shared by the ADK agent and the deterministic flow."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import logging
from typing import Any
from uuid import uuid4

from ..common import service_clients
from ..common.contracts import (
    CART_MANDATE_DATA_KEY,
    INTENT_MANDATE_DATA_KEY,
    PAYMENT_MANDATE_DATA_KEY,
    CartMandate,
    IntentMandate,
)

_LOG = logging.getLogger(__name__)
_PAYMENT_REQUEST_DATA_KEY = "relay.payment.PaymentRequest"
_ORDER_CONFIRMATION_DATA_KEY = "relay.payment.OrderConfirmation"
_SETTLEMENT_REQUEST_DATA_KEY = "relay.payment.SettlementRequest"
_MERCHANT_NAME = "Relay Shopping Broker"


def _signed_mandate(unsigned: dict[str, Any], signer: str) -> dict[str, Any]:
    result = service_clients.payments_sign_mandate(unsigned, signer)
    signed = {**unsigned, "signature": result["signature"]}
    _LOG.info(
        "[ap2] mandate signed signer=%s publicKey=%s",
        signer,
        result.get("publicKey"),
    )
    return signed


def _task_data(task: dict[str, Any]) -> dict[str, Any]:
    data: dict[str, Any] = {}
    for artifact in task.get("artifacts", []):
        for part in artifact.get("parts", []):
            if part.get("kind") == "data" and isinstance(part.get("data"), dict):
                data.update(part["data"])
    if task.get("status", {}).get("state") != "completed":
        message = task.get("status", {}).get("message", {})
        raise RuntimeError(message.get("parts", [{}])[0].get("text", "A2A task failed"))
    return data


def request_quote(
    query: str,
    budget: float,
    ship_to: str,
    delegated_intent: IntentMandate | None = None,
) -> dict[str, Any]:
    """Ask the shopping agent (A2A) for a product + agent-native payment request.

    Returns the PaymentRequest dict.
    """
    if delegated_intent is None:
        unsigned_intent = {
            "user_cart_confirmation_required": False,
            "natural_language_description": query,
            "requires_refundability": False,
            "price_ceiling": {"amount": f"{budget:.2f}", "currency": "USDC"},
            "ship_to": ship_to,
            "intent_expiry": (
                datetime.now(timezone.utc) + timedelta(minutes=15)
            ).isoformat(),
        }
        intent_mandate = _signed_mandate(unsigned_intent, "buyer")
    else:
        # This signature was produced once by the authenticated human wallet.
        # PaymentMandate and the Solana transfer still use the agent wallet.
        intent_mandate = delegated_intent.model_dump(exclude_none=True)
        _LOG.info(
            "[ap2] using delegated IntentMandate signer=human publicKey=%s",
            delegated_intent.signer_wallet,
        )
    task = service_clients.a2a_message_send(
        {INTENT_MANDATE_DATA_KEY: intent_mandate}
    )
    data = _task_data(task)
    cart_mandate = CartMandate(**data[CART_MANDATE_DATA_KEY])
    cart = cart_mandate.model_dump(exclude_none=True)
    verification = service_clients.payments_verify_mandate(cart, "merchant")
    if not verification.get("valid"):
        raise ValueError("shopping agent returned an invalid CartMandate signature")
    _LOG.info(
        "[ap2] CartMandate verified signer=merchant publicKey=%s",
        verification.get("publicKey"),
    )

    payment_request = data.get(
        _PAYMENT_REQUEST_DATA_KEY, cart_mandate.contents.payment_request.model_dump()
    )
    return {
        **payment_request,
        "ap2Mandates": {
            INTENT_MANDATE_DATA_KEY: intent_mandate,
            CART_MANDATE_DATA_KEY: cart,
        },
    }


def authorize_payment(
    pay_to: str,
    amount: str,
    reference: str,
    mandates: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Autonomously sign & send the USDC payment — NO human approval (PRD §7 DoD).

    Returns {"txSignature", "explorer"}.
    """
    if not mandates:
        return service_clients.payments_pay(pay_to, amount, reference)

    intent = mandates[INTENT_MANDATE_DATA_KEY]
    cart = mandates[CART_MANDATE_DATA_KEY]
    wallets = service_clients.payments_wallets()
    unsigned_payment = {
        "payment_mandate_contents": {
            "payment_mandate_id": f"pm_{uuid4()}",
            "payment_details_id": cart["contents"]["id"],
            "payment_method_token": f"solana:{wallets['buyer']}",
            "amount": {"amount": amount, "currency": "USDC"},
            "merchant_name": cart["contents"].get("merchant_name", _MERCHANT_NAME),
            "payer": {
                "wallet_address": wallets["buyer"],
                "ship_to": intent.get("ship_to"),
            },
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "cart_mandate_signature": cart["signature"],
            "intent_mandate_signature": intent["signature"],
        }
    }
    payment_mandate = _signed_mandate(unsigned_payment, "buyer")

    # The Solana Pay payload is deliberately unchanged; AP2 authorization wraps
    # the existing autonomous /pay call rather than replacing it.
    payment = service_clients.payments_pay(pay_to, amount, reference)
    return {
        **payment,
        "ap2Mandates": {PAYMENT_MANDATE_DATA_KEY: payment_mandate},
    }


def confirm_settlement(
    order_ref: str,
    reference: str,
    tx_signature: str,
    mandates: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Hand the payment proof to the shopping agent (A2A) and get the order back.

    Returns the OrderConfirmation dict.
    """
    settlement = {
        "orderRef": order_ref,
        "reference": reference,
        "txSignature": tx_signature,
    }
    if not mandates or PAYMENT_MANDATE_DATA_KEY not in mandates:
        return service_clients.a2a_settle(settlement)

    task = service_clients.a2a_message_send(
        {
            PAYMENT_MANDATE_DATA_KEY: mandates[PAYMENT_MANDATE_DATA_KEY],
            _SETTLEMENT_REQUEST_DATA_KEY: settlement,
        }
    )
    return _task_data(task)[_ORDER_CONFIRMATION_DATA_KEY]
