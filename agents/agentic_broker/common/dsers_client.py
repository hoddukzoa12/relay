"""Small synchronous facade over the DSers Streamable HTTP MCP server."""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from .config import settings
from .dsers_auth import (
    DSersAuthUnavailable,
    DSersTokenManager,
    token_manager_from_settings,
)

_LOG = logging.getLogger(__name__)


class DSersMCPError(RuntimeError):
    """A DSers tool failed or returned an unusable result."""


def _tool_payload(result: Any) -> dict[str, Any]:
    structured = getattr(result, "structuredContent", None)
    if isinstance(structured, dict):
        return structured
    content = getattr(result, "content", [])
    texts = [
        str(getattr(item, "text", ""))
        for item in content
        if getattr(item, "type", "") == "text" and getattr(item, "text", "")
    ]
    for text in texts:
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
        if isinstance(parsed, list):
            return {"items": parsed}
    return {
        "content": texts,
        "raw": result.model_dump(mode="json")
        if hasattr(result, "model_dump")
        else str(result),
    }


class DSersMCPClient:
    def __init__(
        self,
        token_manager: DSersTokenManager | None = None,
        *,
        url: str | None = None,
    ) -> None:
        self.token_manager = token_manager
        self.url = url or settings.dsers_mcp_url

    def _manager(self) -> DSersTokenManager:
        if self.token_manager is None:
            self.token_manager = token_manager_from_settings()
        return self.token_manager

    async def _call_async(
        self, name: str, arguments: dict[str, Any], access_token: str
    ) -> dict[str, Any]:
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/json, text/event-stream",
        }
        async with httpx.AsyncClient(headers=headers, timeout=30) as http:
            async with streamable_http_client(
                self.url, http_client=http
            ) as (read_stream, write_stream, _session_id):
                async with ClientSession(read_stream, write_stream) as session:
                    await session.initialize()
                    result = await session.call_tool(name, arguments)
        if getattr(result, "isError", False):
            raise DSersMCPError(
                f"{name} failed: {_tool_payload(result)}"
            )
        return _tool_payload(result)

    def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        manager = self._manager()
        token = manager.access_token()
        try:
            return asyncio.run(self._call_async(name, arguments, token))
        except Exception as exc:  # noqa: BLE001
            lowered = str(exc).lower()
            if any(
                marker in lowered
                for marker in ("401", "unauthorized", "invalid_token", "token expired")
            ):
                _LOG.warning(
                    "[dsers-mcp] access token rejected; performing one "
                    "lease-protected refresh"
                )
                refreshed = manager.access_token(
                    force_refresh=True,
                    rejected_access_token=token,
                )
                try:
                    return asyncio.run(
                        self._call_async(name, arguments, refreshed)
                    )
                except Exception as retry_exc:  # noqa: BLE001
                    raise DSersMCPError(
                        f"{name} failed after authenticated retry: {retry_exc}"
                    ) from retry_exc
            if isinstance(exc, (DSersMCPError, DSersAuthUnavailable)):
                raise
            raise DSersMCPError(f"{name} failed: {exc}") from exc
