"""Guarded DSers supplier-pool sourcing.

The four named functions mirror the DSers MCP workflow and are also composed by
``source_missing_product`` for deterministic buyer/API calls.  No operation in
this module can touch the Solana Pay service or delete a Shopify/DSers product.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from itertools import product
import logging
import re
import time
from typing import Any, Iterable
from urllib.parse import urlsplit

from ..common import service_clients
from ..common.config import settings
from ..common.dsers_client import DSersMCPClient, DSersMCPError

_LOG = logging.getLogger(__name__)
_AUTONOMOUS_VENDOR = "Relay DSers Autonomous"
_AUTONOMOUS_TAGS = ["relay:autonomous-sourced", "relay:dsers"]
_BIND_LOOKUP_ATTEMPTS = 5
_BIND_LOOKUP_DELAY_SECONDS = 1.0

# Check before the first external search. English and Korean demo requests are
# covered explicitly; broad roots intentionally bias toward refusal.
_PROHIBITED = (
    r"\badult\b",
    r"\bsex(?:ual)?\b",
    r"\bporn",
    r"\bmedicine\b",
    r"\bmedication\b",
    r"\bprescription\b",
    r"\bdrug\b",
    r"\bcounterfeit\b",
    r"\breplica\b",
    r"\bfake\s+brand\b",
    r"\btobacco\b",
    r"\bcigarette\b",
    r"\bvape\b",
    r"\bweapon\b",
    r"\bgun\b",
    r"\bfirearm\b",
    r"\bammunition\b",
    r"\bknife\b",
    r"\bsurveillance\b",
    r"\bspy\s*camera\b",
    r"성인용",
    r"의약품",
    r"처방약",
    r"위조",
    r"가품",
    r"담배",
    r"전자담배",
    r"무기",
    r"총기",
    r"탄약",
    r"감시장비",
    r"몰래\s*카메라",
)


class DSersSourcingUnavailable(RuntimeError):
    """The additive supplier path failed; existing catalog remains usable."""


@dataclass
class SourcingRequest:
    query: str
    budget: Decimal
    import_count: int = 0

    def consume_import(self) -> None:
        configured = settings.dsers_max_imports_per_request
        if configured < 1:
            raise DSersSourcingUnavailable(
                "DSers autonomous imports are disabled by the per-request cap"
            )
        if self.import_count >= configured:
            raise DSersSourcingUnavailable(
                f"DSers import cap reached ({configured} per request)"
            )
        self.import_count += 1


@dataclass(frozen=True)
class VariantEconomics:
    sku: str
    cost: Decimal
    sale_price: Decimal
    stock: int
    shopify_variant_id: str = ""
    option_values: tuple[str, ...] = ()


def assert_query_allowed(query: str) -> None:
    normalized = query.casefold()
    for pattern in _PROHIBITED:
        if re.search(pattern, normalized, flags=re.IGNORECASE):
            raise ValueError(
                "Relay will not search suppliers for regulated or prohibited "
                "adult, medical, counterfeit, tobacco, weapon, or surveillance items"
            )


def _walk_dicts(value: Any) -> Iterable[dict[str, Any]]:
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk_dicts(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_dicts(child)


def _normalized(record: dict[str, Any]) -> dict[str, Any]:
    return {
        re.sub(r"[^a-z0-9]", "", str(key).lower()): value
        for key, value in record.items()
    }


def _field(record: dict[str, Any], *names: str) -> Any:
    values = _normalized(record)
    for name in names:
        key = re.sub(r"[^a-z0-9]", "", name.lower())
        if key in values and values[key] not in (None, ""):
            return values[key]
    return None


def _decimal(value: Any) -> Decimal | None:
    if isinstance(value, (int, float, Decimal)):
        try:
            result = Decimal(str(value))
            return result if result.is_finite() else None
        except InvalidOperation:
            return None
    if not isinstance(value, str):
        return None
    match = re.search(r"\d+(?:\.\d{1,6})?", value.replace(",", ""))
    if not match:
        return None
    try:
        return Decimal(match.group(0))
    except InvalidOperation:
        return None


def _int(value: Any) -> int:
    number = _decimal(value)
    return int(number) if number is not None else 0


def _shopify_variant_id(record: dict[str, Any]) -> str:
    explicit = _field(
        record,
        "shopify_variant_id",
        "shopify_variant_gid",
        "store_variant_id",
        "store_variant_gid",
        "platform_variant_id",
        "platform_variant_gid",
    )
    text = str(explicit or "").strip()
    if re.fullmatch(r"\d+", text):
        return f"gid://shopify/ProductVariant/{text}"
    if re.fullmatch(r"gid://shopify/ProductVariant/\d+", text):
        return text

    # A generic variant_id can belong to the supplier. Accept it only when the
    # payload itself proves that it is a Shopify ProductVariant GID.
    generic = str(_field(record, "variant_id", "variantId") or "").strip()
    return (
        generic
        if re.fullmatch(r"gid://shopify/ProductVariant/\d+", generic)
        else ""
    )


def _preview_option_signatures(payload: dict[str, Any]) -> dict[str, tuple[str, ...]]:
    """Map DSers display rows to exact declared option-value combinations."""
    raw_options = payload.get("options")
    if not isinstance(raw_options, list) or not raw_options:
        return {}
    groups: list[list[str]] = []
    combinations = 1
    for option in raw_options:
        if not isinstance(option, dict) or not isinstance(
            option.get("values"), list
        ):
            return {}
        values = [
            str(value).strip()
            for value in option["values"]
            if str(value).strip()
        ]
        if not values or len(set(values)) != len(values):
            return {}
        combinations *= len(values)
        if combinations > 10_000:
            return {}
        groups.append(values)

    signatures: dict[str, tuple[str, ...]] = {}
    ambiguous: set[str] = set()
    for selected in product(*groups):
        name = "-".join(selected)
        signature = tuple(sorted(selected))
        if name in signatures and signatures[name] != signature:
            ambiguous.add(name)
        else:
            signatures[name] = signature
    for name in ambiguous:
        signatures.pop(name, None)
    return signatures


def _shopify_sku_option_values(sku: str) -> tuple[str, ...]:
    """Extract option labels only from a structured supplier-backed Shopify SKU."""
    parts = sku.split(";")
    if len(parts) < 2:
        return ()
    values: list[str] = []
    for part in parts:
        match = re.fullmatch(r"[^#;]+#([^;]+)", part)
        if not match:
            return ()
        value = match.group(1).strip()
        if not value:
            return ()
        values.append(value)
    return tuple(sorted(values)) if len(set(values)) == len(values) else ()


def _source_url(record: dict[str, Any]) -> str:
    value = _field(
        record,
        "import_url",
        "source_url",
        "supplier_url",
        "product_url",
        "url",
    )
    if not isinstance(value, str):
        return ""
    parsed = urlsplit(value)
    return value if parsed.scheme == "https" and parsed.netloc else ""


def _import_item_id(record: dict[str, Any]) -> str:
    value = _field(record, "import_item_id", "importItemId")
    return str(value or "")


def _records_with_url(payload: dict[str, Any]) -> list[dict[str, Any]]:
    return [record for record in _walk_dicts(payload) if _source_url(record)]


def _exact_source_record(
    payload: dict[str, Any], source_url: str
) -> dict[str, Any] | None:
    expected = source_url.rstrip("/")
    return next(
        (
            record
            for record in _records_with_url(payload)
            if _source_url(record).rstrip("/") == expected
        ),
        None,
    )


def _candidate_cost(record: dict[str, Any]) -> Decimal:
    raw = _field(
        record,
        "cost",
        "min_cost",
        "cost_min",
        "supplier_price",
        "price",
    )
    if isinstance(raw, dict):
        raw = _field(raw, "min", "amount", "value")
    return _decimal(raw) or Decimal("999999999")


def dsers_find_product(
    query: str,
    *,
    client: DSersMCPClient | None = None,
    limit: int = 8,
) -> dict[str, Any]:
    """Search DSers only after the local catalog has no suitable match."""
    assert_query_allowed(query)
    return (client or DSersMCPClient()).call_tool(
        "dsers_find_product",
        {
            "keyword": query,
            "supplier": "aliexpress",
            "ship_to": settings.dsers_ship_to,
            "ship_from": settings.dsers_ship_from,
            "sort": "relevance",
            "limit": min(max(limit, 1), 20),
        },
    )


def _import_list(
    client: DSersMCPClient, source_url: str = ""
) -> dict[str, Any]:
    arguments: dict[str, Any] = {"page": 1, "page_size": 100}
    product_id = _supplier_product_id(source_url)
    if product_id:
        arguments["keyword"] = product_id
    return client.call_tool("dsers_import_list", arguments)


def dsers_product_import(
    source_url: str,
    request: SourcingRequest,
    *,
    client: DSersMCPClient | None = None,
) -> dict[str, Any]:
    """Import at most the configured count and reconcile ambiguous failures."""
    active_client = client or DSersMCPClient()
    staged = _exact_source_record(
        _import_list(active_client, source_url), source_url
    )
    if staged and _import_item_id(staged):
        return {
            "import_item_id": _import_item_id(staged),
            "source_url": source_url,
            "reused": True,
        }

    request.consume_import()
    multiplier = Decimal("1") + Decimal(str(settings.markup_pct)) / Decimal("100")
    try:
        arguments: dict[str, Any] = {
            "source_url": source_url,
            "source_hint": "aliexpress",
            "country": settings.dsers_ship_to,
            "visibility_mode": "sell_immediately",
            "pricing_mode": "multiplier",
            "pricing_multiplier": float(multiplier),
            "title_prefix": "[Relay Sourced] ",
            "batch_detail": "full",
        }
        if settings.dsers_target_store:
            arguments["target_store"] = settings.dsers_target_store
        result = active_client.call_tool("dsers_product_import", arguments)
    except Exception as exc:  # noqa: BLE001
        # DSers/CloudFront has returned 504 after committing mutations. Never
        # retry the import blindly: read staging and accept only an exact URL.
        reconciled = _exact_source_record(
            _import_list(active_client, source_url), source_url
        )
        if reconciled and _import_item_id(reconciled):
            return {
                "import_item_id": _import_item_id(reconciled),
                "source_url": source_url,
                "reused": True,
                "reconciled_after_error": str(exc),
            }
        raise DSersSourcingUnavailable(
            "DSers import returned an ambiguous failure and staging did not "
            "prove success; Relay did not retry, to avoid a duplicate import"
        ) from exc

    item_id = _import_item_id(result)
    if not item_id:
        record = _exact_source_record(result, source_url)
        item_id = _import_item_id(record or {})
    if not item_id:
        reconciled = _exact_source_record(
            _import_list(active_client, source_url), source_url
        )
        item_id = _import_item_id(reconciled or {})
    if not item_id:
        raise DSersSourcingUnavailable(
            "DSers imported a product but returned no persistent import_item_id"
        )
    return {
        **result,
        "import_item_id": item_id,
        "source_url": source_url,
        "reused": False,
    }


def dsers_product_preview(
    import_item_id: str,
    *,
    client: DSersMCPClient | None = None,
) -> dict[str, Any]:
    """Read full costs, sell prices, stock, and supplier mappings."""
    if not import_item_id:
        raise ValueError("import_item_id is required")
    return (client or DSersMCPClient()).call_tool(
        "dsers_product_preview",
        {
            "import_item_id": import_item_id,
            "variant_detail": "full",
            "variant_offset": 0,
            "variant_limit": 100,
            "show_all_options": True,
            "include_images": False,
        },
    )


def _variant_economics(payload: dict[str, Any]) -> list[VariantEconomics]:
    variants: list[VariantEconomics] = []
    option_signatures = _preview_option_signatures(payload)
    for record in _walk_dicts(payload):
        cost = _decimal(
            _field(
                record,
                "cost",
                "cost_price",
                "supplier_cost",
                "product_cost",
                "original_price",
            )
        )
        sale = _decimal(
            _field(
                record,
                "sell_price",
                "selling_price",
                "store_price",
                "price",
            )
        )
        sku = str(
            _field(record, "sku", "variant_sku", "supplier_sku") or ""
        ).strip()
        if not sku or cost is None or sale is None:
            continue
        variants.append(
            VariantEconomics(
                sku=sku,
                cost=cost,
                sale_price=sale,
                stock=_int(
                    _field(
                        record,
                        "stock",
                        "quantity",
                        "supplier_quantity",
                        "inventory",
                    )
                ),
                shopify_variant_id=_shopify_variant_id(record),
                option_values=option_signatures.get(sku, ()),
            )
        )
    # DSers's full preview uses a compact matrix rather than objects:
    # [["name", "sell", "compare_at", "cost", "qty", "supplier_qty"], [...]].
    # Keep the displayed variant name as a temporary identity; the commerce
    # write later binds against the exact post-push Shopify variant inventory.
    for record in _walk_dicts(payload):
        for value in record.values():
            if (
                not isinstance(value, list)
                or len(value) < 2
                or not isinstance(value[0], list)
                or not all(isinstance(column, str) for column in value[0])
            ):
                continue
            headers = [str(column) for column in value[0]]
            for row in value[1:]:
                if not isinstance(row, list) or len(row) != len(headers):
                    continue
                tabular = dict(zip(headers, row, strict=True))
                cost = _decimal(tabular.get("cost"))
                sale = _decimal(tabular.get("sell"))
                name = str(tabular.get("sku") or tabular.get("name") or "").strip()
                if not name or cost is None or sale is None:
                    continue
                variants.append(
                    VariantEconomics(
                        sku=name,
                        cost=cost,
                        sale_price=sale,
                        stock=_int(
                            tabular.get("supplier_qty", tabular.get("qty"))
                        ),
                        shopify_variant_id=_shopify_variant_id(tabular),
                        option_values=option_signatures.get(name, ()),
                    )
                )
    # Some tool payloads repeat the same variant in summary/detail branches.
    unique: dict[
        tuple[str, Decimal, Decimal, str, tuple[str, ...]],
        VariantEconomics,
    ] = {}
    for variant in variants:
        unique[
            (
                variant.sku,
                variant.cost,
                variant.sale_price,
                variant.shopify_variant_id,
                variant.option_values,
            )
        ] = variant
    return list(unique.values())


def validate_margin(
    preview: dict[str, Any], budget: Decimal
) -> list[VariantEconomics]:
    variants = _variant_economics(preview)
    if not variants:
        raise DSersSourcingUnavailable(
            "DSers preview did not expose variant-level cost and sell price; "
            "Relay refused to push without margin evidence"
        )
    unsafe = [
        variant
        for variant in variants
        if variant.cost <= 0
        or variant.sale_price <= 0
        or variant.sale_price <= variant.cost
    ]
    if unsafe:
        raise DSersSourcingUnavailable(
            "DSers preview contains zero-price or non-positive-margin variants; "
            "Relay refused to push"
        )
    affordable = [
        variant
        for variant in variants
        if variant.stock > 0
        and (
            variant.sale_price
            * (
                Decimal("1")
                + Decimal(str(settings.markup_pct)) / Decimal("100")
            )
        ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP) <= budget
    ]
    if not affordable:
        raise DSersSourcingUnavailable(
            "DSers preview has no in-stock positive-margin variant whose "
            f"final broker quote fits {budget} USDC"
        )
    return affordable


def _discover_store(client: DSersMCPClient) -> tuple[str, dict[str, Any]]:
    arguments = (
        {"target_store": settings.dsers_target_store}
        if settings.dsers_target_store
        else {}
    )
    payload = client.call_tool(
        "dsers_store_discover",
        arguments,
    )
    stores = [
        record
        for record in _walk_dicts(payload)
        if _field(record, "store_id", "id")
        and _field(record, "platform")
    ]
    target = settings.dsers_target_store.casefold()
    if target:
        stores = [
            record
            for record in stores
            if target
            in {
                str(_field(record, "store_id", "id") or "").casefold(),
                str(_field(record, "name", "store_name") or "").casefold(),
            }
        ]
    if len(stores) != 1:
        raise DSersSourcingUnavailable(
            "DSers store discovery must resolve exactly one configured store"
        )
    store_id = str(_field(stores[0], "store_id", "id"))
    return store_id, stores[0]


def _my_products(
    client: DSersMCPClient, store_id: str, cursor: str = ""
) -> dict[str, Any]:
    arguments = {"store_id": store_id, "page_size": 100}
    if cursor:
        arguments["cursor"] = cursor
    return client.call_tool("dsers_my_products", arguments)


def _reusable_source_record(
    payload: dict[str, Any], source_url: str
) -> dict[str, Any] | None:
    record = _exact_source_record(payload, source_url)
    if not record:
        return None
    status = str(_field(record, "status") or "").casefold()
    # A fulfilled Relay listing is intentionally DRAFT/offselling in Shopify.
    # Current preview economics and inventory are revalidated before this
    # record is reused, then commerce republishes the exact product GID.
    return None if status == "deleted" else record


def _reusable_product_by_source(
    client: DSersMCPClient,
    store_id: str,
    source_url: str,
) -> dict[str, Any] | None:
    cursor = ""
    seen: set[str] = set()
    for _page in range(20):
        payload = _my_products(client, store_id, cursor)
        record = _reusable_source_record(payload, source_url)
        if record:
            return record
        next_cursor = str(payload.get("next_cursor") or "")
        if not next_cursor or next_cursor in seen:
            return None
        seen.add(next_cursor)
        cursor = next_cursor
    raise DSersSourcingUnavailable(
        "DSers product lookup exceeded the bounded pagination limit"
    )


def _shopify_product_id(record: dict[str, Any]) -> str:
    value = _field(
        record,
        "shopify_product_id",
        "shopify_product_gid",
        "shopify_id",
        "store_product_id",
        "store_product_gid",
        "external_product_id",
        "platform_product_id",
    )
    text = str(value or "")
    if re.fullmatch(r"\d+", text):
        return f"gid://shopify/Product/{text}"
    if re.fullmatch(r"gid://shopify/Product/\d+", text):
        return text
    return ""


def _store_handle(record: dict[str, Any]) -> str:
    value = str(_field(record, "store_handle", "shopify_handle") or "")
    return (
        value
        if re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", value)
        else ""
    )


def _dsers_product_id(record: dict[str, Any]) -> str:
    return str(_field(record, "dsers_product_id", "product_id") or "").strip()


def _exact_binding_records(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Return only records carrying an exact Shopify GID or handle."""
    return [
        record
        for record in _walk_dicts(payload)
        if _shopify_product_id(record) or _store_handle(record)
    ]


