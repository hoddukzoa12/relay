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
    CatalogProduct,
    CatalogProductsResponse,
    FulfillmentResult,
    OrderStatus,
    RefundResult,
    TrackingInfo,
)
from . import dsers_sourcing


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

    def safe_candidates(
        products: list[CatalogProduct | dict[str, Any]],
    ) -> list[dict[str, Any]]:
        candidates: list[dict[str, Any]] = []
        for catalog_product in products:
            product = (
                catalog_product.model_dump()
                if isinstance(catalog_product, CatalogProduct)
                else catalog_product
            )
            try:
                inventory = int(product["inventoryQuantity"])
                supplier_cost = product["supplierCost"]
                if not supplier_cost:
                    continue
                cost = Decimal(str(supplier_cost["amount"]))
                catalog_price = Decimal(str(product["price"]))
            except (KeyError, TypeError, ValueError, InvalidOperation):
                continue
            if (
                inventory <= 0
                or cost <= 0
                or catalog_price <= 0
                or not product.get("variantId")
                or not product.get("sku")
            ):
                continue
            # Keep the store's reviewed USD catalog price as the resale-price
            # basis. Supplier cost is independent margin evidence and never
            # silently reprices Shopify.
            sale_price = (catalog_price * multiplier).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP
            )
            if sale_price > budget:
                continue
            candidates.append(
                {
                    **product,
                    "price": float(catalog_price),
                    "salePrice": float(sale_price),
                }
            )
        return candidates

    eligible = safe_candidates(catalog.products)
    candidates = [
        product
        for product in eligible
        if llm.catalog_satisfies_query(product, query)
    ]
    partial_candidates = [
        product
        for product in eligible
        if llm.catalog_relevance(product, query) > 0
        and not llm.catalog_satisfies_query(product, query)
    ]
    sourcing: dict[str, Any] | None = None
    if not candidates:
        try:
            sourcing = dsers_sourcing.source_missing_product(
                query, budget_amount
            )
            metadata = sourcing.get("metadata", {})
            sourced_product = metadata.get("product")
            product_id = str(sourcing.get("productId") or "")
            if not isinstance(sourced_product, dict):
                refreshed = CatalogProductsResponse(
                    **service_clients.commerce_products("", limit=50)
                )
                sourced_product = next(
                    (
                        product.model_dump()
                        for product in refreshed.products
                        if product.productId == product_id
                    ),
                    None,
                )
            if isinstance(sourced_product, dict):
                sourced_candidates = safe_candidates([sourced_product])
                candidates = [
                    product
                    for product in sourced_candidates
                    if llm.catalog_satisfies_query(product, query)
                ]
        except dsers_sourcing.DSersSourcingUnavailable as exc:
            partial_detail = (
                " Partial catalog results exist, but they match only part of "
                "the query and were not treated as complete matches: "
                + ", ".join(
                    repr(product["title"]) for product in partial_candidates[:3]
                )
                + "."
                if partial_candidates
                else ""
            )
            raise ValueError(
                f"no suitable in-stock catalog product matches {query!r} "
                f"within {budget_amount:.2f} USDC; new DSers sourcing is "
                f"currently unavailable ({exc}). Existing catalog products "
                f"and autonomous USDC checkout remain available.{partial_detail}"
            ) from exc

    if not candidates:
        detail = (
            "DSers pushed a product, but it did not satisfy every query term "
            "or was not safely readable from Shopify with positive margin"
            if sourcing
            else "no matching local catalog product was available"
        )
        if partial_candidates:
            detail += (
                "; partial catalog fallbacks were not treated as complete "
                "matches: "
                + ", ".join(
                    repr(product["title"]) for product in partial_candidates[:3]
                )
            )
        raise ValueError(
            f"no suitable in-stock catalog product matches {query!r} within "
            f"{budget_amount:.2f} USDC ({detail})"
        )

    offer = llm.source_offer(query, budget_amount, candidates)
    return {
        "productId": offer["productId"],
        "variantId": offer["variantId"],
        "sku": offer["sku"],
        "title": offer["title"],
        "cost": float(Decimal(str(offer["supplierCost"]["amount"]))),
        "catalogPrice": offer["price"],
        "supplierCost": offer["supplierCost"],
        "price": offer["salePrice"],
        "inventoryQuantity": offer["inventoryQuantity"],
        "overBudget": False,
        "matchStatus": "complete",
        **({"externalSourcing": sourcing} if sourcing else {}),
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
    supplier_cost: dict[str, Any] | None = None,
    shipping_address: dict[str, Any] | None = None,
    human_customer: bool = False,
    customer_email: str | None = None,
) -> dict[str, Any]:
    """Record the paid order in Shopify (orderCreate + orderMarkAsPaid).

    Returns Shopify ledger identity plus the explicit supplier-order state.
    """
    payload = {
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
        "supplierCost": supplier_cost,
        "shippingAddress": shipping_address,
    }
    if human_customer:
        # Presence (including null) distinguishes an authenticated human whose
        # Clerk record has no email from agent-only paths with no customer.
        payload["customerEmail"] = customer_email
    return service_clients.commerce_create_order(payload)


def get_order_status(identifier: str) -> dict[str, Any]:
    """Get one Relay order by orderRef or Shopify name (for example ``#1006``).

    Returns financial and fulfillment status, real catalog SKUs, paid amount,
    both on-chain proof slots, refund state, and the explicit supplier state.
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
    """Request fulfillment; currently fails closed while Leg 2 is unconnected."""
    return FulfillmentResult(
        **service_clients.commerce_fulfill_order(order_ref)
    ).model_dump()


def track_order(identifier: str) -> dict[str, Any]:
    """Request tracking; currently fails closed until a real shipment exists."""
    return TrackingInfo(
        **service_clients.commerce_track_order(identifier)
    ).model_dump()
