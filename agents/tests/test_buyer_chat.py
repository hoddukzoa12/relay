from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from fastapi.testclient import TestClient

from agentic_broker.buyer import agent as buyer_agent
from agentic_broker.buyer import conversation, server
from agentic_broker.buyer.agent import root_agent


USER_WALLET = "Bn7UDWnm59HtG4zeSS2gF2MWT8bADFDuVaG5Y95HLoFf"


def _chat_tool_context(
    *,
    wallet: str = USER_WALLET,
    approval_signature: str = "approve-tx",
    state: dict | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        state={
            "relay:chat_request": True,
            "relay:chat_identity_wallet": wallet,
            "relay:chat_approval_tx_signature": approval_signature,
            **(state or {}),
        }
    )


def test_chat_endpoint_preserves_caller_session_id() -> None:
    client = TestClient(server.app)
    expected = {
        "sessionId": "session_12345678",
        "reply": "What is your budget?",
        "mode": "ai",
        "toolCalls": [],
        "products": [],
    }
    with patch.object(server.conversation, "respond", return_value=expected) as respond:
        response = client.post(
            "/chat",
            json={
                "sessionId": "session_12345678",
                "message": "I need earbuds",
            },
        )

    assert response.status_code == 200
    assert response.json() == expected
    respond.assert_called_once_with(
        "session_12345678",
        "I need earbuds",
        identity_wallet=None,
        approval_tx_signature=None,
    )


def test_chat_validates_bearer_before_trusting_its_wallet() -> None:
    client = TestClient(server.app)
    identity = server.auth.ClerkIdentity(
        "user_test", "session_test", USER_WALLET
    )
    expected = {
        "sessionId": "verified_12345678",
        "reply": "Ready.",
        "mode": "ai",
        "toolCalls": [],
        "products": [],
    }
    with (
        patch.object(
            server.auth, "verify_session_token", return_value=identity
        ) as verify,
        patch.object(
            server.conversation, "respond", return_value=expected
        ) as respond,
    ):
        response = client.post(
            "/chat",
            headers={"Authorization": "Bearer clerk-session"},
            json={
                "sessionId": "verified_12345678",
                "message": "Find earbuds",
                "approvalTxSignature": "approve-tx",
            },
        )

    assert response.status_code == 200
    verify.assert_called_once_with("clerk-session")
    respond.assert_called_once_with(
        "verified_12345678",
        "Find earbuds",
        identity_wallet=USER_WALLET,
        approval_tx_signature="approve-tx",
    )


def test_chat_rejects_invalid_bearer_before_running_agent() -> None:
    client = TestClient(server.app)
    with (
        patch.object(
            server.auth,
            "verify_session_token",
            side_effect=server.auth.AuthenticationError("invalid session"),
        ),
        patch.object(server.conversation, "respond") as respond,
    ):
        response = client.post(
            "/chat",
            headers={"Authorization": "Bearer invalid"},
            json={
                "sessionId": "invalid_12345678",
                "message": "Buy earbuds",
            },
        )

    assert response.status_code == 401
    assert response.json()["detail"] == "invalid session"
    respond.assert_not_called()


def test_chat_rejects_invalid_session_ids_without_running_agent() -> None:
    client = TestClient(server.app)
    with patch.object(server.conversation, "respond") as respond:
        response = client.post(
            "/chat",
            json={"sessionId": "../../shared", "message": "hello"},
        )

    assert response.status_code == 422
    respond.assert_not_called()


def test_buyer_agent_exposes_all_conversational_tools() -> None:
    names = {
        getattr(tool, "name", getattr(tool, "__name__", ""))
        for tool in root_agent.tools
    }
    assert names == {
        "search_catalog",
        "request_quote",
        "authorize_payment",
        "confirm_settlement",
        "get_order_status",
    }


def test_quote_tool_refuses_missing_structured_address_fields() -> None:
    context = _chat_tool_context()
    with patch.object(buyer_agent.buyer_tools, "request_quote") as quote:
        try:
            buyer_agent.request_quote(
                "Earbuds",
                5.0,
                "legacy free text",
                context,
                shipping_name="Grace Hopper",
                address1="123 Main St",
                city="Arlington",
                province="VA",
                country="US",
                zip_code="",
            )
        except ValueError as exc:
            assert "zip_code" in str(exc)
        else:
            raise AssertionError("missing ZIP must fail before quote")
    quote.assert_not_called()


