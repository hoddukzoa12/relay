from __future__ import annotations

import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from agentic_broker.buyer import auth, server


class BuyerDelegationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(server.app)

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


if __name__ == "__main__":
    unittest.main()
