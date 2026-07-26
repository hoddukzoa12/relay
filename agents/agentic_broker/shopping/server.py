"""Shopping (broker) agent — A2A HTTP endpoints the buyer agent calls."""
from __future__ import annotations

from datetime import datetime, timezone
import logging
from typing import Any
from uuid import uuid4

from fastapi import FastAPI, HTTPException

from ..common import service_clients
from ..common.agent_cards import shopping_agent_card
from ..common.config import settings
from ..common.contracts import (
    CART_MANDATE_DATA_KEY,
    INTENT_MANDATE_DATA_KEY,
    PAYMENT_MANDATE_DATA_KEY,
    CartMandate,
    IntentMandate,
    OrderConfirmation,
    PaymentMandate,
    PaymentRequest,
    PurchaseIntent,
    SettlementRequest,
)
from . import broker

app = FastAPI(title="Shopping Broker Agent", version="0.1.0")
_LOG = logging.getLogger(__name__)
_PAYMENT_REQUEST_DATA_KEY = "relay.payment.PaymentRequest"
_ORDER_CONFIRMATION_DATA_KEY = "relay.payment.OrderConfirmation"
_PURCHASE_INTENT_DATA_KEY = "relay.payment.PurchaseIntent"
_SETTLEMENT_REQUEST_DATA_KEY = "relay.payment.SettlementRequest"
_MERCHANT_NAME = "Relay Shopping Broker"

# A2A order authorization context. The existing broker/payment stores remain the
# source of truth for settlement; this only checks mandate-to-mandate binding.
_a2a_carts: dict[str, dict[str, Any]] = {}


@app.get("/health")
def health() -> dict[str, object]:
    return {"ok": True, "agent": "shopping"}


@app.get("/.well-known/agent-card.json")
@app.get("/a2a/agent-card")
def agent_card() -> dict[str, object]:
    return shopping_agent_card()


def _message_data(message: dict[str, Any]) -> dict[str, Any]:
    data: dict[str, Any] = {}
    for part in message.get("parts", []):
        if part.get("kind") == "data" and isinstance(part.get("data"), dict):
            data.update(part["data"])
    return data


def _task(
    message: dict[str, Any],
    *,
    state: str,
    data: dict[str, Any] | None = None,
    error: str | None = None,
) -> dict[str, Any]:
    context_id = message.get("contextId") or str(uuid4())
    task: dict[str, Any] = {
        "kind": "task",
        "id": message.get("taskId") or str(uuid4()),
        "contextId": context_id,
        "status": {
            "state": state,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        },
        "history": [message],
    }
    if data is not None:
        task["artifacts"] = [
            {
                "artifactId": str(uuid4()),
                "parts": [{"kind": "data", "data": data}],
            }
        ]
    if error:
        task["status"]["message"] = {
            "kind": "message",
            "messageId": str(uuid4()),
            "role": "agent",
            "parts": [{"kind": "text", "text": error}],
        }
    return task