def _resolve_exact_shopify_product(
    record: dict[str, Any],
) -> tuple[str, dict[str, Any]] | None:
    """Resolve variants through exact handle/GID reads, never product titles."""
    expected_id = _shopify_product_id(record)
    handle = _store_handle(record)
    if handle:
        try:
            resolved = service_clients.commerce_product_by_handle(handle)
        except Exception as exc:  # noqa: BLE001
            _LOG.info(
                "[dsers-sourcing] exact Shopify handle %s not readable yet: %s",
                handle,
                exc,
            )
        else:
            resolved_id = str(resolved.get("productId") or "")
            if (
                re.fullmatch(r"gid://shopify/Product/\d+", resolved_id)
                and (not expected_id or resolved_id == expected_id)
            ):
                return resolved_id, resolved

    if expected_id:
        try:
            resolved = service_clients.commerce_product_by_id(expected_id)
        except Exception as exc:  # noqa: BLE001
            _LOG.info(
                "[dsers-sourcing] exact Shopify GID %s not readable yet: %s",
                expected_id,
                exc,
            )
        else:
            if str(resolved.get("productId") or "") == expected_id:
                return expected_id, resolved
    return None


def _poll_post_push_binding(
    client: DSersMCPClient,
    store_id: str,
    source_url: str,
    pushed: dict[str, Any],
) -> tuple[dict[str, Any], str, dict[str, Any]] | None:
    """Poll read-only state until DSers and Shopify expose one exact binding."""
    pushed_records = _exact_binding_records(pushed)
    fallback: tuple[dict[str, Any], str, dict[str, Any]] | None = None
    for attempt in range(_BIND_LOOKUP_ATTEMPTS):
        live: dict[str, Any] | None = None
        try:
            live = _reusable_product_by_source(client, store_id, source_url)
        except Exception as exc:  # noqa: BLE001
            # Reads are safe to retry. Never repeat import or confirmed push,
            # including after the CloudFront 504-with-server-success case.
            _LOG.info(
                "[dsers-sourcing] DSers post-push lookup attempt %d failed: %s",
                attempt + 1,
                exc,
            )

        candidates = ([live] if live else []) + pushed_records
        for record in candidates:
            binding = _resolve_exact_shopify_product(record)
            if not binding:
                continue
            product_id, resolved = binding
            exact = (record, product_id, resolved)
            if _dsers_product_id(record):
                return exact
            fallback = fallback or exact

        if attempt + 1 < _BIND_LOOKUP_ATTEMPTS:
            time.sleep(_BIND_LOOKUP_DELAY_SECONDS)
    return fallback


