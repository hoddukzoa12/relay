"""Buyer-agent tools — shared by the ADK agent and the deterministic flow."""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import logging
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from uuid import uuid4

from ..common import service_clients
from ..common.config import settings
from ..common.contracts import (
    CART_MANDATE_DATA_KEY,
    INTENT_MANDATE_DATA_KEY,
    PAYMENT_MANDATE_DATA_KEY,
    CatalogProductsResponse,
    CartMandate,
    DelegationApprovalRequired,
    IntentMandate,
)

_LOG = logging.getLogger(__name__)
_PAYMENT_REQUEST_DATA_KEY = "relay.payment.PaymentRequest"
_ORDER_CONFIRMATION_DATA_KEY = "relay.payment.OrderConfirmation"
_SETTLEMENT_REQUEST_DATA_KEY = "relay.payment.SettlementRequest"
_MERCHANT_NAME = "Relay Shopping Broker"


class DelegationApprovalRequiredError(PermissionError):
    """Authenticated-user refusal that carries an executable approval response."""

    def __init__(self, response: dict[str, Any]) -> None:
        self.response = response
        super().__init__(str(response["reason"]))


@dataclass(frozen=True)
class StorefrontContext:
    """Server-owned identity for one human-present storefront request."""

    identity_wallet: str | None
    approval_tx_signature: str | None


_STOREFRONT_CONTEXT: ContextVar[StorefrontContext | None] = ContextVar(
    "relay_storefront_context", default=None
)


@contextmanager
def storefront_context(
    identity_wallet: str | None,
    approval_tx_signature: str | None,
):
    """Mark one call chain as storefront-originated without global state."""
    token = _STOREFRONT_CONTEXT.set(
        StorefrontContext(identity_wallet, approval_tx_signature)
    )
    try:
        yield
    finally:
        _STOREFRONT_CONTEXT.reset(token)


def _approval_url(required_amount: Decimal) -> str:
    """Add a stable approval intent without discarding preview/query parameters."""
    base = settings.delegation_approval_url
    parsed = urlsplit(base)
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    query.update(
        {
            "relayAction": "approve",
            "relayAmount": format(required_amount, "f"),
        }
    )
    return urlunsplit(
        (
            parsed.scheme,
            parsed.netloc,
            parsed.path or "/",
            urlencode(query),
            parsed.fragment,
        )
    )


