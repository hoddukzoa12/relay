from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from fastapi.testclient import TestClient

from agentic_broker.buyer import conversation, server
from agentic_broker.buyer.agent import root_agent


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
    respond.assert_called_once_with("session_12345678", "I need earbuds")


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
        response = service.respond("partial_session", "go ahead")

    assert response["mode"] == "fallback"
    assert response["confirmation"]["status"] == "paid"
    settle.assert_called_once()
    buy.assert_not_called()
