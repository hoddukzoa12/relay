"""Deterministic settlement orchestration for the shopping agent.

The LLM handles sourcing/pricing (inside tools.source_and_price); the money path
— issue request, verify on-chain, record order — runs in a fixed sequence so the
demo is reproducible. This is the "정산 시퀀스는 결정론적" principle.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from threading import Lock
from typing import Literal
from uuid import uuid4

from ..common import service_clients
from ..common.contracts import (
    OrderConfirmation,
    PaymentRequest,
    PurchaseIntent,
    SettlementRequest,
    StructuredShippingAddress,
    SupplierCostSnapshot,
    format_shipping_address,
)
from . import tools


class OrderRecordingPendingError(RuntimeError):
    """Payment is final but the recoverable Shopify ledger write is incomplete."""


@dataclass
class _OrderState:
    product_id: str
    sku: str
    title: str
    amount: str
    supplier_cost: SupplierCostSnapshot
    reference: str
    ship_to: str
    shipping_address: StructuredShippingAddress | None = None
    buyer_wallet: str = ""
    human_customer: bool = False
    customer_email: str | None = None
    payment_status: Literal["pending", "paid"] = "pending"
    paid_tx_signature: str | None = None
    explorer: str | None = None
    ledger_status: Literal["pending", "recording", "settled"] = "pending"
    ledger_error: str | None = None
    confirmation: OrderConfirmation | None = None
    lock: Lock = field(default_factory=Lock)


# In-memory order book: orderRef -> payment + Shopify ledger-write state.
# Process-local (see README security notes); production needs Firestore/Redis.
_orders: dict[str, _OrderState] = {}
_orders_lock = Lock()


def _next_order_ref() -> str:
    return f"ord_{uuid4().hex}"


def handle_quote(intent: PurchaseIntent) -> PaymentRequest:
    """Steps 2–3: source + price, then issue the payment request."""
    budget = float(intent.budget.amount)
    offer = tools.source_and_price(intent.query, budget)
    if offer["overBudget"]:
        raise ValueError(
            f"cannot source '{intent.query}' within budget {budget} USDC "
            f"(wholesale cost {offer['cost']})"
        )

    order_ref = _next_order_ref()
    product_id = offer["variantId"]
    pr = tools.issue_payment_request(
        product_id, offer["title"], offer["price"], order_ref
    )

    order = _OrderState(
        product_id=product_id,
        sku=offer["sku"],
        title=offer["title"],
        amount=pr["price"]["amount"],
        supplier_cost=SupplierCostSnapshot(**offer["supplierCost"]),
        reference=pr["reference"],
        ship_to=(
            format_shipping_address(intent.shippingAddress)
            if intent.shippingAddress
            else intent.shipTo
        ),
        shipping_address=intent.shippingAddress,
    )
    with _orders_lock:
        _orders[order_ref] = order
    return PaymentRequest(**pr)


def catalog_identity(order_ref: str) -> dict[str, str]:
    """Return the real Shopify identity bound to an issued quote."""
    with _orders_lock:
        order = _orders.get(order_ref)
    if not order:
        raise ValueError(f"unknown quote {order_ref}")
    return {"sku": order.sku, "variantId": order.product_id}


def handle_settle(
    req: SettlementRequest,
    *,
    identity_wallet: str | None = None,
    human_customer: bool = False,
    customer_email: str | None = None,
) -> OrderConfirmation:
    """Steps 7–10: verify on-chain, then record the paid Shopify order."""
    with _orders_lock:
        order = _orders.get(req.orderRef)
    if not order:
        return OrderConfirmation(orderRef=req.orderRef, status="invalid")

    if req.reference != order.reference:
        return OrderConfirmation(orderRef=req.orderRef, status="invalid")

    # Bind ownership on the first settlement attempt so a retry under another
    # principal cannot change Shopify attribution after payment moves. This is
    # metadata only; payment verification and the payer wallet are unchanged.
    buyer_wallet = identity_wallet
    if not buyer_wallet:
        try:
            buyer_wallet = service_clients.payments_wallets().get("buyer", "")
        except Exception:  # noqa: BLE001
            buyer_wallet = ""

    # Fast idempotent replay path: a completed settlement returns exactly the
    # original confirmation without re-verifying or touching Shopify.
    with order.lock:
        if (
            order.buyer_wallet
            and buyer_wallet
            and order.buyer_wallet != buyer_wallet
        ):
            return OrderConfirmation(orderRef=req.orderRef, status="invalid")
        if not order.buyer_wallet:
            order.buyer_wallet = buyer_wallet
        if (
            order.human_customer
            and human_customer
            and order.customer_email
            and customer_email
            and order.customer_email != customer_email
        ):
            return OrderConfirmation(orderRef=req.orderRef, status="invalid")
        if human_customer:
            order.human_customer = True
            if customer_email:
                order.customer_email = customer_email
        if order.confirmation:
            return order.confirmation

        payment_already_recorded = order.payment_status == "paid"
        if payment_already_recorded:
            if req.txSignature != order.paid_tx_signature:
                return OrderConfirmation(orderRef=req.orderRef, status="invalid")
            if order.ledger_status == "recording":
                return OrderConfirmation(
                    orderRef=req.orderRef,
                    status="pending",
                    txSignature=order.paid_tx_signature,
                    explorer=order.explorer,
                )
            # Mark the retry in-flight before the external commerce call.
            order.ledger_status = "recording"

    if not payment_already_recorded:
        verification = tools.verify_payment(req.reference)
        if verification["status"] != "paid":
            return OrderConfirmation(
                orderRef=req.orderRef,
                status=verification["status"],
                txSignature=verification.get("txSignature"),
                explorer=verification.get("explorer"),
            )
        if (
            not verification.get("txSignature")
            or req.txSignature != verification["txSignature"]
        ):
            return OrderConfirmation(orderRef=req.orderRef, status="invalid")

        with order.lock:
            # Another settle may have completed while this caller verified.
            if order.confirmation:
                return order.confirmation
            if order.payment_status == "paid":
                if req.txSignature != order.paid_tx_signature:
                    return OrderConfirmation(orderRef=req.orderRef, status="invalid")
                if order.ledger_status == "recording":
                    return OrderConfirmation(
                        orderRef=req.orderRef,
                        status="pending",
                        txSignature=order.paid_tx_signature,
                        explorer=order.explorer,
                    )
            else:
                # Persist paid-before-ledger-write so a Shopify failure cannot
                # erase the fact that funds already moved on-chain.
                order.payment_status = "paid"
                order.paid_tx_signature = verification["txSignature"]
                order.explorer = verification["explorer"]
                order.ledger_status = "pending"
                order.ledger_error = None
            order.ledger_status = "recording"

    try:
        result = tools.record_order(
            order_ref=req.orderRef,
            product_id=order.product_id,
            sku=order.sku,
            title=order.title,
            amount=order.amount,
            buyer_address=order.buyer_wallet,
            ship_to=order.ship_to,
            payment_reference=order.reference,
            tx_signature=order.paid_tx_signature or req.txSignature,
            explorer=order.explorer or "",
            supplier_cost=order.supplier_cost.model_dump(),
            shipping_address=(
                order.shipping_address.model_dump(exclude_none=True)
                if order.shipping_address
                else None
            ),
            human_customer=order.human_customer,
            customer_email=order.customer_email,
        )
    except Exception as exc:  # noqa: BLE001
        with order.lock:
            order.ledger_status = "pending"
            order.ledger_error = str(exc)
        raise OrderRecordingPendingError(
            f"Payment {order.paid_tx_signature} is paid on-chain; "
            f"Shopify ledger recording for {req.orderRef} remains pending "
            f"and is safe to retry: {exc}"
        ) from exc

    supplier_order = (
        {"supplierOrder": result["supplierOrder"]}
        if result.get("supplierOrder")
        else {}
    )
    customer_association = (
        {"customerAssociation": result["customerAssociation"]}
        if result.get("customerAssociation")
        else {}
    )
    confirmation = OrderConfirmation(
        orderRef=req.orderRef,
        status="paid",
        txSignature=order.paid_tx_signature,
        explorer=order.explorer,
        shopifyOrderId=result.get("shopifyOrderId"),
        **supplier_order,
        **customer_association,
    )
    with order.lock:
        order.ledger_status = "settled"
        order.ledger_error = None
        order.confirmation = confirmation
    return confirmation