def delegation_approval_response(
    delegator: str,
    required_amount: str | float | Decimal,
    *,
    reason: str,
    status: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the canonical retry response from current on-chain state."""
    required = Decimal(str(required_amount))
    current = status or service_clients.payments_delegation_status(delegator)
    if current.get("delegator") != delegator:
        raise PermissionError("Delegation identity did not match the verified wallet.")
    return DelegationApprovalRequired(
        reason=reason,
        delegator=delegator,
        requiredAmount={
            "amount": format(required, "f"),
            "currency": "USDC",
        },
        allowanceRemaining=current["allowanceRemaining"],
        balance=current["balance"],
        approvalUrl=_approval_url(required),
    ).model_dump()


def require_live_delegation(
    delegator: str,
    required_amount: str | float | Decimal,
) -> dict[str, Any]:
    """Return live allowance or raise a fail-closed, actionable refusal."""
    try:
        required = Decimal(str(required_amount))
    except InvalidOperation as exc:
        raise ValueError("required delegation amount must be decimal") from exc
    if required <= 0:
        raise ValueError("required delegation amount must be positive")

    status = service_clients.payments_delegation_status(delegator)
    if status.get("delegator") != delegator:
        raise PermissionError("Delegation identity did not match the verified wallet.")

    allowance = Decimal(str(status["allowanceRemaining"]["amount"]))
    balance = Decimal(str(status["balance"]["amount"]))
    if status.get("active") and allowance >= required and balance >= required:
        return status

    if not status.get("active"):
        reason = "SPL delegation is missing or revoked; approve the broker once."
    elif allowance < required:
        reason = (
            f"SPL delegated allowance {allowance} USDC is below the required "
            f"{required} USDC; approve a higher limit."
        )
    else:
        reason = (
            f"The authenticated wallet has {balance} USDC but this purchase "
            f"requires {required} USDC; fund it before approval."
        )

    response = delegation_approval_response(
        delegator,
        required,
        reason=reason,
        status=status,
    )
    raise DelegationApprovalRequiredError(response)


def _storefront_intent(
    query: str, budget: float, ship_to: str
) -> dict[str, Any] | None:
    context = _STOREFRONT_CONTEXT.get()
    if context is None:
        return None
    if not context.identity_wallet:
        raise PermissionError(
            "Clerk wallet sign-in is required before request_quote or payment."
        )
    if not context.approval_tx_signature:
        raise PermissionError(
            "Approve a USDC spending limit once before requesting a payment quote."
        )

    status = service_clients.payments_verify_delegation(
        context.identity_wallet, context.approval_tx_signature
    )
    if status.get("delegator") != context.identity_wallet:
        raise PermissionError("Delegation identity did not match the Clerk wallet.")
    if not status.get("active"):
        raise PermissionError(
            "SPL delegation is missing or revoked; approve the broker again."
        )
    allowance = Decimal(str(status["allowanceRemaining"]["amount"]))
    if Decimal(str(budget)) > allowance:
        raise PermissionError(
            f"Requested budget {budget:.2f} USDC exceeds the on-chain delegated "
            f"allowance {allowance} USDC; approve a higher limit."
        )

    return {
        "user_cart_confirmation_required": False,
        "natural_language_description": query,
        "requires_refundability": False,
        "price_ceiling": {"amount": f"{budget:.2f}", "currency": "USDC"},
        "ship_to": ship_to,
        "intent_expiry": (
            datetime.now(timezone.utc) + timedelta(minutes=15)
        ).isoformat(),
        "delegator": context.identity_wallet,
        "delegateAuthority": status["delegateAuthority"],
        "allowanceRemaining": status["allowanceRemaining"],
        "approvalTxSignature": context.approval_tx_signature,
    }


def search_catalog(
    query: str,
    budget: float,
    limit: int = 3,
) -> dict[str, Any]:
    """Search real, in-stock Shopify variants within a USDC budget.

    The returned ``price`` includes the configured broker markup, so the model
    compares the same safe ceiling that the shopping broker will enforce when
    it creates a quote. No product or inventory value is model-generated.
    """
    if budget <= 0:
        raise ValueError("budget must be greater than zero")
    if limit < 1 or limit > 12:
        raise ValueError("limit must be between 1 and 12")

    try:
        catalog = CatalogProductsResponse(
            **service_clients.commerce_products(query, limit=50)
        )
    except Exception as exc:  # noqa: BLE001
        _LOG.info("[chat] catalog query failed; retrying full catalog: %s", exc)
        catalog = CatalogProductsResponse(
            **service_clients.commerce_products("", limit=50)
        )

    ceiling = Decimal(str(budget))
    multiplier = Decimal("1") + Decimal(str(settings.markup_pct)) / Decimal("100")
    within_budget: list[dict[str, Any]] = []
    over_budget: list[dict[str, Any]] = []
    for item in catalog.products:
        product = item.model_dump()
        try:
            cost = Decimal(product["price"])
            inventory = int(product["inventoryQuantity"])
        except (KeyError, TypeError, ValueError, InvalidOperation):
            continue
        if inventory <= 0 or cost <= 0:
            continue
        price = (cost * multiplier).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
        candidate = {
            **product,
            "description": str(product.get("description", ""))[:600],
            "catalogPrice": format(cost, "f"),
            "price": format(price, ".2f"),
            "currency": "USDC",
        }
        if price <= ceiling:
            within_budget.append(candidate)
        else:
            over_budget.append(candidate)

    # The commerce endpoint already relevance-ranks results. Preserve that
    # ordering for in-budget matches and show the nearest over-budget items
    # price-first when no safe candidate exists.
    over_budget.sort(key=lambda product: Decimal(product["price"]))
    return {
        "query": query,
        "budget": format(ceiling, "f"),
        "currency": "USDC",
        "products": within_budget[:limit],
        "closestOverBudget": over_budget[: min(limit, 3)],
    }


def get_order_status(identifier: str) -> dict[str, Any]:
    """Read a Relay order by orderRef or Shopify name through the broker API."""
    return service_clients.shopping_order(identifier)


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
    *,
    delegator: str | None = None,
) -> dict[str, Any]:
    """Ask the shopping agent (A2A) for a product + agent-native payment request.

    Returns the PaymentRequest dict.
    """
    if delegated_intent is None:
        unsigned_intent = _storefront_intent(query, budget, ship_to)
        if unsigned_intent is None:
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
            if delegator:
                # Quoting is non-spending, so do not reject a usable item merely
                # because the caller's budget ceiling exceeds its allowance.
                # The exact quoted amount is enforced immediately before /pay.
                status = service_clients.payments_delegation_status(delegator)
                if status.get("delegator") != delegator:
                    raise PermissionError(
                        "Delegation identity did not match the verified wallet."
                    )
                unsigned_intent.update(
                    {
                        "delegator": delegator,
                        "delegateAuthority": status["delegateAuthority"],
                        "allowanceRemaining": status["allowanceRemaining"],
                    }
                )
        intent_mandate = _signed_mandate(unsigned_intent, "buyer")
    else:
        # This signature was produced once by the authenticated human wallet.
        # The verified caller wallet is threaded separately to the transfer.
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
    *,
    delegator: str | None = None,
) -> dict[str, Any]:
    """Autonomously sign & send the USDC payment — NO human approval (PRD §7 DoD).

    Returns {"txSignature", "explorer"}.
    """
    if not mandates:
        if delegator:
            require_live_delegation(delegator, amount)
        return service_clients.payments_pay(
            pay_to,
            amount,
            reference,
            delegator=delegator,
        )

    intent = mandates[INTENT_MANDATE_DATA_KEY]
    cart = mandates[CART_MANDATE_DATA_KEY]
    mandate_delegator = intent.get("delegator")
    if delegator and mandate_delegator and delegator != mandate_delegator:
        raise PermissionError(
            "AP2 delegator does not match the server-verified payer wallet."
        )
    effective_delegator = delegator or mandate_delegator
    if delegator:
        require_live_delegation(delegator, amount)
    wallets = service_clients.payments_wallets()
    payer_wallet = effective_delegator or wallets["buyer"]
    unsigned_payment = {
        "payment_mandate_contents": {
            "payment_mandate_id": f"pm_{uuid4()}",
            "payment_details_id": cart["contents"]["id"],
            "payment_method_token": f"solana:{payer_wallet}",
            "amount": {"amount": amount, "currency": "USDC"},
            "merchant_name": cart["contents"].get("merchant_name", _MERCHANT_NAME),
            "payer": {
                "wallet_address": payer_wallet,
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
    payment = service_clients.payments_pay(
        pay_to,
        amount,
        reference,
        delegator=effective_delegator,
    )
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
