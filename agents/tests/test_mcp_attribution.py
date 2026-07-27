from __future__ import annotations

from types import SimpleNamespace

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from agentic_broker.common import service_clients
from agentic_broker.mcp import auth, server
from agentic_broker.shopping import server as shopping_server

USER_WALLET = "Bn7UDWnm59HtG4zeSS2gF2MWT8bADFDuVaG5Y95HLoFf"
ATTACKER_WALLET = "AttackerSuppliedWallet"


def _identity() -> auth.OAuthIdentity:
    return auth.OAuthIdentity(
        user_id="user_test",
        client_id="client_test",
        scopes=("openid",),
        expires_at=4_102_444_800,
        wallet_address=USER_WALLET,
    )


def test_clerk_oauth_token_is_jwks_verified_and_resolved(
    monkeypatch,
) -> None:
    calls: dict[str, object] = {}

    def fake_claims(token: str, *, required_claims):
        calls["token"] = token
        calls["required_claims"] = required_claims
        return {
            "sub": "user_test",
            "exp": 4_102_444_800,
            "iat": 1_700_000_000,
            "iss": "https://clerk.test",
        }

    def fake_post(url: str, **kwargs):
        calls["url"] = url
        calls["json"] = kwargs["json"]
        return SimpleNamespace(
            status_code=200,
            json=lambda: {
                "object": "clerk_idp_oauth_access_token",
                "client_id": "client_test",
                "subject": "user_test",
                "scopes": ["openid"],
                "revoked": False,
            },
        )

    monkeypatch.setattr(
        auth.clerk_auth, "verify_token_claims", fake_claims
    )
    monkeypatch.setattr(
        auth.clerk_auth,
        "resolve_identity",
        lambda _: SimpleNamespace(wallet_address=USER_WALLET),
    )
    monkeypatch.setattr(auth.httpx, "post", fake_post)
    monkeypatch.setattr(
        auth,
        "settings",
        SimpleNamespace(
            clerk_secret_key="secret",
            clerk_api_url="https://api.clerk.test",
            clerk_issuer="https://clerk.test",
        ),
    )

    identity = auth.verify_oauth_token("oauth-token")

    assert identity == _identity()
    assert calls["required_claims"] == ("exp", "iat", "iss", "sub")
    assert calls["json"] == {"access_token": "oauth-token"}


