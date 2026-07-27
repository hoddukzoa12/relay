"""Remote MCP surface for Relay's autonomous commerce flow.

The tools deliberately delegate to the existing service clients and buyer
workflow. This module owns only MCP transport and its public-edge API-key check.
"""
from __future__ import annotations

import contextlib
import secrets
from typing import Any

import uvicorn
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from starlette.applications import Starlette
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Mount, Route
from starlette.types import ASGIApp, Receive, Scope, Send

from ..buyer import tools as buyer_tools
from ..common import service_clients
from ..common.config import settings

_API_KEY_HEADER = b"x-relay-api-key"


def _comma_separated(value: str) -> list[str]:
    return [
        item.strip().rstrip("/")
        for item in value.split(",")
        if item.strip()
    ]


mcp = FastMCP(
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


@mcp.tool()
def search_products(query: str, limit: int = 20) -> dict[str, Any]:
    """Search the live Shopify-backed catalog without inventing products."""
    if not 1 <= limit <= 50:
        raise ValueError("limit must be between 1 and 50")
    return service_clients.commerce_products(query, limit)


@mcp.tool()
def request_quote(query: str, budget: float, ship_to: str) -> dict[str, Any]:
    """Request a product quote and agent-native Solana Pay payment request."""
    if budget <= 0:
        raise ValueError("budget must be positive")
    return buyer_tools.request_quote(query, budget, ship_to)


@mcp.tool()
def authorize_payment(
    pay_to: str, amount: str, reference: str
) -> dict[str, Any]:
    """Autonomously sign and broadcast the exact quoted devnet USDC payment."""
    return buyer_tools.authorize_payment(pay_to, amount, reference)


@mcp.tool()
def settle(
    order_ref: str, reference: str, tx_signature: str
) -> dict[str, Any]:
    """Verify payment on-chain by reference and record the paid Shopify order."""
    return buyer_tools.confirm_settlement(order_ref, reference, tx_signature)


@mcp.tool()
def get_order_status(order_ref: str) -> dict[str, Any]:
    """Look up a Relay order and its payment, fulfillment, and refund proofs."""
    return service_clients.shopping_order(order_ref)


@mcp.tool()
def refund_order(order_ref: str) -> dict[str, Any]:
    """Return the order's full USDC amount merchant-to-buyer and update Shopify."""
    return service_clients.shopping_refund_order(order_ref)


@mcp.tool()
def wallet_balances() -> dict[str, Any]:
    """Show buyer and merchant SOL/USDC balances without exposing wallet keys."""
    return service_clients.buyer_wallet_balances()


class ApiKeyMiddleware:
    """Fail-closed shared-secret check for the public MCP transport."""

    def __init__(self, app: ASGIApp, api_key: str) -> None:
        self.app = app
        self.api_key = api_key

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        if not self.api_key:
            response = JSONResponse(
                {"error": "MCP authentication is not configured"},
                status_code=503,
            )
            await response(scope, receive, send)
            return

        headers = dict(scope.get("headers", []))
        provided = headers.get(_API_KEY_HEADER, b"").decode(
            "utf-8", errors="ignore"
        )
        if not secrets.compare_digest(provided, self.api_key):
            response = JSONResponse(
                {"error": "invalid or missing MCP API key"},
                status_code=401,
                headers={"WWW-Authenticate": 'RelayApiKey realm="relay-mcp"'},
            )
            await response(scope, receive, send)
            return

        await self.app(scope, receive, send)


async def _health(request: Request) -> JSONResponse:
    return JSONResponse(
        {
            "ok": bool(request.app.state.mcp_api_key_configured),
            "agent": "mcp",
            "transport": "streamable-http",
            "endpoint": "/mcp",
            "authentication": "X-Relay-API-Key",
        }
    )


def _cors_origins(value: str) -> list[str]:
    return _comma_separated(value)


def create_app(
    *,
    api_key: str | None = None,
    cors_origins: str | None = None,
) -> Starlette:
    """Create the Cloud Run/local ASGI app with a protected MCP mount."""
    configured_api_key = settings.mcp_api_key if api_key is None else api_key
    protected: ASGIApp = ApiKeyMiddleware(
        mcp.streamable_http_app(),
        configured_api_key,
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
                "Content-Type",
                "Mcp-Protocol-Version",
                "Mcp-Session-Id",
                "X-Relay-API-Key",
            ],
            expose_headers=["Mcp-Session-Id"],
            max_age=600,
        )

    @contextlib.asynccontextmanager
    async def lifespan(_: Starlette):
        async with mcp.session_manager.run():
            yield

    application = Starlette(
        routes=[
            Route("/health", endpoint=_health, methods=["GET"]),
            Mount("/", app=protected),
        ],
        lifespan=lifespan,
    )
    application.state.mcp_api_key_configured = bool(configured_api_key)
    return application


app = create_app()


def main() -> None:
    uvicorn.run(app, host="0.0.0.0", port=settings.mcp_port)


if __name__ == "__main__":
    main()
