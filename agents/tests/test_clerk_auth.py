from __future__ import annotations

import base64
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519, rsa

from agentic_broker.buyer import auth
from agentic_broker.common.mandates import (
    canonical_mandate_json,
    verify_wallet_signature,
)


def _base58_encode(value: bytes) -> str:
    alphabet = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
    number = int.from_bytes(value, "big")
    encoded = ""
    while number:
        number, digit = divmod(number, 58)
        encoded = alphabet[digit] + encoded
    return "1" * (len(value) - len(value.lstrip(b"\0"))) + encoded


class _SigningKey:
    def __init__(self, key):
        self.key = key


class _JwksClient:
    def __init__(self, key):
        self.key = key

    def get_signing_key_from_jwt(self, _token: str) -> _SigningKey:
        return _SigningKey(self.key)


class ClerkAuthTest(unittest.TestCase):
    def setUp(self) -> None:
        self.private_key = rsa.generate_private_key(
            public_exponent=65537, key_size=2048
        )
        self.issuer = "https://clerk.test"
        self.settings = SimpleNamespace(
            clerk_issuer=self.issuer,
            clerk_jwks_url=f"{self.issuer}/.well-known/jwks.json",
            clerk_secret_key="test-secret",
            clerk_api_url="https://api.clerk.test",
        )

    def _token(self, *, expires: datetime, key=None) -> str:
        return jwt.encode(
            {
                "sub": "user_test",
                "sid": "sess_test",
                "iss": self.issuer,
                "exp": expires,
            },
            key or self.private_key,
            algorithm="RS256",
            headers={"kid": "test-key"},
        )

    def _verify(self, token: str) -> auth.ClerkIdentity:
        user = {
            "web3_wallets": [
                {
                    "web3_wallet": "Bn7UDWnm59HtG4zeSS2gF2MWT8bADFDuVaG5Y95HLoFf",
                    "verification": {"strategy": "web3_solana_signature"},
                }
            ]
        }
        with (
            patch.object(auth, "settings", self.settings),
            patch.object(
                auth,
                "_jwks_client",
                return_value=_JwksClient(self.private_key.public_key()),
            ),
            patch.object(auth, "_fetch_clerk_user", return_value=user),
        ):
            return auth.verify_session_token(token)

    def test_valid_session_resolves_solana_wallet(self) -> None:
        identity = self._verify(
            self._token(expires=datetime.now(timezone.utc) + timedelta(minutes=5))
        )
        self.assertEqual(identity.user_id, "user_test")
        self.assertEqual(identity.session_id, "sess_test")
        self.assertEqual(
            identity.wallet_address,
            "Bn7UDWnm59HtG4zeSS2gF2MWT8bADFDuVaG5Y95HLoFf",
        )

    def test_expired_session_is_rejected(self) -> None:
        with self.assertRaisesRegex(
            auth.AuthenticationError, "Invalid or expired"
        ):
            self._verify(
                self._token(
                    expires=datetime.now(timezone.utc) - timedelta(minutes=1)
                )
            )

    def test_invalid_signature_is_rejected(self) -> None:
        other_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        token = self._token(
            expires=datetime.now(timezone.utc) + timedelta(minutes=5),
            key=other_key,
        )
        with self.assertRaisesRegex(
            auth.AuthenticationError, "Invalid or expired"
        ):
            self._verify(token)

    def test_solana_intent_signature_matches_payments_canonicalization(self) -> None:
        private_key = ed25519.Ed25519PrivateKey.generate()
        public_key = private_key.public_key().public_bytes(
            serialization.Encoding.Raw,
            serialization.PublicFormat.Raw,
        )
        mandate = {
            "user_cart_confirmation_required": False,
            "natural_language_description": "wireless earbuds",
            "requires_refundability": False,
            "price_ceiling": {"amount": "5.00", "currency": "USDC"},
            "ship_to": "Seoul",
            "intent_expiry": "2099-01-01T00:00:00Z",
            "signer_wallet": _base58_encode(public_key),
        }
        signature = base64.urlsafe_b64encode(
            private_key.sign(canonical_mandate_json(mandate).encode())
        ).decode().rstrip("=")
        signed = {**mandate, "signature": signature}

        self.assertTrue(
            verify_wallet_signature(
                signed, signature, mandate["signer_wallet"]
            )
        )
        self.assertFalse(
            verify_wallet_signature(
                {**signed, "ship_to": "Busan"},
                signature,
                mandate["signer_wallet"],
            )
        )


if __name__ == "__main__":
    unittest.main()
