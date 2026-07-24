"""Buyer agent — serves the demo UI and a /buy endpoint that runs the flow."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

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
