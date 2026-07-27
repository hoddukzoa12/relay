from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from agentic_broker.shopping import dsers_sourcing


SOURCE_URL = "https://www.aliexpress.com/item/1005001234567890.html"


class FakeDSers:
    def __init__(self, *, import_error: bool = False, push_error: bool = False):
        self.calls: list[tuple[str, dict]] = []
        self.import_error = import_error
        self.push_error = push_error
        self.imported = False
        self.pushed = False

    def call_tool(self, name: str, arguments: dict) -> dict:
        self.calls.append((name, arguments))
        if name == "dsers_find_product":
            return {
                "products": [
                    {
                        "title": "Smart Watch",
                        "import_url": SOURCE_URL,
                        "cost": "4.00",
                    }
                ]
            }
        if name == "dsers_import_list":
            return {
                "items": (
                    [
                        {
                            "source_url": SOURCE_URL,
                            "import_item_id": "import-1",
                        }
                    ]
                    if self.imported
                    else []
                )
            }
        if name == "dsers_product_import":
            self.imported = True
            if self.import_error:
                raise RuntimeError("CloudFront 504")
            return {
                "import_item_id": "import-1",
                "source_url": SOURCE_URL,
            }
        if name == "dsers_product_preview":
            return {
                "import_item_id": "import-1",
                "variants": [
                    {
                        "sku": "WATCH-BLACK",
                        "cost": "4.00",
                        "sell_price": "4.60",
                        "stock": 12,
                    }
                ],
            }
        if name == "dsers_store_discover":
            return {
                "stores": [
                    {
                        "id": "store-1",
                        "name": "Relay",
                        "platform": "shopify",
                    }
                ]
            }
        if name == "dsers_store_push":
            if not arguments.get("confirm"):
                return {"confirmation_required": True}
            self.pushed = True
            if self.push_error:
                raise RuntimeError("CloudFront 504")
            return {"state": "pushed"}
        if name == "dsers_my_products":
            return {
                "products": (
                    [
                        {
                            "source_url": SOURCE_URL,
                            "shopify_product_id": "1234",
                            "dsers_product_id": "dsers-9",
                            "store_handle": "relay-smart-watch",
                        }
                    ]
                    if self.pushed
                    else []
                )
            }
        raise AssertionError(name)


def metadata_response(_payload: dict) -> dict:
    return {
        "product": {
            "productId": "gid://shopify/Product/1234",
            "variantId": "gid://shopify/ProductVariant/5678",
            "sku": "WATCH-BLACK",
            "title": "Smart Watch",
            "description": "",
            "price": "4.60",
            "inventoryQuantity": 12,
            "status": "ACTIVE",
            "tags": ["relay:autonomous-sourced", "relay:dsers"],
            "supplierCost": {
                "amount": "4.00",
                "currency": "USD",
                "source": "dsers_mcp_snapshot",
                "capturedAt": "2026-07-27",
                "shipTo": "US",
                "supplierUrl": SOURCE_URL,
            },
        }
    }


def resolved_product(_handle: str) -> dict:
    return {
        "productId": "gid://shopify/Product/1234",
        "handle": "relay-smart-watch",
        "variants": [
            {
                "variantId": "gid://shopify/ProductVariant/5678",
                "sku": "WATCH-BLACK",
                "title": "Default Title",
                "price": "4.60",
                "inventoryQuantity": 12,
            }
        ],
    }


def test_autonomous_workflow_imports_one_and_binds_exact_product_id() -> None:
    client = FakeDSers()
    with patch.object(
        dsers_sourcing.service_clients,
        "commerce_mark_sourced_product",
        side_effect=metadata_response,
    ) as mark, patch.object(
        dsers_sourcing.service_clients,
        "commerce_product_by_handle",
        side_effect=resolved_product,
    ):
        result = dsers_sourcing.source_missing_product(
            "smart watch", 10, client=client
        )

    assert result["status"] == "sourced"
    assert result["productId"] == "gid://shopify/Product/1234"
    assert [name for name, _args in client.calls].count(
        "dsers_product_import"
    ) == 1
    assert [
        arguments
        for name, arguments in client.calls
        if name == "dsers_store_push" and arguments.get("confirm") is True
    ] == [
        {
            "import_item_id": "import-1",
            "target_store": "store-1",
            "visibility_mode": "sell_immediately",
            "force_push": False,
            "confirm": True,
        }
    ]
    payload = mark.call_args.args[0]
    assert payload["vendor"] == "Relay DSers Autonomous"
    assert payload["tags"] == [
        "relay:autonomous-sourced",
        "relay:dsers",
    ]
    assert payload["productId"] == "gid://shopify/Product/1234"
    assert payload["variants"][0]["cost"] == "4.00"


