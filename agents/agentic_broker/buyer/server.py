"""Buyer agent API used by the Shopify widget and autonomous clients."""
from __future__ import annotations

import os
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal, Optional

import httpx
from fastapi import FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from pydantic import BaseModel, Field

from ..common import service_clients
from ..common.agent_cards import buyer_agent_card
from ..common.config import settings
from ..common.contracts import (
    IntentMandate,
    StructuredShippingAddress,
    format_shipping_address,
)
from ..common.mandates import verify_wallet_signature
from . import auth, conversation, flow, tools as buyer_tools


def _cors_origins() -> list[str]:
    """Return the explicitly configured storefront origins for browser clients."""
    origins = [
        value.strip().rstrip("/")
        for value in os.getenv("BUYER_CORS_ORIGINS", "").split(",")
        if value.strip()
    ]
    shopify_domain = os.getenv("SHOPIFY_STORE_DOMAIN", "").strip().rstrip("/")
    if shopify_domain and shopify_domain != "your-store.myshopify.com":
        if not shopify_domain.startswith(("http://", "https://")):
            shopify_domain = f"https://{shopify_domain}"
        origins.append(shopify_domain)
    return list(dict.fromkeys(origins))


app = FastAPI(title="Buyer Agent", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins(),
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
    max_age=600,
)

_WEB_AUTH_CLIENT = Path(__file__).resolve().parents[1] / "web" / "auth-client.js"


class BuyRequest(BaseModel):
    text: Optional[str] = None
    query: Optional[str] = None
    budget: Optional[float] = None
    shipTo: Optional[str] = None
    shippingAddress: Optional[StructuredShippingAddress] = None
    intentMandate: Optional[IntentMandate] = None
    approvalTxSignature: Optional[str] = Field(default=None, min_length=1)


class ChatRequest(BaseModel):
    sessionId: str = Field(
        min_length=8,
        max_length=128,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]+$",
    )
    message: str = Field(min_length=1, max_length=1000)
    approvalTxSignature: Optional[str] = Field(default=None, min_length=1)


class DelegationTransactionRequest(BaseModel):
    action: Literal["approve", "revoke"]
    amount: Optional[str] = Field(
        default=None, pattern=r"^\d+(\.\d{1,6})?$"
    )


@app.get("/health")
def health() -> dict[str, object]:
    return {"ok": True, "agent": "buyer"}


@app.get("/.well-known/agent-card.json")
@app.get("/a2a/agent-card")
def agent_card() -> dict[str, Any]:
    return buyer_agent_card()


@app.get("/auth-client.js")
def auth_client() -> Response:
    if not _WEB_AUTH_CLIENT.exists():
        return Response("// Relay auth client missing\n", media_type="text/javascript")
    return Response(
        _WEB_AUTH_CLIENT.read_text(encoding="utf-8"),
        media_type="text/javascript",
    )


@app.get("/auth/config")
def auth_config() -> dict[str, Any]:
    """Public Clerk browser configuration; never exposes the secret key."""
    return {
        "configured": bool(
            settings.clerk_publishable_key
            and settings.clerk_issuer
            and settings.clerk_secret_key
        ),
        "publishableKey": settings.clerk_publishable_key,
        "issuer": settings.clerk_issuer,
    }


def _identity(authorization: Optional[str]) -> auth.ClerkIdentity:
    scheme, _, token = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(status_code=401, detail="Clerk session required")
    try:
        return auth.verify_session_token(token)
    except auth.AuthenticationError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc


def _optional_identity(
    authorization: Optional[str],
) -> auth.ClerkIdentity | None:
    if not authorization:
        return None
    return _identity(authorization)


@app.get("/auth/me")
def auth_me(authorization: Optional[str] = Header(default=None)) -> dict[str, str]:
    identity = _identity(authorization)
    return {
        "userId": identity.user_id,
        "walletAddress": identity.wallet_address,
    }


@app.get("/my-orders")
def my_orders(authorization: Optional[str] = Header(default=None)) -> dict[str, Any]:
    identity = _identity(authorization)
    return service_clients.commerce_orders(identity.wallet_address)


