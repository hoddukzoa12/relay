"""Thin synchronous HTTP clients for the TS services and the peer agent.

Sync (httpx.Client) on purpose: the same functions back both the ADK function
tools and the deterministic orchestrators, and FastAPI runs sync handlers in a
threadpool — so we avoid async plumbing entirely.
"""
from __future__ import annotations

from functools import lru_cache
import logging
from threading import Lock
from typing import Any, Optional
from urllib.parse import quote, urlsplit
from uuid import uuid4

import httpx
import jwt
from google.auth.credentials import Credentials
from google.auth.transport.requests import Request as GoogleAuthRequest
from google.oauth2 import id_token

from .config import settings

_TIMEOUT = httpx.Timeout(60.0)
_GOOGLE_AUTH_REQUEST = GoogleAuthRequest()
_CREDENTIAL_LOCK = Lock()
_LOG = logging.getLogger(__name__)


class ServiceRequestError(RuntimeError):
    """Preserve peer HTTP status so callers can handle typed refusals safely."""

    def __init__(self, status_code: int, detail: str) -> None:
        self.status_code = status_code
        super().__init__(detail)


@lru_cache(maxsize=16)
def _id_token_credentials(audience: str) -> Credentials:
    """Resolve application-default ID-token credentials for one Cloud Run peer."""
    return id_token.fetch_id_token_credentials(
        audience,
        request=_GOOGLE_AUTH_REQUEST,
    )


def _cloud_run_auth_headers(url: str) -> dict[str, str]:
    """Authenticate run.app service-to-service calls without affecting local dev."""
    parsed = urlsplit(url)
    hostname = parsed.hostname or ""
    if parsed.scheme != "https" or not hostname.endswith(".run.app"):
        return {}

    audience = f"{parsed.scheme}://{parsed.netloc}"
    credentials = _id_token_credentials(audience)
    with _CREDENTIAL_LOCK:
        if not credentials.valid:
            credentials.refresh(_GOOGLE_AUTH_REQUEST)
        token = credentials.token
    if not token:
        raise RuntimeError(f"Unable to obtain a Cloud Run ID token for {audience}")
    claims = jwt.decode(
        token,
        options={"verify_signature": False, "verify_aud": False},
    )
    _LOG.info(
        "[cloud-run-auth] issuer=%s audience=%s email=%s subject=%s",
        claims.get("iss", ""),
        claims.get("aud", ""),
        claims.get("email", ""),
        claims.get("sub", ""),
    )
    return {"Authorization": f"Bearer {token}"}


def _post(
    url: str,
    payload: dict[str, Any],
    *,
    extra_headers: Optional[dict[str, str]] = None,
) -> dict[str, Any]:
    headers = _cloud_run_auth_headers(url)
    if extra_headers:
        headers.update(extra_headers)
    resp = httpx.post(
        url,
        json=payload,
        headers=headers,
        timeout=_TIMEOUT,
    )
    try:
        resp.raise_for_status()
    except httpx.HTTPStatusError as exc:
        try:
            payload = resp.json()
            detail = payload.get("error") or payload.get("detail")
        except (ValueError, AttributeError):
            detail = None
        raise ServiceRequestError(
            resp.status_code,
            str(detail or f"Service request failed with HTTP {resp.status_code}"),
        ) from exc
    return resp.json()


def _get(
    url: str, params: Optional[dict[str, Any]] = None
) -> dict[str, Any]:
    resp = httpx.get(
        url,
        params=params,
        headers=_cloud_run_auth_headers(url),
        timeout=_TIMEOUT,
    )
    try:
        resp.raise_for_status()
    except httpx.HTTPStatusError as exc:
        try:
            payload = resp.json()
            detail = payload.get("error") or payload.get("detail")
        except (ValueError, AttributeError):
            detail = None
        raise ServiceRequestError(
            resp.status_code,
            str(detail or f"Service request failed with HTTP {resp.status_code}"),
        ) from exc
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


def payments_pay(
    pay_to: str,
    amount: str,
    reference: str,
    *,
    delegator: Optional[str] = None,
) -> dict[str, Any]:
    body = {"payTo": pay_to, "amount": amount, "reference": reference}
    if delegator:
        body["delegator"] = delegator
    return _post(
        f"{settings.payments_url}/pay",
        body,
    )


def payments_verify(reference: str) -> dict[str, Any]:
    return _post(f"{settings.payments_url}/verify", {"reference": reference})


def payments_refund(order_ref: str, reference: str) -> dict[str, Any]:
    return _post(
        f"{settings.payments_url}/refunds",
        {"orderRef": order_ref, "reference": reference},
    )