def test_ambiguous_import_reads_state_and_never_blindly_retries() -> None:
    client = FakeDSers(import_error=True)
    request = dsers_sourcing.SourcingRequest(
        query="smart watch", budget=Decimal("10")
    )

    result = dsers_sourcing.dsers_product_import(
        SOURCE_URL, request, client=client
    )

    assert result["import_item_id"] == "import-1"
    assert "504" in result["reconciled_after_error"]
    assert [name for name, _args in client.calls].count(
        "dsers_product_import"
    ) == 1
    assert [name for name, _args in client.calls].count(
        "dsers_import_list"
    ) == 2


def test_ambiguous_push_reads_live_state_and_never_retries() -> None:
    client = FakeDSers(push_error=True)
    client.imported = True
    with patch.object(
        dsers_sourcing.service_clients,
        "commerce_mark_sourced_product",
        side_effect=metadata_response,
    ), patch.object(
        dsers_sourcing.service_clients,
        "commerce_product_by_handle",
        side_effect=resolved_product,
    ):
        result = dsers_sourcing.dsers_store_push(
            "import-1", SOURCE_URL, Decimal("10"), client=client
        )

    assert result["productId"] == "gid://shopify/Product/1234"
    assert [name for name, _args in client.calls].count("dsers_store_push") == 2
    assert sum(
        arguments.get("confirm") is True
        for name, arguments in client.calls
        if name == "dsers_store_push"
    ) == 1
    assert [name for name, _args in client.calls].count("dsers_my_products") >= 1


def test_non_positive_margin_blocks_push() -> None:
    client = FakeDSers()

    def unsafe(name: str, arguments: dict) -> dict:
        if name == "dsers_product_preview":
            client.calls.append((name, arguments))
            return {
                "variants": [
                    {
                        "sku": "LOSS",
                        "cost": "5.00",
                        "sell_price": "4.99",
                        "stock": 10,
                    }
                ]
            }
        return FakeDSers.call_tool(client, name, arguments)

    client.call_tool = unsafe  # type: ignore[method-assign]
    with pytest.raises(
        dsers_sourcing.DSersSourcingUnavailable,
        match="non-positive-margin",
    ):
        dsers_sourcing.dsers_store_push(
            "import-1", SOURCE_URL, Decimal("10"), client=client
        )
    assert "dsers_store_push" not in [name for name, _args in client.calls]


def test_preview_parses_dsers_tabular_skus() -> None:
    preview = {
        "skus": [
            ["name", "sell", "compare_at", "cost", "qty", "supplier_qty"],
            ["Default Title", 2.84, 2.84, 2.47, 13, 13],
        ]
    }

    variants = dsers_sourcing.validate_margin(preview, Decimal("5"))

    assert variants == [
        dsers_sourcing.VariantEconomics(
            sku="Default Title",
            cost=Decimal("2.47"),
            sale_price=Decimal("2.84"),
            stock=13,
        )
    ]


@pytest.mark.parametrize(
    "query",
    [
        "prescription medicine",
        "counterfeit handbag",
        "spy camera surveillance kit",
        "전자담배 찾아줘",
        "감시장비",
    ],
)
def test_prohibited_queries_never_reach_dsers(query: str) -> None:
    client = FakeDSers()
    with pytest.raises(ValueError, match="regulated or prohibited"):
        dsers_sourcing.dsers_find_product(query, client=client)
    assert client.calls == []


def test_per_request_import_cap_stops_second_mutation() -> None:
    client = FakeDSers()
    request = dsers_sourcing.SourcingRequest(
        query="smart watch", budget=Decimal("10")
    )
    second_url = "https://www.aliexpress.com/item/1005009999999999.html"
    with patch.object(
        dsers_sourcing,
        "settings",
        SimpleNamespace(
            dsers_max_imports_per_request=1,
            markup_pct=15,
            dsers_ship_to="US",
            dsers_target_store="",
        ),
    ):
        dsers_sourcing.dsers_product_import(
            SOURCE_URL, request, client=client
        )
        client.imported = False
        with pytest.raises(
            dsers_sourcing.DSersSourcingUnavailable,
            match="import cap reached",
        ):
            dsers_sourcing.dsers_product_import(
                second_url, request, client=client
            )
    assert [name for name, _args in client.calls].count(
        "dsers_product_import"
    ) == 1
