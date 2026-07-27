#!/usr/bin/env python3
"""Exercise /web/buy locally with a test-only Clerk verifier replacement.

The wallet address still enters the application through the verified identity
object, never the request body. Use only against the local service stack after
the devnet helper has submitted a real SPL Approve transaction.
"""
from __future__ import annotations

import argparse
import json
from unittest.mock import patch

from fastapi.testclient import TestClient

from agentic_broker.buyer import auth, server


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--wallet", required=True)
    parser.add_argument("--approval-signature", required=True)
    parser.add_argument("--query", default="wireless earbuds")
    parser.add_argument("--budget", type=float, default=2.5)
    parser.add_argument("--ship-to", default="Google Startup Campus, Seoul, KR")
    parser.add_argument(
        "--expect-blocked",
        action="store_true",
        help="Require the authenticated request to be rejected with HTTP 409.",
    )
    args = parser.parse_args()

    client = TestClient(server.app)
    unauthenticated = client.post(
        "/web/buy",
        json={
            "query": args.query,
            "budget": args.budget,
            "shipTo": args.ship_to,
            "approvalTxSignature": args.approval_signature,
        },
    )
    if unauthenticated.status_code != 401:
        raise RuntimeError(
            f"Expected unauthenticated web buy to return 401, got "
            f"{unauthenticated.status_code}: {unauthenticated.text}"
        )

    identity = auth.ClerkIdentity(
        user_id="issue_42_devnet",
        session_id="issue_42_devnet",
        wallet_address=args.wallet,
    )
    with patch.object(server.auth, "verify_session_token", return_value=identity):
        response = client.post(
            "/web/buy",
            headers={"Authorization": "Bearer local-test-clerk-session"},
            json={
                "query": args.query,
                "budget": args.budget,
                "shipTo": args.ship_to,
                "approvalTxSignature": args.approval_signature,
            },
        )
    payload = response.json()
    print(
        json.dumps(
            {
                "unauthenticatedStatus": unauthenticated.status_code,
                "authenticatedStatus": response.status_code,
                "result": payload,
            },
            indent=2,
        )
    )
    if args.expect_blocked:
        if response.status_code != 409:
            raise RuntimeError(
                f"Expected delegated web purchase to be blocked, got "
                f"{response.status_code}"
            )
        return
    if response.status_code != 200 or not payload.get("ok"):
        raise RuntimeError("Delegated web purchase did not complete")


if __name__ == "__main__":
    main()
