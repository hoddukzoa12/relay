#!/usr/bin/env python3
"""Exercise the local MCP transport with a server-verified OAuth wallet.

This is a local evidence helper, not an OAuth bypass for a deployed service.
It replaces only token verification while retaining the MCP authentication
middleware, caller context, A2A quote/settlement, and live devnet payments.
"""
from __future__ import annotations

import argparse
from decimal import Decimal
import json
import os
import time
from typing import Any

import httpx
from starlette.testclient import TestClient

from agentic_broker.common.config import settings
from agentic_broker.mcp import auth, server


def _rpc(method: str, params: list[Any]) -> Any:
    response = httpx.post(
        os.getenv("SOLANA_RPC_URL", "https://api.devnet.solana.com"),
        json={
            "jsonrpc": "2.0",
            "id": method,
            "method": method,
            "params": params,
        },
        timeout=30.0,
    )
    response.raise_for_status()
    payload = response.json()
    if payload.get("error"):
        raise RuntimeError(str(payload["error"]))
    return payload["result"]


def _token_balance(owner: str, mint: str) -> Decimal:
    result = _rpc(
        "getTokenAccountsByOwner",
        [
            owner,
            {"mint": mint},
            {"encoding": "jsonParsed", "commitment": "confirmed"},
        ],
    )
    total = Decimal("0")
    for entry in result["value"]:
        token_amount = (
            entry["account"]["data"]["parsed"]["info"]["tokenAmount"]
        )
        total += Decimal(token_amount["amount"]) / (
            Decimal(10) ** int(token_amount["decimals"])
        )
    return total


def _mcp_call(
    client: TestClient,
    name: str,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    response = client.post(
        "/mcp",
        headers={
            "Authorization": "Bearer local-test-oauth-token",
            "Accept": "application/json, text/event-stream",
        },
        json={
            "jsonrpc": "2.0",
            "id": name,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        },
    )
    response.raise_for_status()
    result = response.json()["result"]
    if result.get("isError"):
        messages = [
            block.get("text", str(block))
            for block in result.get("content", [])
        ]
        raise RuntimeError("; ".join(messages))
    structured = result.get("structuredContent")
    if isinstance(structured, dict):
        return structured
    for block in result.get("content", []):
        text = block.get("text")
        if text:
            parsed = json.loads(text)
            if isinstance(parsed, dict):
                return parsed
    raise RuntimeError(f"MCP tool {name} returned no structured object")


def _transaction_signers(signature: str) -> list[str]:
    transaction = _rpc(
        "getTransaction",
        [
            signature,
            {
                "commitment": "confirmed",
                "encoding": "jsonParsed",
                "maxSupportedTransactionVersion": 0,
            },
        ],
    )
    if not transaction:
        raise RuntimeError("confirmed payment transaction was not found")
    return [
        item["pubkey"]
        for item in transaction["transaction"]["message"]["accountKeys"]
        if item.get("signer")
    ]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--wallet", required=True)
    parser.add_argument("--query", default="wireless earbuds")
    parser.add_argument("--budget", type=float, default=2.5)
    parser.add_argument(
        "--ship-to",
        default="Google Startup Campus, Seoul, KR",
    )
    parser.add_argument("--expect-blocked", action="store_true")
    args = parser.parse_args()

    wallet_info = httpx.get(
        f"{settings.payments_url}/wallets",
        timeout=10.0,
    ).json()
    buyer_wallet = wallet_info["buyer"]
    mint = wallet_info["usdcMint"]
    before = {
        "userUsdc": _token_balance(args.wallet, mint),
        "agentUsdc": _token_balance(buyer_wallet, mint),
    }
    identity = auth.OAuthIdentity(
        user_id="issue_48_devnet",
        client_id="issue_48_devnet",
        scopes=("openid",),
        expires_at=4_102_444_800,
        wallet_address=args.wallet,
    )
    app = server.create_app(
        api_key="",
        cors_origins="",
        oauth_enabled=True,
        identity_verifier=lambda _: identity,
    )
    with TestClient(app) as client:
        quote = _mcp_call(
            client,
            "request_quote",
            {
                "query": args.query,
                "budget": args.budget,
                "ship_to": args.ship_to,
            },
        )
        payment = _mcp_call(
            client,
            "authorize_payment",
            {
                "pay_to": quote["payTo"],
                "amount": quote["price"]["amount"],
                "reference": quote["reference"],
            },
        )

        confirmation: dict[str, Any] | None = None
        if not args.expect_blocked:
            for attempt in range(6):
                try:
                    confirmation = _mcp_call(
                        client,
                        "settle",
                        {
                            "order_ref": quote["orderRef"],
                            "reference": quote["reference"],
                            "tx_signature": payment["txSignature"],
                        },
                    )
                    if confirmation.get("status") == "paid":
                        break
                except RuntimeError:
                    if attempt == 5:
                        raise
                time.sleep(2)

    after = {
        "userUsdc": _token_balance(args.wallet, mint),
        "agentUsdc": _token_balance(buyer_wallet, mint),
    }
    evidence: dict[str, Any] = {
        "authenticatedWallet": args.wallet,
        "agentWallet": buyer_wallet,
        "before": {key: format(value, "f") for key, value in before.items()},
        "after": {key: format(value, "f") for key, value in after.items()},
        "quote": quote,
        "payment": payment,
        "confirmation": confirmation,
    }

    if args.expect_blocked:
        if payment.get("status") != "approval-required":
            raise RuntimeError("expected an actionable delegation refusal")
        if before != after:
            raise RuntimeError("a blocked OAuth purchase changed a USDC balance")
        evidence["agentUsdcDelta"] = "0"
        evidence["userUsdcDelta"] = "0"
    else:
        amount = Decimal(str(quote["price"]["amount"]))
        user_delta = after["userUsdc"] - before["userUsdc"]
        agent_delta = after["agentUsdc"] - before["agentUsdc"]
        if user_delta != -amount:
            raise RuntimeError(
                f"user balance delta {user_delta} did not equal -{amount}"
            )
        if agent_delta != 0:
            raise RuntimeError(
                f"agent USDC changed during delegated payment: {agent_delta}"
            )
        if not confirmation or confirmation.get("status") != "paid":
            raise RuntimeError("delegated MCP purchase did not settle")
        signers = _transaction_signers(payment["txSignature"])
        if args.wallet in signers:
            raise RuntimeError("user unexpectedly signed the purchase")
        if buyer_wallet not in signers:
            raise RuntimeError("delegate/fee-payer signature was not present")
        evidence["userUsdcDelta"] = format(user_delta, "f")
        evidence["agentUsdcDelta"] = format(agent_delta, "f")
        evidence["transactionSigners"] = signers
        evidence["userPurchaseSignatures"] = 0

    print(json.dumps(evidence, indent=2))


if __name__ == "__main__":
    main()
