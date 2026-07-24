"""Deterministic settlement orchestration for the shopping agent.

The LLM handles sourcing/pricing (inside tools.source_and_price); the money path
— issue request, verify on-chain, record order — runs in a fixed sequence so the
demo is reproducible. This is the "정산 시퀀스는 결정론적" principle.
"""
from __future__ import annotations

from datetime import datetime, timezone

from ..common import service_clients
from ..common.contracts import (
    OrderConfirmation,
    PaymentRequest,
    PurchaseIntent,
    SettlementRequest,
)
from . import tools

# In-memory order book: orderRef -> context needed at settle time.
# Process-local (see note in payments/store.ts); fine for the demo.
_orders: dict[str, dict[str, str]] = {}
_counter = 0


def _next_order_ref() -> str:
    global _counter
    _counter += 1
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d")
    return f"ord_{stamp}_{_counter:04d}"


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
    product_id = f"sku_{abs(hash(offer['title'])) % 10000}"
    pr = tools.issue_payment_request(product_id, offer["title"], offer["price"], order_ref)

    _orders[order_ref] = {
        "productId": product_id,
        "title": offer["title"],
        "amount": pr["price"]["amount"],
        "reference": pr["reference"],
        "shipTo": intent.shipTo,
    }
    return PaymentRequest(**pr)


def handle_settle(req: SettlementRequest) -> OrderConfirmation:
    """Steps 7–10: verify on-chain, then record the paid Shopify order."""
    order = _orders.get(req.orderRef)
    if not order:
        return OrderConfirmation(orderRef=req.orderRef, status="invalid")

    verification = tools.verify_payment(req.reference)
    if verification["status"] != "paid":
        return OrderConfirmation(
            orderRef=req.orderRef,
            status=verification["status"],
            txSignature=verification.get("txSignature"),
            explorer=verification.get("explorer"),
        )

    # Single-buyer demo: learn the buyer wallet from the payments service for the
    # order record. In a multi-buyer system this would come off the verified tx.
    try:
        buyer_wallet = service_clients.payments_wallets().get("buyer", "")
    except Exception:  # noqa: BLE001
        buyer_wallet = ""

    result = tools.record_order(
        order_ref=req.orderRef,
        product_id=order["productId"],
        title=order["title"],
        amount=order["amount"],
        buyer_address=buyer_wallet,
        ship_to=order["shipTo"],
        tx_signature=verification["txSignature"],
        explorer=verification["explorer"],
    )

    return OrderConfirmation(
        orderRef=req.orderRef,
        status="paid",
        txSignature=verification["txSignature"],
        explorer=verification["explorer"],
        shopifyOrderId=result.get("shopifyOrderId"),
    )