def _bind_live_variant_skus(
    economics: list[VariantEconomics],
    resolved: dict[str, Any],
) -> list[VariantEconomics]:
    live = resolved.get("variants")
    if not isinstance(live, list) or not live:
        raise DSersSourcingUnavailable(
            "the exact Shopify product exposed no variants for supplier binding"
        )
    if len(live) == 1:
        sku = str(live[0].get("sku") or "").strip()
        if (
            not sku
            or _int(live[0].get("inventoryQuantity")) <= 0
            or (_decimal(live[0].get("price")) or Decimal("0")) <= 0
        ):
            raise DSersSourcingUnavailable(
                "the exact Shopify variant is not sellable with a real SKU"
            )
        exact = [
            variant
            for variant in economics
            if variant.sku == sku
            or (
                variant.shopify_variant_id
                and variant.shopify_variant_id
                == str(live[0].get("variantId") or "")
            )
        ]
        # A product with one preview row and one live variant is inherently
        # one-to-one within the already exact product GID. This preserves the
        # single-variant DSers path without using its title as an identifier.
        if len(exact) == 1:
            selected = exact[0]
        elif len(economics) == 1:
            selected = economics[0]
        else:
            raise DSersSourcingUnavailable(
                "the sole Shopify variant did not map to one supplier variant "
                "by exact SKU or variant ID"
            )
        return [
            VariantEconomics(
                sku,
                selected.cost,
                selected.sale_price,
                selected.stock,
                str(live[0].get("variantId") or ""),
            )
        ]

    sku_counts: dict[str, int] = {}
    for item in live:
        sku = str(item.get("sku") or "").strip()
        if sku:
            sku_counts[sku] = sku_counts.get(sku, 0) + 1

    candidates: list[tuple[Decimal, int, str, str, VariantEconomics]] = []
    for item in live:
        sku = str(item.get("sku") or "").strip()
        sku_option_values = _shopify_sku_option_values(sku)
        variant_id = str(item.get("variantId") or "").strip()
        price = _decimal(item.get("price"))
        inventory = _int(item.get("inventoryQuantity"))
        if (
            not sku
            or sku_counts.get(sku) != 1
            or not re.fullmatch(r"gid://shopify/ProductVariant/\d+", variant_id)
            or price is None
            or price <= 0
            or inventory <= 0
        ):
            continue
        matches = [
            variant
            for variant in economics
            if variant.sku == sku
            or (
                variant.shopify_variant_id
                and variant.shopify_variant_id == variant_id
            )
            or (
                variant.option_values
                and variant.option_values == sku_option_values
            )
        ]
        if len(matches) != 1:
            continue
        matched = matches[0]
        candidates.append(
            (
                price,
                -inventory,
                sku,
                variant_id,
                VariantEconomics(
                    sku=sku,
                    cost=matched.cost,
                    sale_price=matched.sale_price,
                    stock=matched.stock,
                    shopify_variant_id=variant_id,
                ),
            )
        )
    if not candidates:
        _LOG.info(
            "[dsers-sourcing] no exact sellable variant binding "
            "preview=%d option_backed=%d live=%d structured_skus=%d",
            len(economics),
            sum(bool(variant.option_values) for variant in economics),
            len(live),
            sum(
                bool(
                    _shopify_sku_option_values(
                        str(item.get("sku") or "").strip()
                    )
                )
                for item in live
            ),
        )
        raise DSersSourcingUnavailable(
            "this product could not be bound to one sellable Shopify variant "
            "by exact SKU, SKU-encoded option identity, or variant ID; Relay "
            "did not use title matching"
        )
    candidates.sort(key=lambda candidate: candidate[:4])
    _LOG.info(
        "[dsers-sourcing] bound exact sellable variant sku=%s variant_id=%s",
        candidates[0][2],
        candidates[0][3],
    )
    return [candidates[0][4]]


