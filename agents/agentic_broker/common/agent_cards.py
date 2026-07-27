"""A2A v0.3.0 AgentCard documents for Relay's two agents."""
from __future__ import annotations

from typing import Any

from .config import settings

_PROVIDER = {
    "organization": "Relay",
    "url": "https://github.com/hoddukzoa12/relay",
}
_CAPABILITIES = {"streaming": False, "pushNotifications": False}
_MODES = ["application/json", "text/plain"]


def shopping_agent_card() -> dict[str, Any]:
    return {
        "protocolVersion": "0.3.0",
        "name": "Relay Shopping Broker",
        "description": (
            "Headless merchant broker: sources, prices, and settles USDC on Solana."
        ),
        "url": f"{settings.shopping_agent_url.rstrip('/')}/a2a",
        "preferredTransport": "JSONRPC",
        "version": "0.1.0",
        "provider": _PROVIDER,
        "capabilities": _CAPABILITIES,
        "defaultInputModes": _MODES,
        "defaultOutputModes": _MODES,
        "skills": [
            {
                "id": "quote",
                "name": "Quote",
                "description": (
                    "Source a product and issue an agent-native USDC payment "
                    "request (CartMandate)."
                ),
                "tags": ["commerce", "payments"],
                "examples": ["Buy wireless earbuds under 5 USDC"],
            },
            {
                "id": "settle",
                "name": "Settle",
                "description": (
                    "Verify the on-chain USDC payment and record the order."
                ),
                "tags": ["payments", "solana"],
                "examples": ["Settle an authorized CartMandate on Solana devnet"],
            },
        ],
    }


def buyer_agent_card() -> dict[str, Any]:
    return {
        "protocolVersion": "0.3.0",
        "name": "Relay Buyer Agent",
        "description": (
            "Delegated buyer: authorizes AP2 mandates and autonomously settles "
            "USDC on Solana."
        ),
        "url": f"{settings.buyer_agent_url.rstrip('/')}/a2a",
        "preferredTransport": "JSONRPC",
        "version": "0.1.0",
        "provider": _PROVIDER,
        "capabilities": _CAPABILITIES,
        "defaultInputModes": _MODES,
        "defaultOutputModes": _MODES,
        "skills": [
            {
                "id": "buy",
                "name": "Buy",
                "description": (
                    "Delegate a purchase under a price ceiling with autonomous "
                    "AP2 authorization and Solana Pay settlement."
                ),
                "tags": ["commerce", "payments", "solana"],
                "examples": ["Buy wireless earbuds under 5 USDC"],
            }
        ],
    }
