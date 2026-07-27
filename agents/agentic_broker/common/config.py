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


def _delegation_approval_url() -> str:
    configured = os.getenv("DELEGATION_APPROVAL_URL", "").strip()
    if configured:
        return configured.rstrip("/")
    domain = os.getenv("SHOPIFY_STORE_DOMAIN", "your-store.myshopify.com").strip()
    if domain.startswith(("http://", "https://")):
        return domain.rstrip("/")
    return f"https://{domain}".rstrip("/")


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
    mcp_allowed_hosts: str = os.getenv(
        "MCP_ALLOWED_HOSTS",
        "localhost:*,127.0.0.1:*,testserver",
    )
    mcp_allowed_origins: str = os.getenv(
        "MCP_ALLOWED_ORIGINS",
        "http://localhost:*,http://127.0.0.1:*",
    )

    google_api_key: str = os.getenv("GOOGLE_API_KEY", "")
    gemini_model: str = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")

    # DSers is an additive supplier-catalog path. Leaving the secret ID empty
    # disables it without affecting catalog search, quoting, or settlement.
    dsers_mcp_url: str = os.getenv(
        "DSERS_MCP_URL", "https://mcp.dsers.com/dropshipping/mcp"
    ).rstrip("/")
    dsers_oauth_token_url: str = os.getenv(
        "DSERS_OAUTH_TOKEN_URL", "https://mcp.dsers.com/oauth/token"
    ).rstrip("/")
    dsers_secret_project_id: str = os.getenv(
        "DSERS_SECRET_PROJECT_ID", os.getenv("GCP_PROJECT_ID", "")
    )
    dsers_secret_id: str = os.getenv("DSERS_SECRET_ID", "")
    dsers_secret_alias: str = os.getenv(
        "DSERS_SECRET_ALIAS", "relay-active"
    )
    dsers_target_store: str = os.getenv("DSERS_TARGET_STORE", "")
    dsers_ship_to: str = os.getenv("DSERS_SHIP_TO", "US").upper()
    dsers_ship_from: str = os.getenv("DSERS_SHIP_FROM", "CN").upper()
    dsers_max_imports_per_request: int = int(
        os.getenv("DSERS_MAX_IMPORTS_PER_REQUEST", "1")
    )

    clerk_publishable_key: str = os.getenv("CLERK_PUBLISHABLE_KEY", "")
    clerk_secret_key: str = os.getenv("CLERK_SECRET_KEY", "")
    clerk_issuer: str = os.getenv("CLERK_ISSUER", "").rstrip("/")
    clerk_jwks_url: str = os.getenv("CLERK_JWKS_URL", "")
    clerk_api_url: str = os.getenv("CLERK_API_URL", "https://api.clerk.com").rstrip("/")
    delegation_approval_url: str = _delegation_approval_url()

    markup_pct: float = float(os.getenv("BROKER_MARKUP_PCT", "15"))
    default_budget_usdc: float = float(os.getenv("DEFAULT_BUDGET_USDC", "5"))
    cluster: str = os.getenv("SOLANA_CLUSTER", "devnet")
    default_ship_to: str = os.getenv(
        "DEFAULT_SHIP_TO", "Google Startup Campus, Seoul, KR"
    )


settings = Settings()
