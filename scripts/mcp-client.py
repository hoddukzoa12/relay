#!/usr/bin/env python3
"""Connect to Relay over Streamable HTTP and optionally run a full lifecycle."""
from __future__ import annotations

import argparse
import asyncio
import json
import os
from typing import Any

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

EXPECTED_TOOLS = {
    "search_products",
    "request_quote",
    "authorize_payment",
    "settle",
    "get_order_status",
    "refund_order",
    "wallet_balances",
}


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--url",
        default=os.getenv("RELAY_MCP_URL", "http://localhost:8092/mcp"),
    )
    parser.add_argument(
        "--api-key",
        default=os.getenv("MCP_API_KEY", ""),
        help="trusted service credential; defaults to MCP_API_KEY",
    )
    parser.add_argument(
        "--oauth-token",
        default=os.getenv("MCP_OAUTH_TOKEN", ""),
        help="Clerk OAuth access token; defaults to MCP_OAUTH_TOKEN",
    )
    parser.add_argument("--purchase", action="store_true")
    parser.add_argument("--refund", action="store_true")
    parser.add_argument(
        "--order-ref",
        help="look up an existing order; combine with --refund for a safe replay",
    )
    parser.add_argument("--query", default="wireless earbuds")
    parser.add_argument("--budget", type=float, default=5.0)
    parser.add_argument(
        "--ship-to", default="Google Startup Campus, Seoul, KR"
    )
    return parser.parse_args()


def _structured(result: Any) -> dict[str, Any]:
    if result.isError:
        messages = [
            getattr(block, "text", repr(block)) for block in result.content
        ]
        raise RuntimeError("; ".join(messages))
    if result.structuredContent is not None:
        return result.structuredContent
    for block in result.content:
        text = getattr(block, "text", None)
        if text:
            parsed = json.loads(text)
            if isinstance(parsed, dict):
                return parsed
    raise RuntimeError("tool did not return a JSON object")


def _print_step(step: str, result: dict[str, Any], attempt: int = 1) -> None:
    print(
        json.dumps(
            {"step": step, "attempt": attempt, "result": result},
            sort_keys=True,
        ),
        flush=True,
    )


def _require_delegation_ready(result: dict[str, Any]) -> None:
    if result.get("status") != "approval-required":
        return
    raise RuntimeError(
        f"{result.get('reason', 'wallet approval required')} "
        f"Open {result.get('approvalUrl')} and retry."
    )


async def _run(args: argparse.Namespace) -> None:
    if bool(args.api_key) == bool(args.oauth_token):
        raise SystemExit(
            "provide exactly one of MCP_OAUTH_TOKEN/--oauth-token or "
            "MCP_API_KEY/--api-key"
        )
    headers = (
        {"Authorization": f"Bearer {args.oauth_token}"}
        if args.oauth_token
        else {"X-Relay-API-Key": args.api_key}
    )

    async with httpx.AsyncClient(
        headers=headers,
        follow_redirects=True,
        timeout=httpx.Timeout(90.0),
    ) as http_client:
        async with streamable_http_client(
            args.url, http_client=http_client
        ) as (read_stream, write_stream, _):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                listing = await session.list_tools()
                names = {tool.name for tool in listing.tools}
                print(json.dumps({"url": args.url, "tools": sorted(names)}))
                missing = EXPECTED_TOOLS - names
                if missing:
                    raise RuntimeError(
                        f"Relay MCP is missing tools: {sorted(missing)}"
                    )
                if args.order_ref:
                    status = _structured(
                        await session.call_tool(
                            "get_order_status",
                            {"order_ref": args.order_ref},
                        )
                    )
                    _print_step("get_order_status", status)
                    evidence: dict[str, Any] = {"order": status}
                    if args.refund:
                        refund = _structured(
                            await session.call_tool(
                                "refund_order",
                                {"order_ref": args.order_ref},
                            )
                        )
                        _print_step("refund_order", refund)
                        evidence["refund"] = refund
                    print(json.dumps(evidence, indent=2))
                    return
                if not args.purchase:
                    return

                quote = _structured(
                    await session.call_tool(
                        "request_quote",
                        {
                            "query": args.query,
                            "budget": args.budget,
                            "ship_to": args.ship_to,
                        },
                    )
                )
                _print_step("request_quote", quote)
                _require_delegation_ready(quote)
                payment = _structured(
                    await session.call_tool(
                        "authorize_payment",
                        {
                            "pay_to": quote["payTo"],
                            "amount": quote["price"]["amount"],
                            "reference": quote["reference"],
                        },
                    )
                )
                _print_step("authorize_payment", payment)
                _require_delegation_ready(payment)

                confirmation: dict[str, Any] = {}
                for attempt in range(1, 7):
                    confirmation = _structured(
                        await session.call_tool(
                            "settle",
                            {
                                "order_ref": quote["orderRef"],
                                "reference": quote["reference"],
                                "tx_signature": payment["txSignature"],
                            },
                        )
                    )
                    _print_step("settle", confirmation, attempt)
                    if confirmation.get("status") == "paid":
                        break
                    if confirmation.get("status") != "pending":
                        raise RuntimeError(
                            f"settlement failed: {confirmation}"
                        )
                    await asyncio.sleep(3)
                else:
                    raise RuntimeError("settlement remained pending")

                status: dict[str, Any] = {}
                for attempt in range(1, 7):
                    result = await session.call_tool(
                        "get_order_status", {"order_ref": quote["orderRef"]}
                    )
                    try:
                        status = _structured(result)
                    except RuntimeError:
                        if attempt == 6:
                            raise
                        await asyncio.sleep(2)
                        continue
                    _print_step("get_order_status", status, attempt)
                    break

                evidence: dict[str, Any] = {
                    "quote": quote,
                    "payment": payment,
                    "confirmation": confirmation,
                    "order": status,
                }
                if args.refund:
                    for attempt in range(1, 7):
                        result = await session.call_tool(
                            "refund_order", {"order_ref": quote["orderRef"]}
                        )
                        try:
                            refund = _structured(result)
                        except RuntimeError:
                            if attempt == 6:
                                raise
                            await asyncio.sleep(3)
                            continue
                        _print_step("refund_order", refund, attempt)
                        evidence["refund"] = refund
                        break
                print(json.dumps(evidence, indent=2))


def main() -> None:
    asyncio.run(_run(_arguments()))


if __name__ == "__main__":
    main()
