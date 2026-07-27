from __future__ import annotations

import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from agentic_broker.common import service_clients
from agentic_broker.shopping import server
from agentic_broker.shopping import tools


def order_status(
    *,
    financial_status: str = "PAID",
    refund_signature: str | None = None,
) -> dict:
    return {
        "shopifyOrderId": "gid://shopify/Order/1006",
        "orderRef": "ord_123",
        "name": "#1006",
        "financialStatus": financial_status,
        "fulfillmentStatus": "UNFULFILLED",
        "lineItems": [
            {"title": "Wireless Earbuds", "sku": "RELAY-EARBUDS", "quantity": 1}
        ],
        "amount": {"amount": "1.00", "currency": "USDC"},
        "payment": {
            "reference": "payment-reference",
            "txSignature": "payment-signature",
            "explorer": "https://explorer.test/payment-signature",
        },
        "refund": {
            "status": "refunded" if refund_signature else "not_refunded",
            "reference": "refund-reference" if refund_signature else None,
            "txSignature": refund_signature,
            "explorer": (
                "https://explorer.test/refund-signature"
                if refund_signature
                else None
            ),
        },
        "supplierOrder": {
            "provider": "dsers",
            "status": "disabled",
            "ref": None,
            "message": "Supplier fulfillment is disabled.",
        },
        "tracking": None,
    }


class OrderLifecycleToolsTest(unittest.TestCase):
    def test_get_order_status_is_a_reusable_agent_primitive(self) -> None:
        with patch.object(
            tools.service_clients,
            "commerce_order",
            return_value=order_status(),
        ) as lookup:
            result = tools.get_order_status("#1006")

        lookup.assert_called_once_with("#1006")
        self.assertEqual(result["lineItems"][0]["sku"], "RELAY-EARBUDS")
        self.assertEqual(result["payment"]["txSignature"], "payment-signature")
        self.assertEqual(result["supplierOrder"]["status"], "disabled")
        self.assertIsNone(result["supplierOrder"]["ref"])

    def test_refund_orchestrates_onchain_before_shopify_and_replays(self) -> None:
        refunded = order_status(
            financial_status="REFUNDED", refund_signature="refund-signature"
        )
        with (
            patch.object(
                tools.service_clients,
                "commerce_order",
                return_value=order_status(),
            ),
            patch.object(
                tools.service_clients,
                "payments_refund",
                return_value={
                    "status": "refunded",
                    "refundReference": "refund-reference",
                    "refundTxSignature": "refund-signature",
                    "refundExplorer": "https://explorer.test/refund-signature",
                    "replayed": False,
                },
            ) as payment_refund,
            patch.object(
                tools.service_clients,
                "commerce_refund_order",
                return_value=refunded,
            ) as shopify_refund,
        ):
            result = tools.refund_order("ord_123")

        payment_refund.assert_called_once_with("ord_123", "payment-reference")
        shopify_refund.assert_called_once_with(
            "ord_123",
            "refund-reference",
            "refund-signature",
            "https://explorer.test/refund-signature",
        )
        self.assertEqual(result["financialStatus"], "REFUNDED")
        self.assertEqual(result["refund"]["txSignature"], "refund-signature")
        self.assertFalse(result["replayed"])

        with (
            patch.object(
                tools.service_clients,
                "commerce_order",
                return_value=refunded,
            ),
            patch.object(tools.service_clients, "payments_refund") as replay_payment,
        ):
            replay = tools.refund_order("ord_123")
        replay_payment.assert_not_called()
        self.assertTrue(replay["replayed"])
        self.assertEqual(replay["refund"]["txSignature"], "refund-signature")

    def test_refund_rejects_order_without_recorded_payment_reference(self) -> None:
        legacy = order_status()
        legacy["payment"]["reference"] = None
        with patch.object(
            tools.service_clients,
            "commerce_order",
            return_value=legacy,
        ):
            with self.assertRaisesRegex(ValueError, "predates recorded"):
                tools.refund_order("ord_123")

    def test_supplier_refusals_preserve_http_409_through_shopping_agent(self) -> None:
        refusal = service_clients.ServiceRequestError(
            409,
            "Supplier fulfillment is disabled.",
        )
        client = TestClient(server.app)
        with (
            patch.object(tools, "fulfill_order", side_effect=refusal),
            patch.object(tools, "track_order", side_effect=refusal),
        ):
            fulfill = client.post("/orders/ord_123/fulfill")
            tracking = client.get("/orders/ord_123/tracking")

        self.assertEqual(fulfill.status_code, 409)
        self.assertEqual(tracking.status_code, 409)
        self.assertIn("fulfillment is disabled", fulfill.json()["detail"])
        self.assertIn("fulfillment is disabled", tracking.json()["detail"])


if __name__ == "__main__":
    unittest.main()