def test_quote_tool_prefers_complete_structured_address() -> None:
    context = _chat_tool_context()
    returned = {
        "price": {"amount": "4.54", "currency": "USDC"},
        "orderRef": "ord_1",
    }
    with patch.object(
        buyer_agent.buyer_tools,
        "request_quote",
        return_value=returned,
    ) as quote:
        result = buyer_agent.request_quote(
            "Earbuds",
            5.0,
            "ignored legacy text",
            context,
            shipping_name="Grace Hopper",
            address1="123 Main St",
            city="Arlington",
            province="VA",
            country="us",
            zip_code="22201",
        )

    assert result == returned
    assert quote.call_args.args[2] == (
        "Grace Hopper, 123 Main St, Arlington, VA, 22201, US"
    )
    structured = quote.call_args.kwargs["shipping_address"]
    assert structured.country == "US"
    assert quote.call_args.kwargs["delegator"] == USER_WALLET
    assert context.state["relay:last_shipping_address"]["zip"] == "22201"


def test_anonymous_chat_tools_fail_closed_before_payment_services() -> None:
    context = _chat_tool_context(wallet="", approval_signature="")
    context.state.update(
        {
            "relay:last_budget": 5,
            "relay:last_quote": {
                "payTo": "merchant",
                "price": {"amount": "1.00"},
                "reference": "reference",
                "orderRef": "ord_1",
            },
            "relay:last_payment": {"txSignature": "tx"},
        }
    )
    with (
        patch.object(buyer_agent.buyer_tools, "request_quote") as quote,
        patch.object(buyer_agent.buyer_tools, "authorize_payment") as pay,
        patch.object(buyer_agent.buyer_tools, "confirm_settlement") as settle,
    ):
        quote_result = buyer_agent.request_quote(
            "Earbuds",
            5,
            "ignored",
            context,
            shipping_name="Grace Hopper",
            address1="123 Main St",
            city="Arlington",
            province="VA",
            country="US",
            zip_code="22201",
        )
        pay_result = buyer_agent.authorize_payment(
            "merchant", "1.00", "reference", context
        )
        settle_result = buyer_agent.confirm_settlement(
            "ord_1", "reference", "tx", context
        )

    for result in (quote_result, pay_result, settle_result):
        assert result["status"] == "auth-required"
        assert result["authRequired"] is True
        assert result["paymentBlocked"] is True
        assert result["action"] == "sign-in"
    quote.assert_not_called()
    pay.assert_not_called()
    settle.assert_not_called()


def test_chat_tool_state_binds_payment_to_verified_wallet() -> None:
    quote_result = {
        "payTo": "merchant",
        "price": {"amount": "1.00"},
        "reference": "reference",
        "orderRef": "ord_1",
    }
    context = _chat_tool_context(
        state={
            "relay:last_budget": 5,
            "relay:last_quote": quote_result,
        }
    )
    with patch.object(
        buyer_agent.buyer_tools,
        "authorize_payment",
        return_value={"txSignature": "tx"},
    ) as pay:
        result = buyer_agent.authorize_payment(
            "merchant", "1.00", "reference", context
        )

    assert result == {"txSignature": "tx"}
    pay.assert_called_once_with(
        "merchant",
        "1.00",
        "reference",
        None,
        delegator=USER_WALLET,
    )


def test_runner_state_clears_identity_for_every_anonymous_turn() -> None:
    service = conversation.ConversationService()
    captured: dict = {}

    def fake_run(**kwargs):
        captured.update(kwargs)
        return iter(())

    service._runner = SimpleNamespace(run=fake_run)
    trace = service._run_agent(
        "anonymous_state",
        "buy it",
        identity_wallet=None,
        approval_tx_signature=None,
    )

    assert trace.tool_calls == []
    assert captured["state_delta"] == {
        "relay:chat_request": True,
        "relay:chat_identity_wallet": "",
        "relay:chat_approval_tx_signature": "",
    }