def payments_wallets() -> dict[str, Any]:
    return _get(f"{settings.payments_url}/wallets")


def payments_delegation_status(delegator: str) -> dict[str, Any]:
    """Read live SPL delegate state for one server-verified wallet."""
    return _post(
        f"{settings.payments_url}/delegations/status",
        {"delegator": delegator},
    )


def payments_verify_delegation(
    delegator: str, approval_tx_signature: str
) -> dict[str, Any]:
    """Verify the approval transaction and return current on-chain state."""
    return _post(
        f"{settings.payments_url}/delegations/verify",
        {
            "delegator": delegator,
            "approvalTxSignature": approval_tx_signature,
        },
    )


def payments_prepare_delegation(
    delegator: str, action: str, amount: Optional[str] = None
) -> dict[str, Any]:
    """Prepare an agent-fee-paid approve/revoke transaction for the user."""
    body: dict[str, Any] = {"delegator": delegator, "action": action}
    if amount is not None:
        body["amount"] = amount
    return _post(f"{settings.payments_url}/delegations/transactions", body)


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


def commerce_product_by_handle(handle: str) -> dict[str, Any]:
    """Resolve the exact DSers-provided Shopify handle; never use a title."""
    encoded = quote(handle, safe="")
    return _get(f"{settings.commerce_url}/products/by-handle/{encoded}")


def commerce_mark_sourced_product(payload: dict[str, Any]) -> dict[str, Any]:
    """Bind one exact Shopify product ID to DSers provenance and costs."""
    return _post(f"{settings.commerce_url}/products/sourcing-metadata", payload)


def commerce_create_order(payload: dict[str, Any]) -> dict[str, Any]:
    return _post(f"{settings.commerce_url}/orders", payload)


def commerce_orders(buyer_address: str) -> dict[str, Any]:
    return _get(
        f"{settings.commerce_url}/orders",
        {"buyerAddress": buyer_address},
    )


def commerce_order(identifier: str) -> dict[str, Any]:
    encoded = quote(identifier, safe="")
    return _get(f"{settings.commerce_url}/orders/{encoded}")


def commerce_refund_order(
    order_ref: str,
    refund_reference: str,
    refund_tx_signature: str,
    refund_explorer: str,
) -> dict[str, Any]:
    encoded = quote(order_ref, safe="")
    return _post(
        f"{settings.commerce_url}/orders/{encoded}/refund",
        {
            "refundReference": refund_reference,
            "refundTxSignature": refund_tx_signature,
            "refundExplorer": refund_explorer,
        },
    )


def commerce_fulfill_order(order_ref: str) -> dict[str, Any]:
    encoded = quote(order_ref, safe="")
    return _post(f"{settings.commerce_url}/orders/{encoded}/fulfill", {})


def commerce_track_order(identifier: str) -> dict[str, Any]:
    encoded = quote(identifier, safe="")
    return _get(f"{settings.commerce_url}/orders/{encoded}/tracking")


# --- peer agent (A2A over HTTP) ---------------------------------------------
def a2a_quote(intent: dict[str, Any]) -> dict[str, Any]:
    return _post(f"{settings.shopping_agent_url}/a2a/quote", intent)


def a2a_settle(
    req: dict[str, Any], *, identity_wallet: Optional[str] = None
) -> dict[str, Any]:
    headers = (
        {"X-Relay-Authenticated-Wallet": identity_wallet}
        if identity_wallet
        else None
    )
    return _post(
        f"{settings.shopping_agent_url}/a2a/settle",
        req,
        extra_headers=headers,
    )


def shopping_order(identifier: str) -> dict[str, Any]:
    """Read an order through the shopping-agent lifecycle API."""
    encoded = quote(identifier, safe="")
    return _get(f"{settings.shopping_agent_url}/orders/{encoded}")


def shopping_source_catalog(query: str, budget: float) -> dict[str, Any]:
    """Ask the shopping broker to extend the catalog from DSers if needed."""
    return _post(
        f"{settings.shopping_agent_url}/catalog/source",
        {"query": query, "budget": budget},
    )


def shopping_refund_order(identifier: str) -> dict[str, Any]:
    """Refund an order through the shopping-agent lifecycle API."""
    encoded = quote(identifier, safe="")
    return _post(f"{settings.shopping_agent_url}/orders/{encoded}/refund", {})


def buyer_wallet_balances() -> dict[str, Any]:
    """Read display-only SOL/USDC balances from the buyer agent."""
    return _get(f"{settings.buyer_agent_url}/wallet-balances")


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
