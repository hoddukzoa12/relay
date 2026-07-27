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


def _catalog_relevance(product: dict[str, Any], query: str) -> int:
    tokens = set(re.findall(r"[a-z0-9]+", query.lower()))
    title = str(product.get("title", "")).lower()
    sku = str(product.get("sku", "")).lower()
    searchable = " ".join(
        [
            title,
            sku,
            str(product.get("description", "")).lower(),
            " ".join(str(tag).lower() for tag in product.get("tags", [])),
        ]
    )
    return sum(
        4
        if token in title
        else 3
        if token in sku
        else 1
        if token in searchable
        else 0
        for token in tokens
        if len(token) > 1
    )


def source_offer(
    query: str,
    budget_amount: float,
    candidates: list[dict[str, Any]],
) -> dict[str, Any]:
    """Choose one offer from catalog candidates without inventing products."""
    if not candidates:
        raise ValueError("no catalog candidates were supplied")

    deterministic = sorted(
        candidates,
        key=lambda product: (
            -_catalog_relevance(product, query),
            float(product["price"]),
            str(product["sku"]),
        ),
    )[0]
    fallback = {"variantId": deterministic["variantId"]}
    prompt_candidates = [
        {
            "variantId": product["variantId"],
            "sku": product["sku"],
            "title": product["title"],
            "description": product.get("description", ""),
            "price": product["price"],
            "inventoryQuantity": product["inventoryQuantity"],
        }
        for product in candidates
    ]
    prompt = (
        "You are ranking real Shopify catalog variants for a headless resell "
        "broker. Select exactly one candidate that best matches the request. "
        "You MUST return a variantId from the supplied candidates and may not "
        "invent or alter any product.\n"
        f'Request: "{query}"\nBudget (USDC): {budget_amount}\n'
        f"Candidates: {json.dumps(prompt_candidates, ensure_ascii=False)}\n"
        'Return JSON ONLY: {"variantId": "<one supplied variantId>"}'
    )
    data = _generate_json(prompt, fallback)
    selected_id = str(data.get("variantId") or fallback["variantId"])
    return next(
        (
            product
            for product in candidates
            if product["variantId"] == selected_id
        ),
        deterministic,
    )


def parse_purchase(text: str) -> dict[str, Any]:
    """Parse a natural-language purchase instruction into a structured intent.

    Returns {"query": str, "budget": float, "shipTo": str}.
    """
    # Deterministic fallback: grab the first $ / number as budget.
    m = re.search(r"(\d+(?:\.\d+)?)", text)
    fallback = {
        "query": text.strip(),
        "budget": (
            float(m.group(1))
            if m
            else settings.default_budget_usdc
        ),
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
