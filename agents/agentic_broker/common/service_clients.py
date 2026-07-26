"""Thin synchronous HTTP clients for the TS services and the peer agent.

Sync (httpx.Client) on purpose: the same functions back both the ADK function
tools and the deterministic orchestrators, and FastAPI runs sync handlers in a
threadpool — so we avoid async plumbing entirely.
"""
from __future__ import annotations

from typing import Any, Optional
from uuid import uuid4

import httpx

from .config import settings

_TIMEOUT = httpx.Timeout(60.0)


def _post(url: str, payload: dict[str, Any]) -> dict[str, Any]:
    resp = httpx.post(url, json=payload, timeout=_TIMEOUT)
    resp.raise_for_status()
    return resp.json()


def _get(
    url: str, params: Optional[dict[str, Any]] = None
) -> dict[str, Any]:
    resp = httpx.get(url, params=params, timeout=_TIMEOUT)
    resp.raise_for_status()
    return resp.json()


# --- payments service --------------------------------------------------------
def payments_create_request(
    product_id: str, title: str, amount: str, order_ref: str, pay_to: Optional[str] = None
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "productId": product_id,
        "title": title,
        "amount": amount,
        "orderRef": order_ref,
    }
    if pay_to:
        body["payTo"] = pay_to
    return _post(f"{settings.payments_url}/payment-requests", body)


def payments_pay(pay_to: str, amount: str, reference: str) -> dict[str, Any]:
    return _post(
        f"{settings.payments_url}/pay",
        {"payTo": pay_to, "amount": amount, "reference": reference},
    )


def payments_verify(reference: str) -> dict[str, Any]:
    return _post(f"{settings.payments_url}/verify", {"reference": reference})


def payments_wallets() -> dict[str, Any]:
    return _get(f"{settings.payments_url}/wallets")


def payments_sign_mandate(
    mandate: dict[str, Any], signer: str
) -> dict[str, Any]:
    """Sign canonical mandate JSON with a configured Solana wallet."""
    return _post(
        f"{settings.payments_url}/sign-mandate",
        {"mandate": mandate, "signer": signer},
    )


def payments_verify_mandate(
    mandate: dict[str, Any], signer: str
) -> dict[str, Any]:
    """Verify a mandate against the configured buyer or merchant wallet."""
    return _post(
        f"{settings.payments_url}/verify-mandate",
        {"mandate": mandate, "signer": signer},
    )


# --- commerce service --------------------------------------------------------
def commerce_products(query: str, limit: int = 20) -> dict[str, Any]:
    return _get(
        f"{settings.commerce_url}/products",
        {"query": query, "limit": limit},
    )


def commerce_create_order(payload: dict[str, Any]) -> dict[str, Any]:
    return _post(f"{settings.commerce_url}/orders", payload)


def commerce_orders(buyer_address: str) -> dict[str, Any]:
    return _get(
        f"{settings.commerce_url}/orders",
        {"buyerAddress": buyer_address},
    )


# --- peer agent (A2A over HTTP) ---------------------------------------------
def a2a_quote(intent: dict[str, Any]) -> dict[str, Any]:
    return _post(f"{settings.shopping_agent_url}/a2a/quote", intent)


def a2a_settle(req: dict[str, Any]) -> dict[str, Any]:
    return _post(f"{settings.shopping_agent_url}/a2a/settle", req)


def a2a_message_send(
    data: dict[str, Any], *, context_id: Optional[str] = None
) -> dict[str, Any]:
    """Send one A2A v0.3 JSON-RPC message containing an AP2 DataPart."""
    request_id = str(uuid4())
    message: dict[str, Any] = {
        "kind": "message",
        "messageId": str(uuid4()),
        "role": "user",
        "parts": [{"kind": "data", "data": data}],
    }
    if context_id:
        message["contextId"] = context_id

    response = _post(
        f"{settings.shopping_agent_url}/a2a",
        {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": "message/send",
            "params": {"message": message},
        },
    )
    if "error" in response:
        error = response["error"]
        raise RuntimeError(error.get("message", "A2A request failed"))
    result = response.get("result")
    if not isinstance(result, dict):
        raise RuntimeError("A2A response did not contain a Task result")
    return result