def test_payment_gate_is_actionable_and_not_rendered_as_a_quote() -> None:
    service = conversation.ConversationService()
    gate = {
        "status": "auth-required",
        "authRequired": True,
        "paymentBlocked": True,
        "reason": "Sign in to buy.",
        "action": "sign-in",
    }
    response = service._response_from_trace(
        session_id="blocked_state",
        trace=conversation.TurnTrace(
            reply="Sign in to buy.",
            tool_calls=[{"name": "request_quote", "args": {}}],
            tool_results=[{"name": "request_quote", "result": gate}],
        ),
        mode="ai",
    )

    assert response["paymentBlocked"] is True
    assert response["authRequired"] is True
    assert response["paymentGate"] == gate
    assert "quote" not in response


def test_missing_gemini_key_uses_deterministic_catalog_fallback() -> None:
    service = conversation.ConversationService()
    catalog = {
        "query": "earbuds under 5 USDC",
        "budget": "5.0",
        "currency": "USDC",
        "products": [
            {
                "productId": "gid://shopify/Product/1",
                "variantId": "gid://shopify/ProductVariant/2",
                "sku": "EAR-1",
                "title": "Earbuds",
                "description": "",
                "catalogPrice": "2.00",
                "price": "2.30",
                "currency": "USDC",
                "inventoryQuantity": 7,
                "status": "ACTIVE",
                "tags": [],
            }
        ],
        "closestOverBudget": [],
    }
    fallback_settings = SimpleNamespace(
        google_api_key="",
        default_ship_to="Seoul",
    )
    with (
        patch.object(conversation, "settings", fallback_settings),
        patch.object(
            conversation.buyer_tools,
            "search_catalog",
            return_value=catalog,
        ) as search,
    ):
        response = service.respond("fallback_session", "earbuds under 5 USDC")

    assert response["mode"] == "fallback"
    assert response["products"][0]["inventoryQuantity"] == 7
    assert response["toolCalls"] == ["search_catalog"]
    search.assert_called_once_with("earbuds under 5 USDC", 5.0)


def test_anonymous_fallback_chat_can_search_but_cannot_buy() -> None:
    service = conversation.ConversationService()
    product = {
        "title": "Earbuds",
        "price": "2.30",
        "inventoryQuantity": 7,
    }
    catalog = {
        "query": "earbuds under 5 USDC",
        "budget": "5.0",
        "currency": "USDC",
        "products": [product],
        "closestOverBudget": [],
    }
    fallback_settings = SimpleNamespace(
        google_api_key="",
        default_ship_to="Seoul",
    )
    with (
        patch.object(conversation, "settings", fallback_settings),
        patch.object(
            conversation.buyer_tools,
            "search_catalog",
            return_value=catalog,
        ),
        patch.object(conversation.flow, "buy") as buy,
    ):
        searched = service.respond(
            "anonymous_session", "earbuds under 5 USDC"
        )
        address_required = service.respond("anonymous_session", "buy it")
        still_required = service.respond(
            "anonymous_session",
            "Name: Grace Hopper; Address1: 123 Main St",
        )
        blocked = service.respond(
            "anonymous_session",
            (
                "City: Arlington; Province: VA; Country: US; ZIP: 22201"
            ),
        )

    assert searched["products"] == [product]
    assert address_required["shippingAddressRequired"] is True
    assert "will not invent" in address_required["reply"]
    assert still_required["shippingAddressRequired"] is True
    assert "city" in still_required["reply"]
    assert blocked["paymentBlocked"] is True
    assert "Clerk session is required" in blocked["reply"]
    assert blocked["paymentGate"]["action"] == "sign-in"
    buy.assert_not_called()