def test_verified_oauth_wallet_flows_to_internal_settlement(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_settle(
        request: dict[str, object], *, identity_wallet: str | None = None
    ) -> dict[str, object]:
        captured.update(request)
        captured["identity_wallet"] = identity_wallet
        return {"status": "paid"}

    monkeypatch.setattr(service_clients, "a2a_settle", fake_settle)
    context_token = auth._caller.set(
        auth.Caller(kind="oauth", oauth=_identity())
    )
    try:
        result = server.settle("ord_test", "reference", "signature")
    finally:
        auth._caller.reset(context_token)

    assert result == {"status": "paid"}
    assert captured == {
        "orderRef": "ord_test",
        "reference": "reference",
        "txSignature": "signature",
        "identity_wallet": USER_WALLET,
    }


def test_service_principal_keeps_agent_wallet_fallback(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_settle(
        request: dict[str, object], *, identity_wallet: str | None = None
    ) -> dict[str, object]:
        captured["identity_wallet"] = identity_wallet
        return {"status": "paid"}

    monkeypatch.setattr(service_clients, "a2a_settle", fake_settle)
    context_token = auth._caller.set(auth.Caller(kind="service"))
    try:
        server.settle("ord_test", "reference", "signature")
    finally:
        auth._caller.reset(context_token)

    assert captured["identity_wallet"] is None


def test_oauth_order_reads_are_scoped_to_verified_wallet(monkeypatch) -> None:
    monkeypatch.setattr(
        service_clients,
        "commerce_orders",
        lambda _: {
            "orders": [
                {
                    "orderRef": "ord_owned",
                    "name": "#1001",
                    "shopifyOrderId": "gid://shopify/Order/1",
                }
            ]
        },
    )
    monkeypatch.setattr(
        service_clients,
        "shopping_order",
        lambda identifier: {"orderRef": identifier},
    )
    context_token = auth._caller.set(
        auth.Caller(kind="oauth", oauth=_identity())
    )
    try:
        assert server.get_order_status("ord_owned") == {
            "orderRef": "ord_owned"
        }
        try:
            server.get_order_status("ord_other")
        except PermissionError as exc:
            assert "authenticated wallet" in str(exc)
        else:
            raise AssertionError("another wallet's order should be denied")
    finally:
        auth._caller.reset(context_token)


def test_client_supplied_wallet_header_cannot_override_verified_identity() -> None:
    async def whoami(_: Request) -> JSONResponse:
        return JSONResponse({"wallet": auth.current_wallet()})

    protected = auth.AuthenticationMiddleware(
        Starlette(routes=[Route("/", whoami)]),
        api_key="",
        oauth_enabled=True,
        identity_verifier=lambda _: _identity(),
    )
    client = TestClient(protected)
    try:
        response = client.get(
            "/",
            headers={
                "Authorization": "Bearer valid-oauth-token",
                "X-Relay-Authenticated-Wallet": ATTACKER_WALLET,
            },
        )
    finally:
        client.close()

    assert response.status_code == 200
    assert response.json() == {"wallet": USER_WALLET}


def test_authenticated_mcp_call_uses_token_wallet_not_tool_argument(
    monkeypatch,
) -> None:
    captured: dict[str, object] = {}

    def fake_settle(
        request: dict[str, object], *, identity_wallet: str | None = None
    ) -> dict[str, object]:
        captured.update(request)
        captured["identity_wallet"] = identity_wallet
        return {"status": "paid"}

    monkeypatch.setattr(service_clients, "a2a_settle", fake_settle)
    app = server.create_app(
        api_key="",
        cors_origins="",
        oauth_enabled=True,
        identity_verifier=lambda _: _identity(),
    )
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {
            "name": "settle",
            "arguments": {
                "order_ref": "ord_test",
                "reference": "reference",
                "tx_signature": "signature",
                "buyer_wallet": ATTACKER_WALLET,
            },
        },
    }
    with TestClient(app) as client:
        response = client.post(
            "/mcp",
            headers={
                "Authorization": "Bearer valid-oauth-token",
                "Accept": "application/json, text/event-stream",
            },
            json=payload,
        )

    assert response.status_code == 200
    assert response.json()["result"]["isError"] is False
    assert captured["identity_wallet"] == USER_WALLET


def test_oauth_transport_rejects_unauthenticated_calls_with_discovery() -> None:
    app = server.create_app(
        api_key="",
        cors_origins="",
        oauth_enabled=True,
        identity_verifier=lambda _: _identity(),
    )
    client = TestClient(app)
    try:
        missing = client.post("/mcp", json={"jsonrpc": "2.0"})
        metadata = client.get(
            "/.well-known/oauth-protected-resource/mcp"
        )
    finally:
        client.close()

    assert missing.status_code == 401
    assert (
        'resource_metadata="http://testserver/'
        '.well-known/oauth-protected-resource/mcp"'
        in missing.headers["www-authenticate"]
    )
    assert metadata.status_code == 200
    assert metadata.json()["resource"] == "http://testserver/mcp"


def test_private_shopping_header_controls_attribution_only(monkeypatch) -> None:
    calls: list[str | None] = []

    def fake_handle_settle(_request, *, identity_wallet=None):
        calls.append(identity_wallet)
        return {
            "orderRef": "ord_test",
            "status": "paid",
            "txSignature": "signature",
            "explorer": "https://explorer.solana.com/tx/signature",
            "shopifyOrderId": "gid://shopify/Order/1",
        }

    monkeypatch.setattr(
        shopping_server.broker, "handle_settle", fake_handle_settle
    )
    client = TestClient(shopping_server.app)
    payload = {
        "orderRef": "ord_test",
        "reference": "reference",
        "txSignature": "signature",
    }
    try:
        attributed = client.post(
            "/a2a/settle",
            headers={"X-Relay-Authenticated-Wallet": USER_WALLET},
            json=payload,
        )
        legacy = client.post("/a2a/settle", json=payload)
    finally:
        client.close()

    assert attributed.status_code == 200
    assert legacy.status_code == 200
    assert calls == [USER_WALLET, None]