@app.get("/delegation")
def delegation(
    authorization: Optional[str] = Header(default=None),
) -> dict[str, Any]:
    """Return only the Clerk wallet's live on-chain SPL delegation."""
    identity = _identity(authorization)
    status = service_clients.payments_delegation_status(identity.wallet_address)
    if status.get("delegator") != identity.wallet_address:
        raise HTTPException(status_code=502, detail="Delegation identity mismatch")
    return status


@app.post("/delegation/transaction")
def delegation_transaction(
    body: DelegationTransactionRequest,
    authorization: Optional[str] = Header(default=None),
) -> dict[str, Any]:
    """Prepare a transaction for the verified Clerk wallet, never client input."""
    identity = _identity(authorization)
    if body.action == "approve" and body.amount is None:
        raise HTTPException(status_code=422, detail="Approval amount is required")
    return service_clients.payments_prepare_delegation(
        identity.wallet_address,
        body.action,
        body.amount,
    )


@app.get("/wallets")
def wallets() -> dict[str, Any]:
    try:
        return service_clients.payments_wallets()
    except Exception as exc:  # noqa: BLE001
        return {"error": str(exc)}


@app.post("/chat")
def chat(
    body: ChatRequest,
    authorization: Optional[str] = Header(default=None),
) -> dict[str, Any]:
    """Run one multi-turn ADK buyer-agent turn for a storefront session."""
    message = body.message.strip()
    if not message:
        raise HTTPException(status_code=422, detail="message must not be blank")
    identity = _optional_identity(authorization)
    # ``None`` means an anonymous human storefront request, never an agent
    # principal. Conversation payment tools fail closed on that distinction
    # while search and comparison stay public.
    return conversation.respond(
        body.sessionId,
        message,
        identity_wallet=identity.wallet_address if identity else None,
        approval_tx_signature=body.approvalTxSignature,
    )


def _decimal_units(amount: int, decimals: int) -> str:
    value = Decimal(amount) / (Decimal(10) ** decimals)
    text = format(value, "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


def _rpc_url() -> str:
    configured = os.getenv("SOLANA_RPC_URL")
    if configured:
        return configured
    return {
        "mainnet-beta": "https://api.mainnet-beta.solana.com",
        "testnet": "https://api.testnet.solana.com",
        "localnet": "http://127.0.0.1:8899",
    }.get(settings.cluster, "https://api.devnet.solana.com")


@app.get("/wallet-balances")
def wallet_balances() -> dict[str, Any]:
    """Return display-only balances without widening the payments contract."""
    try:
        wallet_info = service_clients.payments_wallets()
        requests: list[dict[str, Any]] = []
        for role in ("buyer", "merchant"):
            address = wallet_info[role]
            requests.extend(
                [
                    {
                        "jsonrpc": "2.0",
                        "id": f"{role}-sol",
                        "method": "getBalance",
                        "params": [address, {"commitment": "confirmed"}],
                    },
                    {
                        "jsonrpc": "2.0",
                        "id": f"{role}-usdc",
                        "method": "getTokenAccountsByOwner",
                        "params": [
                            address,
                            {"mint": wallet_info["usdcMint"]},
                            {"encoding": "jsonParsed", "commitment": "confirmed"},
                        ],
                    },
                ]
            )

        response = httpx.post(_rpc_url(), json=requests, timeout=10.0)
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, list):
            raise ValueError("Solana RPC batch response was not a list")
        by_id = {item.get("id"): item for item in payload if isinstance(item, dict)}

        balances: dict[str, Any] = {
            "cluster": wallet_info["cluster"],
            "usdcMint": wallet_info["usdcMint"],
        }
        for role in ("buyer", "merchant"):
            sol_result = by_id.get(f"{role}-sol", {})
            token_result = by_id.get(f"{role}-usdc", {})
            if sol_result.get("error"):
                raise RuntimeError(str(sol_result["error"]))
            if token_result.get("error"):
                raise RuntimeError(str(token_result["error"]))

            lamports = int(sol_result.get("result", {}).get("value", 0))
            token_accounts = token_result.get("result", {}).get("value", [])
            usdc_base_units = 0
            usdc_decimals = 6
            for account in token_accounts:
                token_amount = (
                    account.get("account", {})
                    .get("data", {})
                    .get("parsed", {})
                    .get("info", {})
                    .get("tokenAmount", {})
                )
                usdc_base_units += int(token_amount.get("amount", 0))
                usdc_decimals = int(token_amount.get("decimals", usdc_decimals))

            balances[role] = {
                "address": wallet_info[role],
                "sol": _decimal_units(lamports, 9),
                "usdc": _decimal_units(usdc_base_units, usdc_decimals),
            }
        return balances
    except Exception as exc:  # noqa: BLE001
        return {"error": str(exc)}


