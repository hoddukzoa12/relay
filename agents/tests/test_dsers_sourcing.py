from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from agentic_broker.shopping import dsers_sourcing


SOURCE_URL = "https://www.aliexpress.com/item/1005001234567890.html"


@pytest.fixture(autouse=True)
def _isolated_target_store(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin the store filter to this module's fake store.

    `settings` is loaded from the developer's `.env`, so a real
    DSERS_TARGET_STORE would filter the fake catalogue down to nothing and
    fail these tests everywhere except CI, which has no `.env`.
    """
    monkeypatch.setattr(
        dsers_sourcing,
        "settings",
        replace(dsers_sourcing.settings, dsers_target_store="store-1"),
    )


def test_shopify_sku_option_values_parses_single_option() -> None:
    assert dsers_sourcing._shopify_sku_option_values("14:193#Black") == ("Black",)


def test_shopify_sku_option_values_rejects_unstructured_sku() -> None:
    assert dsers_sourcing._shopify_sku_option_values("WATCH-BLACK") == ()


class FakeDSers:
    def __init__(
        self,
        *,
        import_error: bool = False,
        push_error: bool = False,
        offselling: bool = False,
    ):
        self.calls: list[tuple[str, dict]] = []
        self.import_error = import_error
        self.push_error = push_error
        self.offselling = offselling
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
                            **(
                                {"status": "offselling"}
                                if self.offselling
                                else {}
                            ),
                        }
                    ]
                    if self.pushed
                    else []
                )
            }
        raise AssertionError(name)


class DelayedBindingDSers(FakeDSers):
    def __init__(self, delayed_reads: int = 2):
        super().__init__()
        self.delayed_reads = delayed_reads
        self.post_push_reads = 0

    def call_tool(self, name: str, arguments: dict) -> dict:
        if name == "dsers_my_products" and self.pushed:
            self.calls.append((name, arguments))
            self.post_push_reads += 1
            if self.post_push_reads <= self.delayed_reads:
                return {"products": []}
            return {
                "products": [
                    {
                        "source_url": SOURCE_URL,
                        "shopify_product_id": "1234",
                        "dsers_product_id": "dsers-9",
                        "store_handle": "relay-smart-watch",
                    }
                ]
            }
        return super().call_tool(name, arguments)


class MultiVariantDSers(FakeDSers):
    def call_tool(self, name: str, arguments: dict) -> dict:
        if name == "dsers_product_preview":
            self.calls.append((name, arguments))
            return {
                "skus": [
                    ["name", "sell", "compare_at", "cost", "qty", "supplier_qty"],
                    ["Red-iPhone 17", "3.20", "3.20", "2.10", 12, 12],
                    ["Blue-iPhone 17", "2.80", "2.80", "1.90", 8, 8],
                    ["Green-iPhone 17", "2.80", "2.80", "1.95", 9, 9],
                ],
                "options": [
                    {"name": "Color", "values": ["Red", "Blue", "Green"]},
                    {"name": "Model", "values": ["iPhone 17"]},
                ]
            }
        return super().call_tool(name, arguments)


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


def resolved_multi_product(_handle: str) -> dict:
    return {
        "productId": "gid://shopify/Product/1234",
        "handle": "relay-phone-case",
        "variants": [
            {
                "variantId": "gid://shopify/ProductVariant/6001",
                "sku": "10:365212#iPhone 17;14:10#Red",
                "title": "Red",
                "price": "3.20",
                "inventoryQuantity": 12,
            },
            {
                "variantId": "gid://shopify/ProductVariant/6002",
                "sku": "10:365212#iPhone 17;14:173#Blue",
                "title": "Blue",
                "price": "2.80",
                "inventoryQuantity": 8,
            },
            {
                "variantId": "gid://shopify/ProductVariant/6003",
                "sku": "10:365212#iPhone 17;14:175#Green",
                "title": "Green",
                "price": "2.80",
                "inventoryQuantity": 9,
            },
            {
                "variantId": "gid://shopify/ProductVariant/6004",
                "sku": "SHOP-YELLOW",
                "title": "CASE-YELLOW",
                "price": "2.60",
                "inventoryQuantity": 10,
            },
        ],
    }


def multi_metadata_response(payload: dict) -> dict:
    selected = payload["variants"]
    assert selected == [
        {
            "sku": "10:365212#iPhone 17;14:175#Green",
            "cost": "1.95",
            "supplierInventory": 9,
        }
    ]
    return {
        "product": {
            "productId": "gid://shopify/Product/1234",
            "variantId": "gid://shopify/ProductVariant/6003",
            "sku": "10:365212#iPhone 17;14:175#Green",
            "title": "iPhone Case",
            "description": "",
            "price": "2.80",
            "inventoryQuantity": 9,
            "status": "ACTIVE",
            "tags": ["relay:autonomous-sourced", "relay:dsers"],
            "supplierCost": {
                "amount": "1.95",
                "currency": "USD",
                "source": "dsers_mcp_snapshot",
                "capturedAt": "2026-07-27",
                "shipTo": "US",
                "supplierUrl": SOURCE_URL,
            },
        }
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


def test_multi_variant_product_binds_one_deterministic_sellable_sku() -> None:
    client = MultiVariantDSers()
    with patch.object(
        dsers_sourcing.service_clients,
        "commerce_mark_sourced_product",
        side_effect=multi_metadata_response,
    ) as mark, patch.object(
        dsers_sourcing.service_clients,
        "commerce_product_by_handle",
        side_effect=resolved_multi_product,
    ):
        result = dsers_sourcing.source_missing_product(
            "iphone case", 10, client=client
        )

    assert (
        result["metadata"]["product"]["sku"]
        == "10:365212#iPhone 17;14:175#Green"
    )
    assert (
        result["metadata"]["product"]["variantId"]
        == "gid://shopify/ProductVariant/6003"
    )
    assert mark.call_args.args[0]["variants"] == [
        {
            "sku": "10:365212#iPhone 17;14:175#Green",
            "cost": "1.95",
            "supplierInventory": 9,
        }
    ]


def test_multi_variant_binding_never_matches_a_shopify_title() -> None:
    with pytest.raises(
        dsers_sourcing.DSersSourcingUnavailable,
        match="exact SKU",
    ):
        dsers_sourcing._bind_live_variant_skus(
            [
                dsers_sourcing.VariantEconomics(
                    sku="Blue",
                    cost=Decimal("1.00"),
                    sale_price=Decimal("2.00"),
                    stock=5,
                )
            ],
            {
                "variants": [
                    {
                        "variantId": "gid://shopify/ProductVariant/6002",
                        "sku": "CASE-BLUE",
                        "title": "Blue",
                        "price": "2.00",
                        "inventoryQuantity": 5,
                    },
                    {
                        "variantId": "gid://shopify/ProductVariant/6003",
                        "sku": "CASE-GREEN",
                        "title": "Green",
                        "price": "2.10",
                        "inventoryQuantity": 5,
                    },
                ]
            },
        )


def test_supplier_relevance_order_wins_over_a_cheaper_later_result() -> None:
    client = FakeDSers()
    original_call = client.call_tool

    def relevance_first(name: str, arguments: dict) -> dict:
        if name == "dsers_find_product":
            client.calls.append((name, arguments))
            return {
                "products": [
                    {
                        "title": "Smart Watch",
                        "import_url": SOURCE_URL,
                        "cost": "4.00",
                    },
                    {
                        "title": "Unrelated Cheap Item",
                        "import_url":
                            "https://www.aliexpress.com/item/1005009999999999.html",
                        "cost": "1.00",
                    },
                ]
            }
        return original_call(name, arguments)

    client.call_tool = relevance_first  # type: ignore[method-assign]
    with patch.object(
        dsers_sourcing.service_clients,
        "commerce_mark_sourced_product",
        side_effect=metadata_response,
    ), patch.object(
        dsers_sourcing.service_clients,
        "commerce_product_by_handle",
        side_effect=resolved_product,
    ):
        result = dsers_sourcing.source_missing_product(
            "smart watch", 10, client=client
        )

    imported = next(
        arguments
        for name, arguments in client.calls
        if name == "dsers_product_import"
    )
    assert imported["source_url"] == SOURCE_URL
    assert result["sourceUrl"] == SOURCE_URL


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


def test_post_push_binding_polls_reads_without_repeating_mutations() -> None:
    client = DelayedBindingDSers(delayed_reads=2)
    with patch.object(
        dsers_sourcing.service_clients,
        "commerce_mark_sourced_product",
        side_effect=metadata_response,
    ), patch.object(
        dsers_sourcing.service_clients,
        "commerce_product_by_handle",
        side_effect=resolved_product,
    ), patch.object(dsers_sourcing.time, "sleep") as sleep:
        result = dsers_sourcing.dsers_store_push(
            "import-1", SOURCE_URL, Decimal("10"), client=client
        )

    assert result["productId"] == "gid://shopify/Product/1234"
    assert client.post_push_reads == 3
    assert sleep.call_count == 2
    assert [name for name, _args in client.calls].count("dsers_store_push") == 2
    assert sum(
        arguments.get("confirm") is True
        for name, arguments in client.calls
        if name == "dsers_store_push"
    ) == 1


def test_post_push_binding_can_resolve_an_exact_gid_without_a_handle() -> None:
    client = FakeDSers()
    original_call = client.call_tool

    def gid_only(name: str, arguments: dict) -> dict:
        result = original_call(name, arguments)
        if name == "dsers_my_products" and result["products"]:
            result["products"][0].pop("store_handle")
        return result

    client.call_tool = gid_only  # type: ignore[method-assign]
    with patch.object(
        dsers_sourcing.service_clients,
        "commerce_mark_sourced_product",
        side_effect=metadata_response,
    ), patch.object(
        dsers_sourcing.service_clients,
        "commerce_product_by_handle",
    ) as by_handle, patch.object(
        dsers_sourcing.service_clients,
        "commerce_product_by_id",
        side_effect=resolved_product,
    ) as by_id:
        result = dsers_sourcing.dsers_store_push(
            "import-1", SOURCE_URL, Decimal("10"), client=client
        )

    assert result["productId"] == "gid://shopify/Product/1234"
    by_handle.assert_not_called()
    by_id.assert_called_with("gid://shopify/Product/1234")


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


def test_preview_budget_includes_final_broker_markup() -> None:
    preview = {
        "variants": [
            {
                "sku": "NEAR-CAP",
                "cost": "4.00",
                "sell_price": "4.60",
                "stock": 10,
            }
        ]
    }

    with pytest.raises(
        dsers_sourcing.DSersSourcingUnavailable,
        match="final broker quote",
    ):
        dsers_sourcing.validate_margin(preview, Decimal("5"))


def test_existing_exact_source_is_reused_without_another_push() -> None:
    client = FakeDSers()
    client.pushed = True
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

    assert result["push"]["reused_existing_push"] is True
    assert "dsers_store_push" not in [name for name, _args in client.calls]


def test_fulfilled_offselling_product_is_republished_without_duplication() -> None:
    client = FakeDSers(offselling=True)
    client.pushed = True
    with patch.object(
        dsers_sourcing.service_clients,
        "commerce_mark_sourced_product",
        side_effect=metadata_response,
    ) as mark, patch.object(
        dsers_sourcing.service_clients,
        "commerce_product_by_handle",
        side_effect=resolved_product,
    ):
        result = dsers_sourcing.dsers_store_push(
            "import-1", SOURCE_URL, Decimal("10"), client=client
        )

    assert result["push"]["reused_existing_push"] is True
    assert result["metadata"]["product"]["status"] == "ACTIVE"
    assert mark.call_args.args[0]["productId"] == "gid://shopify/Product/1234"
    assert "dsers_store_push" not in [name for name, _args in client.calls]


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
