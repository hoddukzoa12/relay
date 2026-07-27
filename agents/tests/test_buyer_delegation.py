from __future__ import annotations

import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from agentic_broker.buyer import auth, server, tools
from agentic_broker.common.contracts import (
    CART_MANDATE_DATA_KEY,
    INTENT_MANDATE_DATA_KEY,
)


class BuyerDelegationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(server.app)

    def test_console_root_is_not_served(self) -> None:
        response = self.client.get("/")

        self.assertEqual(response.status_code, 404)

    def test_agent_only_buy_stays_public(self) -> None:
        result = {"ok": True, "confirmation": {"status": "paid"}}
        with patch.object(server.flow, "buy", return_value=result) as buy:
            response = self.client.post(
                "/buy",
                json={"query": "earbuds", "budget": 5, "shipTo": "Seoul"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), result)
        self.assertIsNone(buy.call_args.kwargs["intent_mandate"])
        self.assertIsNone(buy.call_args.kwargs["identity_wallet"])

    def test_structured_address_takes_precedence_over_legacy_ship_to(self) -> None:
        result = {"ok": True, "confirmation": {"status": "paid"}}
        address = {
            "name": "Grace Hopper",
            "address1": "123 Main St",
            "city": "Arlington",
            "province": "VA",
            "country": "US",
            "zip": "22201",
        }
        with patch.object(server.flow, "buy", return_value=result) as buy:
            response = self.client.post(
                "/buy",
                json={
                    "query": "earbuds",
                    "budget": 5,
                    "shipTo": "ignored legacy destination",
                    "shippingAddress": address,
                },
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            buy.call_args.kwargs["ship_to"],
            "Grace Hopper, 123 Main St, Arlington, VA, 22201, US",
        )
        self.assertEqual(
            buy.call_args.kwargs["shipping_address"].model_dump(exclude_none=True),
            address,
        )

    def test_authenticated_intent_is_bound_to_clerk_wallet(self) -> None:
        wallet = "Bn7UDWnm59HtG4zeSS2gF2MWT8bADFDuVaG5Y95HLoFf"
        mandate = {
            "user_cart_confirmation_required": False,
            "natural_language_description": "earbuds",
            "requires_refundability": False,
            "price_ceiling": {"amount": "5.00", "currency": "USDC"},
            "ship_to": "Seoul",
            "intent_expiry": "2099-01-01T00:00:00Z",
            "signer_wallet": wallet,
            "signature": "signed-by-human",
        }
        result = {"ok": True, "confirmation": {"status": "paid"}}
        identity = auth.ClerkIdentity("user_test", "sess_test", wallet)
        with (
            patch.object(server.auth, "verify_session_token", return_value=identity),
            patch.object(server, "verify_wallet_signature", return_value=True),
            patch.object(server.flow, "buy", return_value=result) as buy,
        ):
            response = self.client.post(
                "/buy",
                headers={"Authorization": "Bearer clerk-session"},
                json={
                    "query": "earbuds",
                    "budget": 5,
                    "shipTo": "Seoul",
                    "intentMandate": mandate,
                },
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(buy.call_args.kwargs["identity_wallet"], wallet)
        self.assertEqual(
            buy.call_args.kwargs["intent_mandate"].signer_wallet, wallet
        )

    def test_authenticated_buy_without_mandate_uses_session_wallet(self) -> None:
        wallet = "Bn7UDWnm59HtG4zeSS2gF2MWT8bADFDuVaG5Y95HLoFf"
        identity = auth.ClerkIdentity("user_test", "sess_test", wallet)
        result = {"ok": True, "confirmation": {"status": "paid"}}
        with (
            patch.object(server.auth, "verify_session_token", return_value=identity),
            patch.object(server.flow, "buy", return_value=result) as buy,
        ):
            response = self.client.post(
                "/buy",
                headers={"Authorization": "Bearer clerk-session"},
                json={"query": "earbuds", "budget": 5, "shipTo": "Seoul"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(buy.call_args.kwargs["identity_wallet"], wallet)

    def test_authenticated_intent_cannot_select_another_delegator(self) -> None:
        wallet = "Bn7UDWnm59HtG4zeSS2gF2MWT8bADFDuVaG5Y95HLoFf"
        identity = auth.ClerkIdentity("user_test", "sess_test", wallet)
        mandate = {
            "user_cart_confirmation_required": False,
            "natural_language_description": "earbuds",
            "requires_refundability": False,
            "price_ceiling": {"amount": "5.00", "currency": "USDC"},
            "ship_to": "Seoul",
            "intent_expiry": "2099-01-01T00:00:00Z",
            "signer_wallet": wallet,
            "delegator": "attacker-wallet",
            "signature": "signed-by-human",
        }
        with (
            patch.object(server.auth, "verify_session_token", return_value=identity),
            patch.object(server.flow, "buy") as buy,
        ):
            response = self.client.post(
                "/buy",
                headers={"Authorization": "Bearer clerk-session"},
                json={
                    "query": "earbuds",
                    "budget": 5,
                    "shipTo": "Seoul",
                    "intentMandate": mandate,
                },
            )

        self.assertEqual(response.status_code, 403)
        self.assertIn("delegator does not match", response.json()["detail"])
        buy.assert_not_called()

    def test_delegated_intent_requires_a_session(self) -> None:
        mandate = {
            "user_cart_confirmation_required": False,
            "natural_language_description": "earbuds",
            "requires_refundability": False,
            "price_ceiling": {"amount": "5.00", "currency": "USDC"},
            "ship_to": "Seoul",
            "intent_expiry": "2099-01-01T00:00:00Z",
            "signer_wallet": "Bn7UDWnm59HtG4zeSS2gF2MWT8bADFDuVaG5Y95HLoFf",
            "signature": "signed-by-human",
        }
        response = self.client.post(
            "/buy",
            json={
                "query": "earbuds",
                "budget": 5,
                "shipTo": "Seoul",
                "intentMandate": mandate,
            },
        )
        self.assertEqual(response.status_code, 401)

    def test_web_purchase_requires_a_clerk_session(self) -> None:
        with patch.object(server.flow, "buy") as buy:
            response = self.client.post(
                "/web/buy",
                json={
                    "query": "earbuds",
                    "budget": 5,
                    "shipTo": "Seoul",
                    "approvalTxSignature": "approve-tx",
                },
            )

        self.assertEqual(response.status_code, 401)
        buy.assert_not_called()

    def test_web_purchase_context_uses_only_the_clerk_wallet(self) -> None:
        wallet = "Bn7UDWnm59HtG4zeSS2gF2MWT8bADFDuVaG5Y95HLoFf"
        identity = auth.ClerkIdentity("user_test", "sess_test", wallet)

        def delegated_buy(**kwargs):
            context = tools._STOREFRONT_CONTEXT.get()
            self.assertIsNotNone(context)
            self.assertEqual(context.identity_wallet, wallet)
            self.assertEqual(context.approval_tx_signature, "approve-tx")
            self.assertEqual(kwargs["identity_wallet"], wallet)
            return {"ok": True}

        with (
            patch.object(server.auth, "verify_session_token", return_value=identity),
            patch.object(server.flow, "buy", side_effect=delegated_buy) as buy,
        ):
            response = self.client.post(
                "/web/buy",
                headers={"Authorization": "Bearer clerk-session"},
                json={
                    "query": "earbuds",
                    "budget": 5,
                    "shipTo": "Seoul",
                    "approvalTxSignature": "approve-tx",
                    "delegator": "attacker-wallet",
                },
            )

        self.assertEqual(response.status_code, 200)
        buy.assert_called_once()

    def test_prepare_approve_uses_clerk_wallet_not_request_wallet(self) -> None:
        wallet = "Bn7UDWnm59HtG4zeSS2gF2MWT8bADFDuVaG5Y95HLoFf"
        identity = auth.ClerkIdentity("user_test", "sess_test", wallet)
        prepared = {
            "action": "approve",
            "delegator": wallet,
            "delegateAuthority": "agent-wallet",
            "allowanceRemaining": {"amount": "50", "currency": "USDC"},
            "transaction": "base64",
            "blockhash": "blockhash",
            "lastValidBlockHeight": 1,
        }
        with (
            patch.object(server.auth, "verify_session_token", return_value=identity),
            patch.object(
                server.service_clients,
                "payments_prepare_delegation",
                return_value=prepared,
            ) as prepare,
        ):
            response = self.client.post(
                "/delegation/transaction",
                headers={"Authorization": "Bearer clerk-session"},
                json={
                    "action": "approve",
                    "amount": "50",
                    "delegator": "attacker-wallet",
                },
            )

        self.assertEqual(response.status_code, 200)
        prepare.assert_called_once_with(wallet, "approve", "50")

    def test_storefront_payment_tools_reject_anonymous_sessions(self) -> None:
        with (
            tools.storefront_context(None, None),
            patch.object(tools.service_clients, "payments_sign_mandate") as sign,
        ):
            with self.assertRaisesRegex(PermissionError, "Clerk wallet sign-in"):
                tools.request_quote("earbuds", 5, "Seoul")
        sign.assert_not_called()

    def test_storefront_quote_rejects_budget_above_onchain_allowance(self) -> None:
        wallet = "Bn7UDWnm59HtG4zeSS2gF2MWT8bADFDuVaG5Y95HLoFf"
        status = {
            "active": True,
            "delegator": wallet,
            "delegateAuthority": "agent-wallet",
            "allowanceRemaining": {"amount": "4.99", "currency": "USDC"},
        }
        with (
            tools.storefront_context(wallet, "approve-tx"),
            patch.object(
                tools.service_clients,
                "payments_verify_delegation",
                return_value=status,
            ),
            patch.object(tools.service_clients, "payments_sign_mandate") as sign,
        ):
            with self.assertRaisesRegex(PermissionError, "exceeds.*allowance"):
                tools.request_quote("earbuds", 5, "Seoul")
        sign.assert_not_called()

    def test_payment_uses_delegator_as_source_and_mandate_payer(self) -> None:
        wallet = "Bn7UDWnm59HtG4zeSS2gF2MWT8bADFDuVaG5Y95HLoFf"
        mandates = {
            INTENT_MANDATE_DATA_KEY: {
                "delegator": wallet,
                "ship_to": "Seoul",
                "signature": "intent-signature",
            },
            CART_MANDATE_DATA_KEY: {
                "contents": {
                    "id": "ord_1",
                    "merchant_name": "Relay",
                },
                "signature": "cart-signature",
            },
        }
        signed_payment: dict = {}

        def sign_mandate(mandate, signer):
            signed_payment.update(mandate)
            return {"signature": "payment-signature", "publicKey": "agent"}

        with (
            patch.object(
                tools.service_clients,
                "payments_wallets",
                return_value={"buyer": "agent-wallet"},
            ),
            patch.object(
                tools.service_clients,
                "payments_sign_mandate",
                side_effect=sign_mandate,
            ),
            patch.object(
                tools.service_clients,
                "payments_pay",
                return_value={"txSignature": "tx", "explorer": "explorer"},
            ) as pay,
        ):
            result = tools.authorize_payment(
                "merchant", "1.00", "reference", mandates
            )

        pay.assert_called_once_with(
            "merchant", "1.00", "reference", delegator=wallet
        )
        self.assertEqual(
            signed_payment["payment_mandate_contents"]["payer"][
                "wallet_address"
            ],
            wallet,
        )
        self.assertEqual(result["txSignature"], "tx")

    def test_deterministic_flow_threads_verified_identity_to_payment(self) -> None:
        wallet = "Bn7UDWnm59HtG4zeSS2gF2MWT8bADFDuVaG5Y95HLoFf"
        quote = {
            "payTo": "merchant",
            "price": {"amount": "1.00", "currency": "USDC"},
            "reference": "reference",
            "orderRef": "ord_1",
            "ap2Mandates": {"intent": {"signature": "intent"}},
        }
        with (
            patch.object(server.flow.tools, "request_quote", return_value=quote) as quote_call,
            patch.object(
                server.flow.tools,
                "authorize_payment",
                return_value={"txSignature": "tx"},
            ) as pay,
            patch.object(
                server.flow.tools,
                "confirm_settlement",
                return_value={"status": "paid"},
            ),
        ):
            result = server.flow.buy(
                "earbuds",
                5,
                "Seoul",
                identity_wallet=wallet,
            )

        self.assertTrue(result["ok"])
        self.assertEqual(quote_call.call_args.kwargs["delegator"], wallet)
        self.assertEqual(pay.call_args.kwargs["delegator"], wallet)

    def test_deterministic_cli_flow_keeps_agent_wallet(self) -> None:
        quote = {
            "payTo": "merchant",
            "price": {"amount": "1.00", "currency": "USDC"},
            "reference": "reference",
            "orderRef": "ord_1",
        }
        with (
            patch.object(server.flow.tools, "request_quote", return_value=quote) as quote_call,
            patch.object(
                server.flow.tools,
                "authorize_payment",
                return_value={"txSignature": "tx"},
            ) as pay,
            patch.object(
                server.flow.tools,
                "confirm_settlement",
                return_value={"status": "paid"},
            ),
        ):
            result = server.flow.buy("earbuds", 5, "Seoul")

        self.assertTrue(result["ok"])
        self.assertIsNone(quote_call.call_args.kwargs["delegator"])
        self.assertIsNone(pay.call_args.kwargs["delegator"])


if __name__ == "__main__":
    unittest.main()
