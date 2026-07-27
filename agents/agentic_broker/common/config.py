"""Runtime configuration, loaded from the repo-root .env."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

# agents/agentic_broker/common/config.py -> parents[3] == repo root
_REPO_ROOT = Path(__file__).resolve().parents[3]
load_dotenv(_REPO_ROOT / ".env")
load_dotenv(Path.cwd() / ".env", override=False)


@dataclass(frozen=True)
class Settings:
    payments_url: str = os.getenv("PAYMENTS_SERVICE_URL", "http://localhost:8081")
    commerce_url: str = os.getenv("COMMERCE_SERVICE_URL", "http://localhost:8082")
    shopping_agent_url: str = os.getenv("SHOPPING_AGENT_URL", "http://localhost:8091")
    buyer_agent_url: str = os.getenv("BUYER_AGENT_URL", "http://localhost:8090")
    buyer_port: int = int(os.getenv("BUYER_PORT", "8090"))
    shopping_port: int = int(os.getenv("SHOPPING_PORT", "8091"))
    mcp_port: int = int(os.getenv("MCP_PORT", "8092"))
    mcp_api_key: str = os.getenv("MCP_API_KEY", "")
    mcp_cors_origins: str = os.getenv("MCP_CORS_ORIGINS", "*")

    google_api_key: str = os.getenv("GOOGLE_API_KEY", "")
    gemini_model: str = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")

    clerk_publishable_key: str = os.getenv("CLERK_PUBLISHABLE_KEY", "")
    clerk_secret_key: str = os.getenv("CLERK_SECRET_KEY", "")
    clerk_issuer: str = os.getenv("CLERK_ISSUER", "").rstrip("/")
    clerk_jwks_url: str = os.getenv("CLERK_JWKS_URL", "")
    clerk_api_url: str = os.getenv("CLERK_API_URL", "https://api.clerk.com").rstrip("/")

    markup_pct: float = float(os.getenv("BROKER_MARKUP_PCT", "15"))
    cluster: str = os.getenv("SOLANA_CLUSTER", "devnet")
    default_ship_to: str = os.getenv(
        "DEFAULT_SHIP_TO", "Google Startup Campus, Seoul, KR"
    )


settings = Settings()