def test_authenticated_fallback_chat_pays_only_from_verified_wallet() -> None:
    service = conversation.ConversationService()
    product = {
        "title": "Earbuds",
        "price": "2.30",
        "inventoryQuantity": 7,
    }
    catalog = {
        "query": "earbuds under 5 USDC",
        "budget": "5.0",
        "currency": "USDC",
        "products": [product],
        "closestOverBudget": [],
    }
    result = {
        "ok": True,
        "quote": {
            "title": "Earbuds",
            "price": {"amount": "2.30", "currency": "USDC"},
        },
        "confirmation": {
            "status": "paid",
            "shopifyOrderId": "gid://shopify/Order/3",
        },
    }
    fallback_settings = SimpleNamespace(
        google_api_key="",
        default_ship_to="Seoul",
    )
    with (
        patch.object(conversation, "settings", fallback_settings),
        patch.object(
            conversation.buyer_tools,
            "search_catalog",
            return_value=catalog,
        ),
        patch.object(
            conversation.flow, "buy", return_value=result
        ) as buy,
    ):
        service.respond(
            "verified_fallback",
            "earbuds under 5 USDC",
            identity_wallet=USER_WALLET,
            approval_tx_signature="approve-tx",
        )
        purchased = service.respond(
            "verified_fallback",
            (
                "buy it; Name: Grace Hopper; Address1: 123 Main St; "
                "City: Arlington; Province: VA; Country: US; ZIP: 22201"
            ),
            identity_wallet=USER_WALLET,
            approval_tx_signature="approve-tx",
        )

    assert purchased["ok"] is True
    buy.assert_called_once()
    assert buy.call_args.kwargs["identity_wallet"] == USER_WALLET
    assert buy.call_args.kwargs["shipping_address"].country == "US"


def test_partial_agent_failure_never_sends_payment_twice() -> None:
    service = conversation.ConversationService()
    quote = {
        "orderRef": "ord_1",
        "reference": "ref_1",
        "title": "Earbuds",
        "price": {"amount": "2.30", "currency": "USDC"},
        "ap2Mandates": {"intent": {"signature": "one"}},
    }
    payment = {
        "txSignature": "tx_1",
        "explorer": "https://explorer.solana.com/tx/tx_1?cluster=devnet",
        "ap2Mandates": {"payment": {"signature": "two"}},
    }
    trace = conversation.TurnTrace(
        tool_calls=[
            {"name": "request_quote", "args": {}},
            {"name": "authorize_payment", "args": {}},
        ],
        tool_results=[
            {"name": "request_quote", "result": quote},
            {"name": "authorize_payment", "result": payment},
        ],
    )
    confirmation = {
        "status": "paid",
        "shopifyOrderId": "gid://shopify/Order/3",
        "explorer": payment["explorer"],
    }
    configured = SimpleNamespace(
        google_api_key="configured",
        default_ship_to="Seoul",
    )
    with (
        patch.object(conversation, "settings", configured),
        patch.object(
            service,
            "_run_agent",
            side_effect=conversation.AgentTurnError(
                RuntimeError("model disconnected"), trace
            ),
        ),
        patch.object(
            conversation.buyer_tools,
            "confirm_settlement",
            return_value=confirmation,
        ) as settle,
        patch.object(conversation.flow, "buy") as buy,
    ):
        response = service.respond(
            "partial_session",
            "go ahead",
            identity_wallet=USER_WALLET,
            approval_tx_signature="approve-tx",
        )

    assert response["mode"] == "fallback"
    assert response["confirmation"]["status"] == "paid"
    settle.assert_called_once()
    buy.assert_not_called()


def test_anonymous_partial_turn_cannot_reach_settlement_recovery() -> None:
    service = conversation.ConversationService()
    trace = conversation.TurnTrace(
        tool_calls=[
            {"name": "request_quote", "args": {}},
            {"name": "authorize_payment", "args": {}},
        ],
        tool_results=[
            {
                "name": "request_quote",
                "result": {
                    "orderRef": "ord_1",
                    "reference": "ref_1",
                    "price": {"amount": "1.00"},
                },
            },
            {
                "name": "authorize_payment",
                "result": {"txSignature": "tx_1"},
            },
        ],
    )
    configured = SimpleNamespace(
        google_api_key="configured",
        default_ship_to="Seoul",
    )
    with (
        patch.object(conversation, "settings", configured),
        patch.object(
            service,
            "_run_agent",
            side_effect=conversation.AgentTurnError(
                RuntimeError("model disconnected"), trace
            ),
        ),
        patch.object(
            conversation.buyer_tools, "confirm_settlement"
        ) as settle,
    ):
        response = service.respond("anonymous_partial", "go ahead")

    assert response["paymentBlocked"] is True
    assert response["authRequired"] is True
    settle.assert_not_called()
