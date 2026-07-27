"""MCP OAuth authentication backed by Clerk."""
from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass
import secrets
from typing import Callable, Literal

import anyio
import httpx
from starlette.datastructures import Headers
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from ..buyer import auth as clerk_auth
from ..common.config import settings

_API_KEY_HEADER = "x-relay-api-key"
_REQUIRED_SCOPE = "openid"


@dataclass(frozen=True)
class OAuthIdentity:
    """A Clerk OAuth caller and the verified wallet it owns."""

    user_id: str
    client_id: str
    scopes: tuple[str, ...]
    expires_at: int
    wallet_address: str


@dataclass(frozen=True)
class Caller:
    """The principal authenticated for the current MCP request."""

    kind: Literal["oauth", "service"]
    oauth: OAuthIdentity | None = None


_caller: ContextVar[Caller | None] = ContextVar("relay_mcp_caller", default=None)


def current_caller() -> Caller | None:
    """Return the authenticated MCP principal in the current tool call."""
    return _caller.get()


def current_wallet() -> str | None:
    """Return the server-verified OAuth wallet, never a client-supplied value."""
    caller = current_caller()
    return caller.oauth.wallet_address if caller and caller.oauth else None


def verify_oauth_token(token: str) -> OAuthIdentity:
    """Verify a Clerk JWT access token and resolve its verified Solana wallet."""
    claims = clerk_auth.verify_token_claims(
        token,
        required_claims=("exp", "iat", "iss", "sub"),
    )
    if not settings.clerk_secret_key:
        raise clerk_auth.AuthenticationError(
            "Clerk OAuth verification is not configured"
        )

    try:
        response = httpx.post(
            f"{settings.clerk_api_url}/oauth_applications/access_tokens/verify",
            headers={"Authorization": f"Bearer {settings.clerk_secret_key}"},
            json={"access_token": token},
            timeout=10.0,
        )
    except httpx.HTTPError as exc:
        raise clerk_auth.AuthenticationError(
            "Clerk OAuth verification failed"
        ) from exc
    if response.status_code != 200:
        raise clerk_auth.AuthenticationError("Invalid Clerk OAuth access token")
    payload = response.json()
    if not isinstance(payload, dict):
        raise clerk_auth.AuthenticationError(
            "Clerk returned an invalid OAuth token record"
        )

    user_id = claims.get("sub")
    subject = payload.get("subject")
    client_id = payload.get("client_id")
    scopes = payload.get("scopes")
    expires_at = claims.get("exp")
    if (
        payload.get("object") != "clerk_idp_oauth_access_token"
        or payload.get("revoked") is True
        or not isinstance(user_id, str)
        or subject != user_id
        or not isinstance(client_id, str)
        or not client_id
        or not isinstance(scopes, list)
        or not all(isinstance(scope, str) for scope in scopes)
        or not isinstance(expires_at, int)
    ):
        raise clerk_auth.AuthenticationError("Invalid Clerk OAuth access token")

    identity = clerk_auth.resolve_identity(user_id)
    return OAuthIdentity(
        user_id=user_id,
        client_id=client_id,
        scopes=tuple(scopes),
        expires_at=expires_at,
        wallet_address=identity.wallet_address,
    )


IdentityVerifier = Callable[[str], OAuthIdentity]


def _request_origin(scope: Scope) -> str:
    headers = Headers(scope=scope)
    scheme = headers.get("x-forwarded-proto", scope.get("scheme", "https"))
    host = headers.get("host", "")
    return f"{scheme}://{host}".rstrip("/")


def _resource_metadata_url(scope: Scope) -> str:
    return f"{_request_origin(scope)}/.well-known/oauth-protected-resource/mcp"


async def _auth_error(
    scope: Scope,
    receive: Receive,
    send: Send,
    *,
    status_code: int,
    error: str,
    description: str,
    oauth_enabled: bool,
    api_key_enabled: bool,
) -> None:
    response = JSONResponse(
        {"error": error, "error_description": description},
        status_code=status_code,
    )
    if oauth_enabled:
        response.headers.append(
            "WWW-Authenticate",
            (
                f'Bearer error="{error}", '
                f'error_description="{description}", '
                f'resource_metadata="{_resource_metadata_url(scope)}"'
            ),
        )
    if api_key_enabled:
        response.headers.append(
            "WWW-Authenticate", 'RelayApiKey realm="relay-mcp"'
        )
    await response(scope, receive, send)


class AuthenticationMiddleware:
    """Authenticate MCP requests as a Clerk user or trusted agent service."""

    def __init__(
        self,
        app: ASGIApp,
        *,
        api_key: str,
        oauth_enabled: bool,
        identity_verifier: IdentityVerifier = verify_oauth_token,
    ) -> None:
        self.app = app
        self.api_key = api_key
        self.oauth_enabled = oauth_enabled
        self.identity_verifier = identity_verifier

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        if not self.api_key and not self.oauth_enabled:
            response = JSONResponse(
                {"error": "MCP authentication is not configured"},
                status_code=503,
            )
            await response(scope, receive, send)
            return

        headers = Headers(scope=scope)
        provided_key = headers.get(_API_KEY_HEADER, "")
        authorization = headers.get("authorization", "")
        bearer = (
            authorization[7:].strip()
            if authorization.lower().startswith("bearer ")
            else ""
        )

        if provided_key and authorization:
            await _auth_error(
                scope,
                receive,
                send,
                status_code=401,
                error="invalid_request",
                description="Use exactly one MCP authentication method",
                oauth_enabled=self.oauth_enabled,
                api_key_enabled=bool(self.api_key),
            )
            return

        caller: Caller | None = None
        if bearer and self.oauth_enabled:
            try:
                identity = await anyio.to_thread.run_sync(
                    self.identity_verifier, bearer
                )
            except (clerk_auth.AuthenticationError, ValueError):
                identity = None
            if identity is not None and _REQUIRED_SCOPE in identity.scopes:
                caller = Caller(kind="oauth", oauth=identity)
            elif identity is not None:
                await _auth_error(
                    scope,
                    receive,
                    send,
                    status_code=403,
                    error="insufficient_scope",
                    description=f"Required scope: {_REQUIRED_SCOPE}",
                    oauth_enabled=True,
                    api_key_enabled=bool(self.api_key),
                )
                return
        elif (
            provided_key
            and self.api_key
            and secrets.compare_digest(provided_key, self.api_key)
        ):
            caller = Caller(kind="service")

        if caller is None:
            await _auth_error(
                scope,
                receive,
                send,
                status_code=401,
                error="invalid_token",
                description="Authentication required",
                oauth_enabled=self.oauth_enabled,
                api_key_enabled=bool(self.api_key),
            )
            return

        context_token = _caller.set(caller)
        try:
            await self.app(scope, receive, send)
        finally:
            _caller.reset(context_token)


async def protected_resource_metadata(request) -> JSONResponse:
    """Serve RFC 9728 discovery metadata for the `/mcp` resource."""
    origin = _request_origin(request.scope)
    return JSONResponse(
        {
            "resource": f"{origin}/mcp",
            "authorization_servers": (
                [settings.clerk_issuer] if settings.clerk_issuer else []
            ),
            "bearer_methods_supported": ["header"],
            "scopes_supported": [_REQUIRED_SCOPE],
            "resource_name": "Relay MCP",
        }
    )
