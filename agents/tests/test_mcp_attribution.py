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


def test_oauth_quote_and_payment_use_verified_wallet_as_delegator(
    monkeypatch,
) -> None:
    calls: list[tuple[str, str | None]] = []

    def fake_quote(*_args, delegator=None):
        calls.append(("quote", delegator))
        return {"reference": "reference"}

    def fake_pay(*_args, delegator=None):
        calls.append(("pay", delegator))
        return {"txSignature": "signature"}

    monkeypatch.setattr(server.buyer_tools, "request_quote", fake_quote)
    monkeypatch.setattr(server.buyer_tools, "authorize_payment", fake_pay)
    context_token = auth._caller.set(
        auth.Caller(kind="oauth", oauth=_identity())
    )
    try:
        server.request_quote("earbuds", 5, "Seoul")
        server.authorize_payment("merchant", "1.00", "reference")
    finally:
        auth._caller.reset(context_token)

    assert calls == [("quote", USER_WALLET), ("pay", USER_WALLET)]


def test_oauth_missing_delegation_returns_link_without_agent_fallback(
    monkeypatch,
) -> None:
    status = {
        "active": False,
        "delegator": USER_WALLET,
        "delegateAuthority": "agent-wallet",
        "allowanceRemaining": {"amount": "0", "currency": "USDC"},
        "balance": {"amount": "3", "currency": "USDC"},
        "sourceTokenAccount": "source-account",
        "usdcMint": "mint",
        "network": "solana-devnet",
    }
    monkeypatch.setattr(
        service_clients,
        "payments_delegation_status",
        lambda _: status,
    )

    def unexpected_pay(*_args, **_kwargs):
        raise AssertionError("authenticated refusal must not use the agent wallet")

    monkeypatch.setattr(service_clients, "payments_pay", unexpected_pay)
    context_token = auth._caller.set(
        auth.Caller(kind="oauth", oauth=_identity())
    )
    try:
        result = server.authorize_payment(
            "merchant",
            "1.00",
            "reference",
        )
    finally:
        auth._caller.reset(context_token)

    assert result["status"] == "approval-required"
    assert result["delegator"] == USER_WALLET
    assert result["requiredAmount"] == {
        "amount": "1.00",
        "currency": "USDC",
    }
    assert result["approvalUrl"].startswith("https://")
    assert "relayAction=approve" in result["approvalUrl"]
    assert "relayAmount=1.00" in result["approvalUrl"]


def test_service_principal_payment_omits_delegator(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_pay(*_args, delegator=None):
        captured["delegator"] = delegator
        return {"txSignature": "signature"}

    monkeypatch.setattr(server.buyer_tools, "authorize_payment", fake_pay)
    context_token = auth._caller.set(auth.Caller(kind="service"))
    try:
        server.authorize_payment("merchant", "1.00", "reference")
    finally:
        auth._caller.reset(context_token)

    assert captured["delegator"] is None


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
    calls: list[dict[str, object]] = []

    def fake_handle_settle(
        _request,
        *,
        identity_wallet=None,
        human_customer=False,
        customer_email=None,
    ):
        calls.append(
            {
                "identity_wallet": identity_wallet,
                "human_customer": human_customer,
                "customer_email": customer_email,
            }
        )
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
            headers={
                "X-Relay-Authenticated-Wallet": USER_WALLET,
                "X-Relay-Human-Customer": "true",
                "X-Relay-Authenticated-Customer-Email": (
                    "verified@example.com"
                ),
            },
            json=payload,
        )
        legacy = client.post("/a2a/settle", json=payload)
    finally:
        client.close()

    assert attributed.status_code == 200
    assert legacy.status_code == 200
    assert calls == [
        {
            "identity_wallet": USER_WALLET,
            "human_customer": True,
            "customer_email": "verified@example.com",
        },
        {
            "identity_wallet": None,
            "human_customer": False,
            "customer_email": None,
        },
    ]


def test_a2a_human_delegator_must_match_signed_wallet() -> None:
    intent = {
        "user_cart_confirmation_required": False,
        "natural_language_description": "earbuds",
        "requires_refundability": False,
        "price_ceiling": {"amount": "5.00", "currency": "USDC"},
        "ship_to": "Seoul",
        "intent_expiry": "2099-01-01T00:00:00Z",
        "signer_wallet": USER_WALLET,
        "delegator": ATTACKER_WALLET,
        "signature": "signed",
    }
    try:
        shopping_server._handle_intent(
            {"kind": "message", "messageId": "message"},
            {"ap2.mandates.IntentMandate": intent},
        )
    except ValueError as exc:
        assert "delegator does not match" in str(exc)
    else:
        raise AssertionError("A2A must reject a mismatched human delegator")


def test_a2a_human_intent_requires_valid_wallet_signature(
    monkeypatch,
) -> None:
    intent = {
        "user_cart_confirmation_required": False,
        "natural_language_description": "earbuds",
        "requires_refundability": False,
        "price_ceiling": {"amount": "5.00", "currency": "USDC"},
        "ship_to": "Seoul",
        "intent_expiry": "2099-01-01T00:00:00Z",
        "signer_wallet": USER_WALLET,
        "delegator": USER_WALLET,
        "signature": "forged",
    }
    monkeypatch.setattr(
        shopping_server, "verify_wallet_signature", lambda *_: False
    )

    def unexpected_quote(*_args, **_kwargs):
        raise AssertionError("invalid human A2A identity must fail before quote")

    monkeypatch.setattr(
        shopping_server.broker, "handle_quote", unexpected_quote
    )
    try:
        shopping_server._handle_intent(
            {"kind": "message", "messageId": "message"},
            {"ap2.mandates.IntentMandate": intent},
        )
    except ValueError as exc:
        assert "invalid human wallet signature" in str(exc)
    else:
        raise AssertionError("A2A must reject an unsigned human principal")
