"""Remote MCP surface for Relay's autonomous commerce flow.

The tools deliberately delegate to the existing service clients and buyer
workflow. This module owns MCP transport and its public-edge authentication.
"""
from __future__ import annotations

import contextlib
from typing import Any

import uvicorn
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from starlette.applications import Starlette
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Mount, Route
from starlette.types import ASGIApp

from ..buyer import tools as buyer_tools
from ..common import service_clients
from ..common.config import settings
from ..common.contracts import StructuredShippingAddress
from . import auth


def _comma_separated(value: str) -> list[str]:
    return [
        item.strip().rstrip("/")
        for item in value.split(",")
        if item.strip()
    ]


def search_products(query: str, limit: int = 20) -> dict[str, Any]:
    """Search the live Shopify-backed catalog without inventing products."""
    if not 1 <= limit <= 50:
        raise ValueError("limit must be between 1 and 50")
    return service_clients.commerce_products(query, limit)


def request_quote(
    query: str,
    budget: float,
    ship_to: str,
    shipping_address: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Request a product quote and agent-native Solana Pay payment request."""
    if budget <= 0:
        raise ValueError("budget must be positive")
    kwargs: dict[str, Any] = {"delegator": auth.current_wallet()}
    if shipping_address:
        kwargs["shipping_address"] = StructuredShippingAddress(
            **shipping_address
        )
    try:
        return buyer_tools.request_quote(
            query,
            budget,
            ship_to,
            **kwargs,
        )
    except buyer_tools.DelegationApprovalRequiredError as exc:
        return exc.response


def authorize_payment(
    pay_to: str, amount: str, reference: str
) -> dict[str, Any]:
    """Autonomously sign and broadcast the exact quoted devnet USDC payment."""
    delegator = auth.current_wallet()
    try:
        return buyer_tools.authorize_payment(
            pay_to,
            amount,
            reference,
            delegator=delegator,
        )
    except buyer_tools.DelegationApprovalRequiredError as exc:
        return exc.response
    except service_clients.ServiceRequestError as exc:
        if not delegator or exc.status_code != 409:
            raise
        # The payments service performs the authoritative last-moment read. If
        # allowance changes after our preflight, preserve its refusal and give
        # the OAuth user a concrete one-time recovery link.
        return buyer_tools.delegation_approval_response(
            delegator,
            amount,
            reason=str(exc),
        )


def settle(
    order_ref: str, reference: str, tx_signature: str
) -> dict[str, Any]:
    """Verify payment on-chain by reference and record the paid Shopify order."""
    # OAuth proves the Clerk user, then Clerk's Backend API resolves the wallet.
    # The wallet is attribution metadata only: the configured agent wallet
    # remains the autonomous payer, and shopping still verifies on-chain.
    return service_clients.a2a_settle(
        {
            "orderRef": order_ref,
            "reference": reference,
            "txSignature": tx_signature,
        },
        identity_wallet=auth.current_wallet(),
    )


def get_order_status(order_ref: str) -> dict[str, Any]:
    """Look up a Relay order and its payment, fulfillment, and refund proofs."""
    _require_owned_order(order_ref)
    return service_clients.shopping_order(order_ref)


def refund_order(order_ref: str) -> dict[str, Any]:
    """Return the order's full USDC amount merchant-to-buyer and update Shopify."""
    _require_owned_order(order_ref)
    return service_clients.shopping_refund_order(order_ref)


def wallet_balances() -> dict[str, Any]:
    """Show buyer and merchant SOL/USDC balances without exposing wallet keys."""
    return service_clients.buyer_wallet_balances()


def _require_owned_order(identifier: str) -> None:
    """Keep OAuth users inside their own Shopify order ledger."""
    wallet = auth.current_wallet()
    if not wallet:
        # The API-key path is a trusted service principal with no user wallet.
        return
    orders = service_clients.commerce_orders(wallet).get("orders", [])
    if not any(
        identifier
        in {
            order.get("orderRef"),
            order.get("name"),
            order.get("shopifyOrderId"),
        }
        for order in orders
        if isinstance(order, dict)
    ):
        raise PermissionError("order does not belong to the authenticated wallet")


def _create_mcp_server() -> FastMCP:
    protocol = FastMCP(
        name="Relay",
        instructions=(
            "Autonomous agent commerce settled in devnet USDC. Request a quote, "
            "authorize its exact payment, then settle using the returned proof."
        ),
        stateless_http=True,
        json_response=True,
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=_comma_separated(settings.mcp_allowed_hosts),
            allowed_origins=_comma_separated(settings.mcp_allowed_origins),
        ),
    )
    for tool in (
        search_products,
        request_quote,
        authorize_payment,
        settle,
        get_order_status,
        refund_order,
        wallet_balances,
    ):
        protocol.tool()(tool)
    return protocol


async def _health(request: Request) -> JSONResponse:
    return JSONResponse(
        {
            "ok": bool(
                request.app.state.mcp_api_key_configured
                or request.app.state.mcp_oauth_configured
            ),
            "agent": "mcp",
            "transport": "streamable-http",
            "endpoint": "/mcp",
            "authentication": {
                "oauth": bool(request.app.state.mcp_oauth_configured),
                "serviceApiKey": bool(
                    request.app.state.mcp_api_key_configured
                ),
            },
        }
    )


def _cors_origins(value: str) -> list[str]:
    return _comma_separated(value)


def create_app(
    *,
    api_key: str | None = None,
    cors_origins: str | None = None,
    oauth_enabled: bool | None = None,
    identity_verifier: auth.IdentityVerifier = auth.verify_oauth_token,
) -> Starlette:
    """Create the Cloud Run/local ASGI app with a protected MCP mount."""
    configured_api_key = settings.mcp_api_key if api_key is None else api_key
    configured_oauth = (
        bool(
            settings.clerk_issuer
            and settings.clerk_jwks_url
            and settings.clerk_secret_key
        )
        if oauth_enabled is None
        else oauth_enabled
    )
    protocol = _create_mcp_server()
    protected: ASGIApp = auth.AuthenticationMiddleware(
        protocol.streamable_http_app(),
        api_key=configured_api_key,
        oauth_enabled=configured_oauth,
        identity_verifier=identity_verifier,
    )
    origins = _cors_origins(
        settings.mcp_cors_origins if cors_origins is None else cors_origins
    )
    if origins:
        protected = CORSMiddleware(
            protected,
            allow_origins=origins,
            allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
            allow_headers=[
                "Accept",
                "Authorization",
                "Content-Type",
                "Mcp-Protocol-Version",
                "Mcp-Session-Id",
                "X-Relay-API-Key",
            ],
            expose_headers=["Mcp-Session-Id", "WWW-Authenticate"],
            max_age=600,
        )

    @contextlib.asynccontextmanager
    async def lifespan(_: Starlette):
        async with protocol.session_manager.run():
            yield

    application = Starlette(
        routes=[
            Route("/health", endpoint=_health, methods=["GET"]),
            Route(
                "/.well-known/oauth-protected-resource",
                endpoint=auth.protected_resource_metadata,
                methods=["GET"],
            ),
            Route(
                "/.well-known/oauth-protected-resource/mcp",
                endpoint=auth.protected_resource_metadata,
                methods=["GET"],
            ),
            Mount("/", app=protected),
        ],
        lifespan=lifespan,
    )
    application.state.mcp_api_key_configured = bool(configured_api_key)
    application.state.mcp_oauth_configured = configured_oauth
    return application


app = create_app()


def main() -> None:
    uvicorn.run(app, host="0.0.0.0", port=settings.mcp_port)


if __name__ == "__main__":
    main()