def _supplier_product_id(source_url: str) -> str:
    match = re.search(r"/item/(\d+)", source_url)
    return match.group(1) if match else ""


def dsers_store_push(
    import_item_id: str,
    source_url: str,
    budget: Decimal,
    *,
    client: DSersMCPClient | None = None,
) -> dict[str, Any]:
    """Push live only after local margin proof; reconcile errors by read."""
    active_client = client or DSersMCPClient()
    preview = dsers_product_preview(import_item_id, client=active_client)
    variants = validate_margin(preview, budget)
    store_id, _store = _discover_store(active_client)
    live_before = _reusable_product_by_source(
        active_client, store_id, source_url
    )
    push_arguments = {
        "import_item_id": import_item_id,
        "target_store": store_id,
        "visibility_mode": "sell_immediately",
        "force_push": False,
    }
    binding: tuple[dict[str, Any], str, dict[str, Any]] | None = None
    try:
        if live_before:
            pushed = {
                "reused_existing_push": True,
                "product": live_before,
            }
        else:
            # DSers requires a non-mutating confirmation envelope before a
            # live push. Relay's caller has already authorized autonomous
            # sourcing, and the margin/budget checks above are the bounded
            # consent policy.
            confirmation = active_client.call_tool(
                "dsers_store_push", push_arguments
            )
            pushed = active_client.call_tool(
                "dsers_store_push",
                {**push_arguments, "confirm": True},
            )
            pushed["confirmation"] = confirmation
    except Exception as exc:  # noqa: BLE001
        binding = _poll_post_push_binding(
            active_client,
            store_id,
            source_url,
            {},
        )
        if not binding:
            raise DSersSourcingUnavailable(
                "DSers push returned an ambiguous failure and bounded read-only "
                "polling did not prove success; Relay did not retry the push"
            ) from exc
        pushed = {
            "reconciled_after_error": str(exc),
            "product": binding[0],
        }

    binding = binding or _poll_post_push_binding(
        active_client,
        store_id,
        source_url,
        pushed,
    )
    if not binding:
        raise DSersSourcingUnavailable(
            "this product could not be bound to an exact Shopify product after "
            "bounded read-only polling; Relay did not use title matching"
        )
    product_record, product_id, resolved = binding
    dsers_product_id = _dsers_product_id(product_record)
    if not dsers_product_id:
        raise DSersSourcingUnavailable(
            "the exact Shopify product binding exposed no DSers product ID"
        )
    variants = _bind_live_variant_skus(variants, resolved)
    metadata = service_clients.commerce_mark_sourced_product(
        {
            "productId": product_id,
            "vendor": _AUTONOMOUS_VENDOR,
            "tags": _AUTONOMOUS_TAGS,
            "importItemId": import_item_id,
            "sourceUrl": source_url,
            "supplierProductId": _supplier_product_id(source_url),
            "dsersProductId": dsers_product_id,
            "capturedAt": date.today().isoformat(),
            "shipTo": settings.dsers_ship_to,
            "variants": [
                {
                    "sku": variant.sku,
                    "cost": format(variant.cost, "f"),
                    "supplierInventory": variant.stock,
                }
                for variant in variants
            ],
        }
    )
    return {
        "status": "sourced",
        "provider": "dsers",
        "productId": product_id,
        "importItemId": import_item_id,
        "sourceUrl": source_url,
        "metadata": metadata,
        "push": pushed,
    }


