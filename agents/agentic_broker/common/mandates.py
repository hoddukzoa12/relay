"""Canonical AP2 mandate signing helpers shared by the Python agents."""
from __future__ import annotations

import base64
import json
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey


def canonical_mandate_json(mandate: dict[str, Any]) -> str:
    """Match services/payments canonicalMandateJson for JSON-only values."""
    unsigned = {key: value for key, value in mandate.items() if key != "signature"}
    return json.dumps(
        unsigned,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _base58_decode(value: str) -> bytes:
    alphabet = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
    number = 0
    for character in value:
        try:
            digit = alphabet.index(character)
        except ValueError as exc:
            raise ValueError("wallet address is not valid base58") from exc
        number = number * 58 + digit
    decoded = number.to_bytes((number.bit_length() + 7) // 8, "big") if number else b""
    return b"\0" * (len(value) - len(value.lstrip("1"))) + decoded


def verify_wallet_signature(
    mandate: dict[str, Any], signature: str, wallet_address: str
) -> bool:
    """Verify a base64url ed25519 signature from a Solana wallet."""
    try:
        public_key = _base58_decode(wallet_address)
        if len(public_key) != 32:
            return False
        padded = signature + "=" * (-len(signature) % 4)
        signature_bytes = base64.urlsafe_b64decode(padded)
        Ed25519PublicKey.from_public_bytes(public_key).verify(
            signature_bytes,
            canonical_mandate_json(mandate).encode("utf-8"),
        )
        return True
    except (InvalidSignature, ValueError):
        return False
