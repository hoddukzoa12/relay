from __future__ import annotations

from starlette.testclient import TestClient

from agentic_broker.mcp import server


def test_mcp_transport_fails_closed_without_api_key() -> None:
    client = TestClient(
        server.create_app(
            api_key="", cors_origins="", oauth_enabled=False
        )
    )
    try:
        health = client.get("/health")
        response = client.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {
                    "name": "authorize_payment",
                    "arguments": {
                        "pay_to": "merchant",
                        "amount": "1.00",
                        "reference": "reference",
                    },
                },
            },
        )
    finally:
        client.close()

    assert health.json()["ok"] is False
    assert response.status_code == 503


def test_mcp_transport_rejects_missing_or_wrong_api_key() -> None:
    app = server.create_app(
        api_key="unit-test-secret",
        cors_origins="",
        oauth_enabled=False,
    )
    client = TestClient(app)
    try:
        health = client.get("/health")
        missing = client.post("/mcp", json={"jsonrpc": "2.0"})
        wrong = client.post(
            "/mcp",
            headers={"X-Relay-API-Key": "wrong"},
            json={"jsonrpc": "2.0"},
        )
    finally:
        client.close()

    assert health.json()["ok"] is True
    assert missing.status_code == 401
    assert wrong.status_code == 401
    assert (
        missing.headers["www-authenticate"]
        == 'RelayApiKey realm="relay-mcp"'
    )


def test_mcp_transport_rejects_unapproved_host_after_authentication() -> None:
    app = server.create_app(
        api_key="unit-test-secret",
        cors_origins="",
        oauth_enabled=False,
    )
    with TestClient(app) as client:
        response = client.post(
            "/mcp",
            headers={
                "Host": "attacker.example",
                "X-Relay-API-Key": "unit-test-secret",
            },
            json={"jsonrpc": "2.0"},
        )

    assert response.status_code == 421


def test_mcp_tools_are_thin_service_wrappers(monkeypatch) -> None:
    calls: list[tuple[str, tuple[object, ...]]] = []

    def result(name: str):
        def fake(*args, **kwargs):
            calls.append((name, (*args, kwargs)))
            return {"tool": name}

        return fake

    monkeypatch.setattr(
        server.service_clients, "commerce_products", result("search_products")
    )
    monkeypatch.setattr(server.buyer_tools, "request_quote", result("request_quote"))
    monkeypatch.setattr(
        server.buyer_tools, "authorize_payment", result("authorize_payment")
    )
    monkeypatch.setattr(server.service_clients, "a2a_settle", result("settle"))
    monkeypatch.setattr(
        server.service_clients, "shopping_order", result("get_order_status")
    )
    monkeypatch.setattr(
        server.service_clients, "shopping_refund_order", result("refund_order")
    )
    monkeypatch.setattr(
        server.service_clients, "buyer_wallet_balances", result("wallet_balances")
    )

    assert server.search_products("earbuds", 5) == {"tool": "search_products"}
    assert server.request_quote("earbuds", 5.0, "Seoul") == {
        "tool": "request_quote"
    }
    assert server.authorize_payment("merchant", "3.45", "reference") == {
        "tool": "authorize_payment"
    }
    assert server.settle("ord_1", "reference", "signature") == {"tool": "settle"}
    assert server.get_order_status("ord_1") == {"tool": "get_order_status"}
    assert server.refund_order("ord_1") == {"tool": "refund_order"}
    assert server.wallet_balances() == {"tool": "wallet_balances"}

    assert calls == [
        ("search_products", ("earbuds", 5, {})),
        (
            "request_quote",
            ("earbuds", 5.0, "Seoul", {"delegator": None}),
        ),
        (
            "authorize_payment",
            ("merchant", "3.45", "reference", {"delegator": None}),
        ),
        (
            "settle",
            (
                {
                    "orderRef": "ord_1",
                    "reference": "reference",
                    "txSignature": "signature",
                },
                {"identity_wallet": None},
            ),
        ),
        ("get_order_status", ("ord_1", {})),
        ("refund_order", ("ord_1", {})),
        ("wallet_balances", ({},)),
    ]


def test_search_products_rejects_unbounded_limits() -> None:
    for limit in (0, 51):
        try:
            server.search_products("earbuds", limit)
        except ValueError as exc:
            assert str(exc) == "limit must be between 1 and 50"
        else:
            raise AssertionError(f"limit={limit} should be rejected")