@app.post("/buy")
def buy(
    body: BuyRequest,
    authorization: Optional[str] = Header(default=None),
) -> dict[str, Any]:
    identity = _optional_identity(authorization)
    if body.text:
        try:
            return flow.buy_from_text(
                body.text,
                identity_wallet=identity.wallet_address if identity else None,
                shipping_address=body.shippingAddress,
            )
        except buyer_tools.DelegationApprovalRequiredError as exc:
            raise HTTPException(status_code=409, detail=exc.response) from exc
    query = body.query or "wireless earbuds"
    budget = (
        body.budget
        if body.budget is not None
        else settings.default_budget_usdc
    )
    ship_to = (
        format_shipping_address(body.shippingAddress)
        if body.shippingAddress
        else body.shipTo or settings.default_ship_to
    )

    if body.intentMandate is not None:
        if identity is None:
            raise HTTPException(status_code=401, detail="Clerk session required")
        mandate = body.intentMandate
        mandate_data = mandate.model_dump(exclude_none=True)
        if mandate.signer_wallet != identity.wallet_address:
            raise HTTPException(
                status_code=403,
                detail="IntentMandate signer does not match the Clerk wallet",
            )
        if mandate.delegator and mandate.delegator != identity.wallet_address:
            raise HTTPException(
                status_code=403,
                detail="IntentMandate delegator does not match the Clerk wallet",
            )
        if not verify_wallet_signature(
            mandate_data, mandate.signature, identity.wallet_address
        ):
            raise HTTPException(
                status_code=422, detail="Invalid IntentMandate wallet signature"
            )
        try:
            expiry = datetime.fromisoformat(
                mandate.intent_expiry.replace("Z", "+00:00")
            )
        except ValueError as exc:
            raise HTTPException(
                status_code=422, detail="Invalid IntentMandate expiry"
            ) from exc
        if expiry.tzinfo is None or expiry <= datetime.now(timezone.utc):
            raise HTTPException(status_code=422, detail="IntentMandate has expired")
        if (
            mandate.user_cart_confirmation_required
            or mandate.natural_language_description != query
            or mandate.ship_to != ship_to
            or mandate.shipping_address != body.shippingAddress
            or Decimal(mandate.price_ceiling.amount) != Decimal(str(budget))
        ):
            raise HTTPException(
                status_code=422,
                detail="IntentMandate does not match the requested purchase",
            )

    try:
        return flow.buy(
            query=query,
            budget=budget,
            ship_to=ship_to,
            intent_mandate=body.intentMandate,
            identity_wallet=identity.wallet_address if identity else None,
            shipping_address=body.shippingAddress,
        )
    except buyer_tools.DelegationApprovalRequiredError as exc:
        raise HTTPException(status_code=409, detail=exc.response) from exc


@app.post("/web/buy")
def web_buy(
    body: BuyRequest,
    authorization: Optional[str] = Header(default=None),
) -> dict[str, Any]:
    """Human-present storefront path: Clerk + live SPL delegation are mandatory."""
    identity = _identity(authorization)
    if not body.approvalTxSignature:
        raise HTTPException(
            status_code=409,
            detail="Approve a USDC spending limit before purchasing",
        )

    try:
        with buyer_tools.storefront_context(
            identity.wallet_address, body.approvalTxSignature
        ):
            if body.text:
                return flow.buy_from_text(
                    body.text,
                    identity_wallet=identity.wallet_address,
                    shipping_address=body.shippingAddress,
                )
            return flow.buy(
                query=body.query or "wireless earbuds",
                budget=body.budget if body.budget is not None else 30.0,
                ship_to=(
                    format_shipping_address(body.shippingAddress)
                    if body.shippingAddress
                    else body.shipTo or settings.default_ship_to
                ),
                identity_wallet=identity.wallet_address,
                shipping_address=body.shippingAddress,
            )
    except buyer_tools.DelegationApprovalRequiredError as exc:
        raise HTTPException(status_code=409, detail=exc.response) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


def main() -> None:
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=settings.buyer_port)


if __name__ == "__main__":
    main()
