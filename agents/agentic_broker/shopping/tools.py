"""Shopping-agent tools — the building blocks used by BOTH the ADK agent
(LLM-driven) and the deterministic broker orchestrator (server-driven).

All functions take/return primitives or JSON-serializable dicts so they work
directly as ADK function tools.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any

from ..common import llm, service_clients
from ..common.config import settings
from ..common.contracts import (
    CatalogProductsResponse,
    FulfillmentResult,
    OrderStatus,
    RefundResult,
    TrackingInfo,
)


def _usd(x: float) -> str:
    return f"{x:.2f}"


def source_and_price(query: str, budget_amount: float) -> dict[str, Any]:
    """Source a product for `query` and set a resale price.

    Retrieves real Shopify variants, removes unavailable/over-budget choices,
    and uses Gemini (or deterministic relevance) only to rank that safe set.

    Returns the selected real variant, SKU, cost, and marked-up sale price.
    """
    try:
        catalog = CatalogProductsResponse(
            **service_clients.commerce_products(query, limit=50)
        )
    except Exception as exc:  # noqa: BLE001
        print(f"[sourcing] catalog query failed; retrying full catalog ({exc})")
        catalog = CatalogProductsResponse(
            **service_clients.commerce_products("", limit=50)
        )

    budget = Decimal(str(budget_amount))
    multiplier = Decimal("1") + Decimal(str(settings.markup_pct)) / Decimal("100")
    candidates: list[dict[str, Any]] = []
    for catalog_product in catalog.products:
        product = catalog_product.model_dump()
        try:
            inventory = int(product["inventoryQuantity"])
            cost = Decimal(str(product["price"]))
        except (KeyError, TypeError, ValueError, InvalidOperation):
            continue
        if (
            inventory <= 0
            or cost <= 0
            or not product.get("variantId")
            or not product.get("sku")
        ):
            continue
        sale_price = (cost * multiplier).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
        if sale_price > budget:
            continue
        candidates.append(
            {
                **product,
                "price": float(cost),
                "salePrice": float(sale_price),
            }
        )

    if not candidates:
        raise ValueError(
            f"no in-stock catalog product is available within "
            f"{budget_amount:.2f} USDC"
        )

    offer = llm.source_offer(query, budget_amount, candidates)
    return {
        "productId": offer["productId"],
        "variantId": offer["variantId"],
        "sku": offer["sku"],
        "title": offer["title"],
        "cost": offer["price"],
        "price": offer["salePrice"],
        "inventoryQuantity": offer["inventoryQuantity"],
        "overBudget": False,
    }


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
    sku: str,
    title: str,
    amount: str,
    buyer_address: str,
    ship_to: str,
    payment_reference: str,
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
            "variantId": product_id,
            "sku": sku,
            "title": title,
            "amount": amount,
            "buyerAddress": buyer_address,
            "shipTo": ship_to,
            "paymentReference": payment_reference,
            "txSignature": tx_signature,
            "explorer": explorer,
        }
    )


def get_order_status(identifier: str) -> dict[str, Any]:
    """Get one Relay order by orderRef or Shopify name (for example ``#1006``).

    Returns financial and fulfillment status, real catalog SKUs, paid amount,
    both on-chain proof slots, refund state, and any demo tracking metadata.
    This primitive is intentionally reusable as MCP ``get_order_status``.
    """
    return OrderStatus(**service_clients.commerce_order(identifier)).model_dump()


def refund_order(identifier: str) -> dict[str, Any]:
    """Return the full paid USDC amount merchant→buyer and update Shopify.

    The payments service verifies the original transfer and owns the refund
    compare-and-set. If the Shopify write fails after USDC moves, retrying this
    function reuses the original refund signature instead of transferring again.
    """
    order = OrderStatus(**service_clients.commerce_order(identifier))
    refund_repair = order.refund.status == "refunded"
    if refund_repair and order.refund.reference:
        if not order.refund.txSignature or not order.refund.explorer:
            raise RuntimeError(
                f"Order {order.orderRef} has incomplete refund proof"
            )
        return RefundResult(
            shopifyOrderId=order.shopifyOrderId,
            orderRef=order.orderRef,
            name=order.name,
            status="refunded",
            financialStatus="REFUNDED",
            amount=order.amount,
            payment=order.payment,
            refund={
                "reference": order.refund.reference,
                "txSignature": order.refund.txSignature,
                "explorer": order.refund.explorer,
            },
            replayed=True,
        ).model_dump()
    if not refund_repair and order.financialStatus != "PAID":
        raise ValueError(
            f"Order {order.orderRef} cannot be refunded from "
            f"financialStatus={order.financialStatus}"
        )
    if not order.payment.reference:
        raise ValueError(
            f"Order {order.orderRef} predates recorded payment references and "
            "cannot be autonomously refunded"
        )

    transfer = service_clients.payments_refund(
        order.orderRef, order.payment.reference
    )
    if transfer.get("status") != "refunded":
        raise RuntimeError(
            f"Refund {transfer.get('refundTxSignature', 'unknown')} for "
            f"{order.orderRef} is pending on-chain; retry safely"
        )
    if (
        refund_repair
        and order.refund.txSignature
        and order.refund.txSignature != transfer["refundTxSignature"]
    ):
        raise RuntimeError(
            f"Shopify refund proof for {order.orderRef} does not match the "
            "payments-service refund"
        )

    updated = OrderStatus(
        **service_clients.commerce_refund_order(
            order.orderRef,
            transfer["refundReference"],
            transfer["refundTxSignature"],
            transfer["refundExplorer"],
        )
    )
    if (
        updated.financialStatus != "REFUNDED"
        or updated.refund.status != "refunded"
        or not updated.refund.reference
        or not updated.refund.txSignature
        or not updated.refund.explorer
    ):
        raise RuntimeError(
            f"Shopify order {order.orderRef} did not reach a fully refunded state"
        )
    return RefundResult(
        shopifyOrderId=updated.shopifyOrderId,
        orderRef=updated.orderRef,
        name=updated.name,
        status="refunded",
        financialStatus="REFUNDED",
        amount=updated.amount,
        payment=updated.payment,
        refund={
            "reference": updated.refund.reference,
            "txSignature": updated.refund.txSignature,
            "explorer": updated.refund.explorer,
        },
        replayed=bool(transfer.get("replayed", False)),
    ).model_dump()


def fulfill_order(order_ref: str) -> dict[str, Any]:
    """Mark a paid Shopify order FULFILLED with an explicit demo waybill."""
    return FulfillmentResult(
        **service_clients.commerce_fulfill_order(order_ref)
    ).model_dump()


def track_order(identifier: str) -> dict[str, Any]:
    """Look up an order's demo waybill via the EasyPost provider interface."""
    return TrackingInfo(
        **service_clients.commerce_track_order(identifier)
    ).model_dump()
