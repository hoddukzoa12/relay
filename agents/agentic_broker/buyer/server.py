"""Buyer agent — serves the demo UI and a /buy endpoint that runs the flow."""
from __future__ import annotations

import os
from decimal import Decimal
from pathlib import Path
from typing import Any, Optional

import httpx
from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from ..common import service_clients
from ..common.agent_cards import buyer_agent_card
from ..common.config import settings
from . import flow

app = FastAPI(title="Buyer Agent", version="0.1.0")

_WEB_INDEX = Path(__file__).resolve().parents[1] / "web" / "index.html"


class BuyRequest(BaseModel):
    text: Optional[str] = None
    query: Optional[str] = None
    budget: Optional[float] = None
    shipTo: Optional[str] = None


@app.get("/health")
def health() -> dict[str, object]:
    return {"ok": True, "agent": "buyer"}


@app.get("/.well-known/agent-card.json")
@app.get("/a2a/agent-card")
def agent_card() -> dict[str, Any]:
    return buyer_agent_card()


@app.get("/", response_class=HTMLResponse)
def home() -> str:
    if _WEB_INDEX.exists():
        return _WEB_INDEX.read_text(encoding="utf-8")
    return "<h1>Agentic Resell Broker</h1><p>Demo UI missing.</p>"


@app.get("/wallets")
def wallets() -> dict[str, Any]:
    try:
        return service_clients.payments_wallets()
    except Exception as exc:  # noqa: BLE001
        return {"error": str(exc)}


def _decimal_units(amount: int, decimals: int) -> str:
    value = Decimal(amount) / (Decimal(10) ** decimals)
    text = format(value, "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


def _rpc_url() -> str:
    configured = os.getenv("SOLANA_RPC_URL")
    if configured:
        return configured
    return {
        "mainnet-beta": "https://api.mainnet-beta.solana.com",
        "testnet": "https://api.testnet.solana.com",
        "localnet": "http://127.0.0.1:8899",
    }.get(settings.cluster, "https://api.devnet.solana.com")


@app.get("/wallet-balances")
def wallet_balances() -> dict[str, Any]:
    """Return display-only balances without widening the payments contract."""
    try:
        wallet_info = service_clients.payments_wallets()
        requests: list[dict[str, Any]] = []
        for role in ("buyer", "merchant"):
            address = wallet_info[role]
            requests.extend(
                [
                    {
                        "jsonrpc": "2.0",
                        "id": f"{role}-sol",
                        "method": "getBalance",
                        "params": [address, {"commitment": "confirmed"}],
                    },
                    {
                        "jsonrpc": "2.0",
                        "id": f"{role}-usdc",
                        "method": "getTokenAccountsByOwner",
                        "params": [
                            address,
                            {"mint": wallet_info["usdcMint"]},
                            {"encoding": "jsonParsed", "commitment": "confirmed"},
                        ],
                    },
                ]
            )

        response = httpx.post(_rpc_url(), json=requests, timeout=10.0)
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, list):
            raise ValueError("Solana RPC batch response was not a list")
        by_id = {item.get("id"): item for item in payload if isinstance(item, dict)}

        balances: dict[str, Any] = {
            "cluster": wallet_info["cluster"],
            "usdcMint": wallet_info["usdcMint"],
        }
        for role in ("buyer", "merchant"):
            sol_result = by_id.get(f"{role}-sol", {})
            token_result = by_id.get(f"{role}-usdc", {})
            if sol_result.get("error"):
                raise RuntimeError(str(sol_result["error"]))
            if token_result.get("error"):
                raise RuntimeError(str(token_result["error"]))

            lamports = int(sol_result.get("result", {}).get("value", 0))
            token_accounts = token_result.get("result", {}).get("value", [])
            usdc_base_units = 0
            usdc_decimals = 6
            for account in token_accounts:
                token_amount = (
                    account.get("account", {})
                    .get("data", {})
                    .get("parsed", {})
                    .get("info", {})
                    .get("tokenAmount", {})
                )
                usdc_base_units += int(token_amount.get("amount", 0))
                usdc_decimals = int(token_amount.get("decimals", usdc_decimals))

            balances[role] = {
                "address": wallet_info[role],
                "sol": _decimal_units(lamports, 9),
                "usdc": _decimal_units(usdc_base_units, usdc_decimals),
            }
        return balances
    except Exception as exc:  # noqa: BLE001
        return {"error": str(exc)}


@app.post("/buy")
def buy(body: BuyRequest) -> dict[str, Any]:
    if body.text:
        return flow.buy_from_text(body.text)
    return flow.buy(
        query=body.query or "wireless earbuds",
        budget=body.budget if body.budget is not None else 30.0,
        ship_to=body.shipTo or settings.default_ship_to,
    )


def main() -> None:
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=settings.buyer_port)


if __name__ == "__main__":
    main()
