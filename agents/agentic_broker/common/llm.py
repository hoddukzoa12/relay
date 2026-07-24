"""Gemini helpers with graceful deterministic fallbacks.

The AI layer (product sourcing + intent parsing) uses Gemini via google-genai.
If GOOGLE_API_KEY is unset or a call fails, we fall back to deterministic stubs
so the whole pipeline still runs end-to-end during early setup.
"""
from __future__ import annotations

import json
import re
from typing import Any

from .config import settings


def gemini_available() -> bool:
    return bool(settings.google_api_key)


def _generate_json(prompt: str, fallback: dict[str, Any]) -> dict[str, Any]:
    if not gemini_available():
        return fallback
    try:
        from google import genai

        client = genai.Client(api_key=settings.google_api_key)
        resp = client.models.generate_content(
            model=settings.gemini_model,
            contents=prompt,
            config={"response_mime_type": "application/json"},
        )
        data = json.loads(resp.text or "{}")
        return data if isinstance(data, dict) else fallback
    except Exception as exc:  # noqa: BLE001 — never let the AI layer break the flow
        print(f"[llm] falling back (reason: {exc})")
        return fallback


def source_offer(query: str, budget_amount: float) -> dict[str, Any]:
    """AI sourcing: pick a concrete product + a plausible wholesale cost.

    Returns {"title": str, "cost": float}. `cost` is what the broker pays;
    the resale price (cost + markup) is computed by the caller.
    """
    fallback = {"title": query.strip().title(), "cost": round(budget_amount * 0.6, 2)}
    prompt = (
        "You are a sourcing agent for a headless resell broker. Given a shopper's "
        f'request and their budget, propose ONE concrete product to source.\n'
        f'Request: "{query}"\nBudget (USDC): {budget_amount}\n'
        "Return JSON ONLY: {\"title\": <specific product name>, "
        "\"cost\": <wholesale cost in USDC as a number, comfortably below budget>}."
    )
    data = _generate_json(prompt, fallback)
    title = str(data.get("title") or fallback["title"])[:120]
    try:
        cost = float(data.get("cost", fallback["cost"]))
    except (TypeError, ValueError):
        cost = fallback["cost"]
    if cost <= 0:
        cost = fallback["cost"]
    return {"title": title, "cost": round(cost, 2)}


def parse_purchase(text: str) -> dict[str, Any]:
    """Parse a natural-language purchase instruction into a structured intent.

    Returns {"query": str, "budget": float, "shipTo": str}.
    """
    # Deterministic fallback: grab the first $ / number as budget.
    m = re.search(r"(\d+(?:\.\d+)?)", text)
    fallback = {
        "query": text.strip(),
        "budget": float(m.group(1)) if m else 30.0,
        "shipTo": settings.default_ship_to,
    }
    prompt = (
        "Extract a purchase intent from the user's message. "
        f'Message: "{text}"\n'
        f'If no shipping address is given, use "{settings.default_ship_to}".\n'
        "Return JSON ONLY: {\"query\": <what to buy>, \"budget\": <max USDC as number>, "
        "\"shipTo\": <shipping address>}."
    )
    data = _generate_json(prompt, fallback)
    try:
        budget = float(data.get("budget", fallback["budget"]))
    except (TypeError, ValueError):
        budget = fallback["budget"]
    return {
        "query": str(data.get("query") or fallback["query"]),
        "budget": budget,
        "shipTo": str(data.get("shipTo") or fallback["shipTo"]),
    }