def source_missing_product(
    query: str,
    budget_amount: float,
    *,
    client: DSersMCPClient | None = None,
) -> dict[str, Any]:
    """Run search→dedupe/import→preview→margin check→push once."""
    assert_query_allowed(query)
    budget = Decimal(str(budget_amount))
    if budget <= 0:
        raise ValueError("budget must be positive")
    active_client = client or DSersMCPClient()
    request = SourcingRequest(query=query, budget=budget)
    try:
        search = dsers_find_product(query, client=active_client)
        candidates = _records_with_url(search)
        if not candidates:
            raise DSersSourcingUnavailable(
                "DSers found no import-ready supplier product"
            )
        multiplier = (
            Decimal("1")
            + Decimal(str(settings.markup_pct)) / Decimal("100")
        )
        # DSers sets the Shopify catalog price from cost, then Relay applies
        # its broker markup to that reviewed catalog price.
        max_cost = budget / multiplier / multiplier
        affordable = [
            record
            for record in candidates
            if _candidate_cost(record) <= max_cost
        ]
        # dsers_find_product was explicitly requested with sort=relevance.
        # Preserve that supplier-ranked order; cost is an eligibility boundary,
        # not a reason to replace the best match with the cheapest unrelated
        # item.
        selected = (affordable or candidates)[0]
        source_url = _source_url(selected)
        _LOG.info(
            "[dsers-sourcing] selected supplier source query=%r url=%s",
            query,
            source_url,
        )
        imported = dsers_product_import(
            source_url, request, client=active_client
        )
        preview = dsers_product_preview(
            imported["import_item_id"], client=active_client
        )
        validate_margin(preview, budget)
        return dsers_store_push(
            imported["import_item_id"],
            source_url,
            budget,
            client=active_client,
        )
    except ValueError:
        raise
    except Exception as exc:  # noqa: BLE001
        _LOG.warning(
            "[dsers-sourcing] unavailable query=%r; existing catalog remains "
            "active: %s",
            query,
            exc,
        )
        if isinstance(exc, DSersSourcingUnavailable):
            raise
        if isinstance(exc, DSersMCPError):
            raise DSersSourcingUnavailable(
                f"DSers MCP is unavailable: {exc}"
            ) from exc
        raise DSersSourcingUnavailable(
            f"DSers autonomous sourcing is unavailable: {exc}"
        ) from exc
