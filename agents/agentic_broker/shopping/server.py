"""Shopping (broker) agent — A2A HTTP endpoints the buyer agent calls."""
from __future__ import annotations

from fastapi import FastAPI, HTTPException

from ..common.config import settings
from ..common.contracts import (
    OrderConfirmation,
    PaymentRequest,
    PurchaseIntent,
    SettlementRequest,
)
from . import broker

app = FastAPI(title="Shopping Broker Agent", version="0.1.0")


@app.get("/health")
def health() -> dict[str, object]:
    return {"ok": True, "agent": "shopping"}


@app.get("/a2a/agent-card")
def agent_card() -> dict[str, object]:
    """A minimal A2A-style agent card advertising this broker's capabilities."""
    return {
        "name": "shopping-broker",
        "description": "Headless merchant broker. Sources products, issues "
        "agent-native USDC payment requests, verifies on-chain, records orders.",
        "capabilities": ["quote", "settle"],
        "endpoints": {"quote": "/a2a/quote", "settle": "/a2a/settle"},
        "settlement": {"network": f"solana-{settings.cluster}", "asset": "USDC"},
    }


@app.post("/a2a/quote", response_model=PaymentRequest)
def quote(intent: PurchaseIntent) -> PaymentRequest:
    try:
        return broker.handle_quote(intent)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/a2a/settle", response_model=OrderConfirmation)
def settle(req: SettlementRequest) -> OrderConfirmation:
    return broker.handle_settle(req)


def main() -> None:
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=settings.shopping_port)


if __name__ == "__main__":
    main()
