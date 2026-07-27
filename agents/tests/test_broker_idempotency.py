from __future__ import annotations

import unittest
from unittest.mock import patch

from agentic_broker.common.contracts import (
    Money,
    PurchaseIntent,
    SettlementRequest,
    StructuredShippingAddress,
)
from agentic_broker.shopping import broker
from agentic_broker.shopping import tools as shopping_tools


class BrokerIdempotencyTest(unittest.TestCase):
    def setUp(self) -> None:
        with broker._orders_lock:
            broker._orders.clear()

    def _quote(self):
        intent = PurchaseIntent(
            query="test product",
            budget=Money(amount="5.00", currency="USDC"),
            shipTo="Grace Hopper, 123 Main St, Arlington, VA, 22201, US",
            shippingAddress=StructuredShippingAddress(
                name="Grace Hopper",
                address1="123 Main St",
                city="Arlington",
                province="VA",
                country="US",
                zip="22201",
            ),
        )

        def issue_payment_request(
            product_id: str,
            title: str,
            price: float,
            order_ref: str,
        ):
            return {
                "productId": product_id,
                "title": title,
                "price": {"amount": f"{price:.2f}", "currency": "USDC"},
                "payTo": "merchant",
                "reference": f"reference_{order_ref}",
                "orderRef": order_ref,
                "network": "solana-devnet",
                "expiresAt": "2099-01-01T00:00:00Z",
            }

        with (
            patch.object(
                broker.tools,
                "source_and_price",
                return_value={
                    "productId": "gid://shopify/Product/1",
                    "variantId": "gid://shopify/ProductVariant/1",
                    "sku": "RELAY-TEST-1",
                    "title": "Test Product",
                    "cost": 1.0,
                    "supplierCost": {
                        "amount": "1.00",
                        "currency": "USD",
                        "source": "dsers_mcp_snapshot",
                        "capturedAt": "2026-07-27",
                        "shipTo": "US",
                        "supplierUrl": "https://supplier.test/item/1",
                    },
                    "price": 1.15,
                    "inventoryQuantity": 10,
                    "overBudget": False,
                },
            ),
            patch.object(
                broker.tools,
                "issue_payment_request",
                side_effect=issue_payment_request,
            ),
        ):
            quote = broker.handle_quote(intent)
        self.assertEqual(
            quote.productId, "gid://shopify/ProductVariant/1"
        )
        self.assertEqual(
            broker.catalog_identity(quote.orderRef),
            {
                "sku": "RELAY-TEST-1",
                "variantId": "gid://shopify/ProductVariant/1",
            },
        )
        return quote

    def test_settle_replay_returns_original_confirmation(self) -> None:
        quote = self._quote()
        settlement = SettlementRequest(
            orderRef=quote.orderRef,
            reference=quote.reference,
            txSignature="tx_signature",
        )
        verification = {
            "status": "paid",
            "txSignature": "tx_signature",
            "explorer": "https://explorer.test/tx_signature",
            "amount": "1.15",
            "reason": None,
        }

        with (
            patch.object(broker.tools, "verify_payment", return_value=verification),
            patch.object(
                broker.tools,
                "record_order",
                return_value={"shopifyOrderId": "shopify-order-1"},
            ) as record_order,
            patch.object(
                broker.service_clients,
                "payments_wallets",
                return_value={"buyer": "buyer-wallet"},
            ),
        ):
            first = broker.handle_settle(settlement)
            second = broker.handle_settle(settlement)

        self.assertEqual(first, second)
        self.assertEqual(first.shopifyOrderId, "shopify-order-1")
        record_order.assert_called_once()
        self.assertEqual(
            record_order.call_args.kwargs["product_id"],
            "gid://shopify/ProductVariant/1",
        )
        self.assertEqual(
            record_order.call_args.kwargs["sku"],
            "RELAY-TEST-1",
        )
        self.assertEqual(
            record_order.call_args.kwargs["supplier_cost"]["amount"],
            "1.00",
        )
        self.assertEqual(
            record_order.call_args.kwargs["shipping_address"]["country"],
            "US",
        )
        self.assertFalse(record_order.call_args.kwargs["human_customer"])
        self.assertIsNone(record_order.call_args.kwargs["customer_email"])
        self.assertEqual(first.supplierOrder.status, "disabled")
        self.assertIsNone(first.supplierOrder.ref)

    def test_paid_state_survives_shopify_ledger_failure_and_retry(self) -> None:
        quote = self._quote()
        settlement = SettlementRequest(
            orderRef=quote.orderRef,
            reference=quote.reference,
            txSignature="tx_signature",
        )
        verification = {
            "status": "paid",
            "txSignature": "tx_signature",
            "explorer": "https://explorer.test/tx_signature",
            "amount": "1.15",
            "reason": None,
        }

        with (
            patch.object(
                broker.tools,
                "verify_payment",
                return_value=verification,
            ) as verify_payment,
            patch.object(
                broker.tools,
                "record_order",
                side_effect=[
                    RuntimeError("Shopify unavailable"),
                    {"shopifyOrderId": "shopify-order-1"},
                ],
            ),
            patch.object(
                broker.service_clients,
                "payments_wallets",
                return_value={"buyer": "buyer-wallet"},
            ),
        ):
            with self.assertRaisesRegex(
                broker.OrderRecordingPendingError,
                "paid on-chain.*remains pending",
            ):
                broker.handle_settle(settlement)

            state = broker._orders[quote.orderRef]
            self.assertEqual(state.payment_status, "paid")
            self.assertEqual(state.ledger_status, "pending")
            self.assertEqual(state.paid_tx_signature, "tx_signature")

            confirmation = broker.handle_settle(settlement)

        self.assertEqual(confirmation.status, "paid")
        self.assertEqual(confirmation.shopifyOrderId, "shopify-order-1")
        verify_payment.assert_called_once()

    def test_human_identity_wallet_owns_order_but_agent_pays(self) -> None:
        quote = self._quote()
        settlement = SettlementRequest(
            orderRef=quote.orderRef,
            reference=quote.reference,
            txSignature="tx_signature",
        )
        verification = {
            "status": "paid",
            "txSignature": "tx_signature",
            "explorer": "https://explorer.test/tx_signature",
            "amount": "1.15",
            "reason": None,
        }
        with (
            patch.object(broker.tools, "verify_payment", return_value=verification),
            patch.object(
                broker.tools,
                "record_order",
                return_value={
                    "shopifyOrderId": "shopify-order-1",
                    "customerAssociation": {
                        "status": "linked",
                        "customerId": "gid://shopify/Customer/1",
                        "message": "Linked from verified Clerk email.",
                    },
                },
            ) as record_order,
            patch.object(broker.service_clients, "payments_wallets") as wallets,
        ):
            confirmation = broker.handle_settle(
                settlement,
                identity_wallet="human-wallet",
                human_customer=True,
                customer_email="verified@example.com",
            )

        self.assertEqual(confirmation.status, "paid")
        self.assertEqual(
            record_order.call_args.kwargs["buyer_address"], "human-wallet"
        )
        self.assertTrue(record_order.call_args.kwargs["human_customer"])
        self.assertEqual(
            record_order.call_args.kwargs["customer_email"],
            "verified@example.com",
        )
        self.assertEqual(
            confirmation.customerAssociation.customerId,
            "gid://shopify/Customer/1",
        )
        wallets.assert_not_called()

    def test_agent_order_payload_omits_customer_identity(self) -> None:
        with patch.object(
            shopping_tools.service_clients,
            "commerce_create_order",
            return_value={"shopifyOrderId": "shopify-order-1"},
        ) as create_order:
            shopping_tools.record_order(
                order_ref="ord_agent",
                product_id="gid://shopify/ProductVariant/1",
                sku="SKU-1",
                title="Agent Product",
                amount="1.00",
                buyer_address="agent-wallet",
                ship_to="Seoul",
                payment_reference="reference",
                tx_signature="signature",
                explorer="https://explorer.test/signature",
            )

        self.assertNotIn(
            "customerEmail",
            create_order.call_args.args[0],
        )

    def test_settlement_retry_cannot_change_bound_owner(self) -> None:
        quote = self._quote()
        settlement = SettlementRequest(
            orderRef=quote.orderRef,
            reference=quote.reference,
            txSignature="tx_signature",
        )
        verification = {
            "status": "paid",
            "txSignature": "tx_signature",
            "explorer": "https://explorer.test/tx_signature",
            "amount": "1.15",
            "reason": None,
        }
        with (
            patch.object(
                broker.tools, "verify_payment", return_value=verification
            ),
            patch.object(
                broker.tools,
                "record_order",
                side_effect=RuntimeError("Shopify unavailable"),
            ),
        ):
            with self.assertRaises(broker.OrderRecordingPendingError):
                broker.handle_settle(
                    settlement, identity_wallet="first-wallet"
                )

            retry = broker.handle_settle(
                settlement, identity_wallet="different-wallet"
            )

        self.assertEqual(retry.status, "invalid")
        self.assertEqual(
            broker._orders[quote.orderRef].buyer_wallet, "first-wallet"
        )

    def test_order_refs_are_uuid_based_and_unique(self) -> None:
        first = self._quote().orderRef
        second = self._quote().orderRef

        self.assertRegex(first, r"^ord_[0-9a-f]{32}$")
        self.assertNotEqual(first, second)


if __name__ == "__main__":
    unittest.main()
