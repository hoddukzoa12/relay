"""Thin synchronous HTTP clients for the TS services and the peer agent.

Sync (httpx.Client) on purpose: the same functions back both the ADK function
tools and the deterministic orchestrators, and FastAPI runs sync handlers in a
threadpool — so we avoid async plumbing entirely.
"""
from __future__ import annotations

from typing import Any, Optional

import httpx

from .config import settings

_TIMEOUT = httpx.Timeout(60.0)


def _post(url: str, payload: dict[str, Any]) -> dict[str, Any]:
    resp = httpx.post(url, json=payload, timeout=_TIMEOUT)
    resp.raise_for_status()
    return resp.json()


def _get(url: str) -> dict[str, Any]:
    resp = httpx.get(url, timeout=_TIMEOUT)
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


# --- commerce service --------------------------------------------------------
def commerce_create_order(payload: dict[str, Any]) -> dict[str, Any]:
    return _post(f"{settings.commerce_url}/orders", payload)


# --- peer agent (A2A over HTTP) ---------------------------------------------
def a2a_quote(intent: dict[str, Any]) -> dict[str, Any]:
    return _post(f"{settings.shopping_agent_url}/a2a/quote", intent)


def a2a_settle(req: dict[str, Any]) -> dict[str, Any]:
    return _post(f"{settings.shopping_agent_url}/a2a/settle", req)