def _parse_expiry(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _handle_intent(message: dict[str, Any], data: dict[str, Any]) -> dict[str, Any]:
    intent = IntentMandate(**data[INTENT_MANDATE_DATA_KEY])
    verification = service_clients.payments_verify_mandate(
        intent.model_dump(exclude_none=True), "buyer"
    )
    if not verification.get("valid"):
        raise ValueError("invalid buyer signature on IntentMandate")
    if _parse_expiry(intent.intent_expiry) <= datetime.now(timezone.utc):
        raise ValueError("IntentMandate has expired")
    _LOG.info(
        "[ap2] IntentMandate verified signer=buyer publicKey=%s",
        verification.get("publicKey"),
    )

    payment_request = broker.handle_quote(
        PurchaseIntent(
            query=intent.natural_language_description,
            budget=intent.price_ceiling,
            shipTo=intent.ship_to,
        )
    )
    unsigned_cart = {
        "contents": {
            "id": payment_request.orderRef,
            "user_cart_confirmation_required": (
                intent.user_cart_confirmation_required
            ),
            "cart_items": [
                {
                    "sku": payment_request.productId,
                    "name": payment_request.title,
                    "price": payment_request.price.model_dump(),
                }
            ],
            "total": payment_request.price.model_dump(),
            "shipping": {"amount": "0.00", "currency": "USDC"},
            "tax": {"amount": "0.00", "currency": "USDC"},
            "refund_period": 30,
            "cart_expiry": payment_request.expiresAt,
            "merchant_name": _MERCHANT_NAME,
            "payment_request": payment_request.model_dump(),
            "intent_mandate_signature": intent.signature,
        }
    }
    signed = service_clients.payments_sign_mandate(unsigned_cart, "merchant")
    cart = CartMandate(
        **{**unsigned_cart, "signature": signed["signature"]}
    )
    _LOG.info(
        "[ap2] CartMandate signed signer=merchant publicKey=%s",
        signed.get("publicKey"),
    )
    _a2a_carts[payment_request.orderRef] = {
        "amount": payment_request.price.model_dump(),
        "cart_mandate_signature": cart.signature,
        "intent_mandate_signature": intent.signature,
    }
    return _task(
        message,
        state="completed",
        data={
            CART_MANDATE_DATA_KEY: cart.model_dump(),
            _PAYMENT_REQUEST_DATA_KEY: payment_request.model_dump(),
        },
    )


def _handle_payment(message: dict[str, Any], data: dict[str, Any]) -> dict[str, Any]:
    payment = PaymentMandate(**data[PAYMENT_MANDATE_DATA_KEY])
    settlement = SettlementRequest(**data[_SETTLEMENT_REQUEST_DATA_KEY])
    verification = service_clients.payments_verify_mandate(
        payment.model_dump(exclude_none=True), "buyer"
    )
    if not verification.get("valid"):
        raise ValueError("invalid buyer signature on PaymentMandate")
    _LOG.info(
        "[ap2] PaymentMandate verified signer=buyer publicKey=%s",
        verification.get("publicKey"),
    )

    expected = _a2a_carts.get(settlement.orderRef)
    contents = payment.payment_mandate_contents
    if not expected:
        raise ValueError("PaymentMandate has no matching A2A CartMandate")
    if contents.payment_details_id != settlement.orderRef:
        raise ValueError("PaymentMandate is bound to a different cart")
    if contents.amount.model_dump() != expected["amount"]:
        raise ValueError("PaymentMandate amount does not match CartMandate")
    if contents.cart_mandate_signature != expected["cart_mandate_signature"]:
        raise ValueError("PaymentMandate is not bound to the accepted CartMandate")
    if contents.intent_mandate_signature != expected["intent_mandate_signature"]:
        raise ValueError("PaymentMandate is not bound to the accepted IntentMandate")

    confirmation = broker.handle_settle(settlement)
    return _task(
        message,
        state="completed",
        data={_ORDER_CONFIRMATION_DATA_KEY: confirmation.model_dump()},
    )


def _handle_legacy_quote(
    message: dict[str, Any], payload: dict[str, Any]
) -> dict[str, Any]:
    """JSON-RPC compatibility for callers that have not adopted AP2 yet."""
    payment_request = broker.handle_quote(PurchaseIntent(**payload))
    return _task(
        message,
        state="completed",
        data={_PAYMENT_REQUEST_DATA_KEY: payment_request.model_dump()},
    )


def _handle_legacy_settle(
    message: dict[str, Any], payload: dict[str, Any]
) -> dict[str, Any]:
    """JSON-RPC compatibility equivalent of the retained REST settle route."""
    confirmation = broker.handle_settle(SettlementRequest(**payload))
    return _task(
        message,
        state="completed",
        data={_ORDER_CONFIRMATION_DATA_KEY: confirmation.model_dump()},
    )


@app.post("/a2a")
def a2a_jsonrpc(request: dict[str, Any]) -> dict[str, Any]:
    """A2A v0.3 JSON-RPC transport; legacy REST routes remain below."""
    request_id = request.get("id")
    if request.get("jsonrpc") != "2.0":
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "error": {"code": -32600, "message": "Invalid JSON-RPC request"},
        }
    if request.get("method") != "message/send":
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "error": {"code": -32601, "message": "Method not found"},
        }

    params = request.get("params")
    message = params.get("message") if isinstance(params, dict) else None
    if not isinstance(message, dict) or message.get("kind") != "message":
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "error": {"code": -32602, "message": "params.message is required"},
        }

    try:
        data = _message_data(message)
        if INTENT_MANDATE_DATA_KEY in data:
            result = _handle_intent(message, data)
        elif (
            PAYMENT_MANDATE_DATA_KEY in data
            and _SETTLEMENT_REQUEST_DATA_KEY in data
        ):
            result = _handle_payment(message, data)
        elif _PURCHASE_INTENT_DATA_KEY in data:
            result = _handle_legacy_quote(
                message, data[_PURCHASE_INTENT_DATA_KEY]
            )
        elif {"query", "budget", "shipTo"}.issubset(data):
            result = _handle_legacy_quote(message, data)
        elif _SETTLEMENT_REQUEST_DATA_KEY in data:
            result = _handle_legacy_settle(
                message, data[_SETTLEMENT_REQUEST_DATA_KEY]
            )
        elif {"orderRef", "reference", "txSignature"}.issubset(data):
            result = _handle_legacy_settle(message, data)
        else:
            result = _task(
                message,
                state="input-required",
                error="Expected an AP2 IntentMandate or PaymentMandate DataPart",
            )
    except Exception as exc:  # noqa: BLE001
        _LOG.warning("[a2a] message/send failed: %s", exc)
        result = _task(message, state="failed", error=str(exc))
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


@app.post("/a2a/quote", response_model=PaymentRequest)
def quote(intent: PurchaseIntent) -> PaymentRequest:
    try:
        return broker.handle_quote(intent)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/a2a/settle", response_model=OrderConfirmation)
def settle(req: SettlementRequest) -> OrderConfirmation:
    try:
        return broker.handle_settle(req)
    except broker.FulfillmentPendingError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


def main() -> None:
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=settings.shopping_port)


if __name__ == "__main__":
    main()
