"""Clerk session verification and Solana-wallet identity resolution."""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import Any
from urllib.parse import quote

import httpx
import jwt

from ..common.config import settings


class AuthenticationError(ValueError):
    """The request did not contain a valid Clerk identity."""


@dataclass(frozen=True)
class ClerkIdentity:
    user_id: str
    session_id: str
    wallet_address: str


@lru_cache(maxsize=1)
def _jwks_client() -> jwt.PyJWKClient:
    jwks_url = settings.clerk_jwks_url or (
        f"{settings.clerk_issuer}/.well-known/jwks.json"
        if settings.clerk_issuer
        else ""
    )
    if not jwks_url:
        raise AuthenticationError("Clerk JWKS is not configured")
    # PyJWKClient caches the JWKS and refreshes it only for a new/rotated kid.
    return jwt.PyJWKClient(jwks_url, cache_jwk_set=True, lifespan=300)


def _fetch_clerk_user(user_id: str) -> dict[str, Any]:
    if not settings.clerk_secret_key:
        raise AuthenticationError("Clerk user lookup is not configured")
    try:
        response = httpx.get(
            f"{settings.clerk_api_url}/v1/users/{quote(user_id, safe='')}",
            headers={"Authorization": f"Bearer {settings.clerk_secret_key}"},
            timeout=10.0,
        )
    except httpx.HTTPError as exc:
        raise AuthenticationError("Clerk user lookup failed") from exc
    if response.status_code != 200:
        raise AuthenticationError("Clerk user lookup failed")
    payload = response.json()
    if not isinstance(payload, dict):
        raise AuthenticationError("Clerk returned an invalid user record")
    return payload


def _solana_wallet(user: dict[str, Any]) -> str:
    wallets = user.get("web3_wallets")
    if not isinstance(wallets, list):
        raise AuthenticationError("Clerk user has no Solana wallet")
    for wallet in wallets:
        if not isinstance(wallet, dict):
            continue
        verification = wallet.get("verification")
        strategy = (
            verification.get("strategy", "")
            if isinstance(verification, dict)
            else ""
        )
        address = wallet.get("web3_wallet")
        if (
            isinstance(address, str)
            and address
            and (not strategy or "solana" in str(strategy).lower())
        ):
            return address
    raise AuthenticationError("Clerk user has no verified Solana wallet")


def verify_session_token(token: str) -> ClerkIdentity:
    """Validate a Clerk JWT and resolve its verified Solana wallet."""
    if not token or not settings.clerk_issuer:
        raise AuthenticationError("Clerk session is not configured")
    try:
        signing_key = _jwks_client().get_signing_key_from_jwt(token)
        claims = jwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256"],
            issuer=settings.clerk_issuer,
            options={
                "require": ["exp", "iss", "sid", "sub"],
                "verify_exp": True,
                "verify_signature": True,
            },
        )
    except jwt.PyJWTError as exc:
        raise AuthenticationError("Invalid or expired Clerk session") from exc

    user_id = claims.get("sub")
    session_id = claims.get("sid", "")
    if not isinstance(user_id, str) or not user_id:
        raise AuthenticationError("Clerk session has no user")
    user = _fetch_clerk_user(user_id)
    return ClerkIdentity(
        user_id=user_id,
        session_id=session_id if isinstance(session_id, str) else "",
        wallet_address=_solana_wallet(user),
    )
