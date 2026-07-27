from __future__ import annotations

import unittest
from unittest.mock import patch

from agentic_broker.common import llm
from agentic_broker.buyer import tools as buyer_tools
from agentic_broker.shopping import tools


def product(
    sku: str,
    title: str,
    price: str,
    inventory: int,
) -> dict:
    return {
        "productId": f"gid://shopify/Product/{sku}",
        "variantId": f"gid://shopify/ProductVariant/{sku}",
        "sku": sku,
        "title": title,
        "description": title,
        "price": price,
        "inventoryQuantity": inventory,
        "status": "ACTIVE",
        "tags": [],
        "supplierCost": {
            "amount": price,
            "currency": "USD",
            "source": "dsers_mcp_snapshot",
            "capturedAt": "2026-07-27",
            "shipTo": "US",
            "supplierUrl": f"https://supplier.test/{sku}",
        },
    }


class CatalogSourcingTest(unittest.TestCase):
    def test_filters_out_of_stock_and_marked_up_over_budget_before_ranking(
        self,
    ) -> None:
        products = [
            product("OUT", "Wireless Earbuds Out", "1.00", 0),
            product("OVER", "Wireless Earbuds Premium", "5.00", 10),
            product("REAL", "Wireless Earbuds Mini", "4.00", 10),
        ]

        with (
            patch.object(
                tools.service_clients,
                "commerce_products",
                return_value={"products": products},
            ),
            patch.object(
                tools.llm,
                "source_offer",
                side_effect=lambda _query, _budget, candidates: candidates[0],
            ) as ranker,
        ):
            offer = tools.source_and_price("wireless earbuds", 5.00)

        candidates = ranker.call_args.args[2]
        self.assertEqual([candidate["sku"] for candidate in candidates], ["REAL"])
        self.assertEqual(offer["variantId"], products[2]["variantId"])
        self.assertEqual(offer["price"], 4.60)
        self.assertEqual(offer["supplierCost"]["amount"], "4.00")

    def test_sale_price_uses_reviewed_catalog_price_and_margin_uses_dsers_cost(
        self,
    ) -> None:
        item = product("F9", "Sports Waterproof Earbuds", "3.96", 10)
        item["price"] = "3.95"
        with (
            patch.object(
                tools.service_clients,
                "commerce_products",
                return_value={"products": [item]},
            ),
            patch.object(
                tools.llm,
                "source_offer",
                side_effect=lambda _query, _budget, candidates: candidates[0],
            ),
        ):
            offer = tools.source_and_price("sports waterproof earbuds", 5.00)

        self.assertEqual(offer["catalogPrice"], 3.95)
        self.assertEqual(offer["cost"], 3.96)
        self.assertEqual(offer["price"], 4.54)
        self.assertEqual(offer["supplierCost"]["amount"], "3.96")

    def test_rejects_products_without_a_supplier_cost_snapshot(self) -> None:
        missing = product("MISSING", "Unknown-cost Earbuds", "1.00", 10)
        missing["supplierCost"] = None
        with patch.object(
            tools.service_clients,
            "commerce_products",
            return_value={"products": [missing]},
        ):
            with self.assertRaisesRegex(ValueError, "no in-stock catalog product"):
                tools.source_and_price("earbuds", 5.00)

    def test_public_catalog_never_returns_the_private_supplier_snapshot(self) -> None:
        item = product("PRIVATE", "Private-cost Earbuds", "3.96", 10)
        item["price"] = "3.95"
        with patch.object(
            buyer_tools.service_clients,
            "commerce_products",
            return_value={"products": [item]},
        ):
            result = buyer_tools.search_catalog("earbuds", 5.00)

        public = result["products"][0]
        self.assertEqual(public["catalogPrice"], "3.95")
        self.assertEqual(public["price"], "4.54")
        self.assertNotIn("supplierCost", public)
        self.assertNotIn("supplierUrl", public)

    def test_raises_when_no_real_variant_can_meet_the_budget(self) -> None:
        with patch.object(
            tools.service_clients,
            "commerce_products",
            return_value={
                "products": [product("OVER", "Premium Headphones", "9.00", 5)]
            },
        ):
            with self.assertRaisesRegex(ValueError, "no in-stock catalog product"):
                tools.source_and_price("headphones", 5.00)

    def test_catalog_query_failure_retries_the_full_real_catalog(self) -> None:
        available = product("REAL", "Wireless Earbuds Mini", "2.50", 10)
        with (
            patch.object(
                tools.service_clients,
                "commerce_products",
                side_effect=[
                    RuntimeError("search unavailable"),
                    {"products": [available]},
                ],
            ) as catalog,
            patch.object(
                tools.llm,
                "source_offer",
                side_effect=lambda _query, _budget, candidates: candidates[0],
            ),
        ):
            offer = tools.source_and_price("wireless earbuds", 5.00)

        self.assertEqual(offer["sku"], "REAL")
        self.assertEqual(catalog.call_args_list[1].args, ("",))
        self.assertEqual(catalog.call_args_list[1].kwargs, {"limit": 50})

    def test_no_gemini_key_uses_deterministic_catalog_relevance(self) -> None:
        candidates = [
            product("MOUSE", "Silent Wireless Mouse", "1.00", 10),
            product("BUDS", "Mini Wireless Earbuds", "2.00", 10),
        ]
        with patch.object(llm, "gemini_available", return_value=False):
            selected = llm.source_offer(
                "wireless earbuds",
                5.00,
                candidates,
            )
        self.assertEqual(selected["sku"], "BUDS")

    def test_relevance_handles_long_supplier_titles_and_descriptions(self) -> None:
        earbuds = product(
            "14:193#black",
            (
                "TWS F9-5 Earphone Bluetooth 5.3 Wireless Headphones "
                "Hifi Stereo Sports Waterproof Earbuds Headset"
            ),
            "3.95",
            1245,
        )
        earbuds["description"] = (
            "SPECIFICATIONSActive Noise-Cancellation: Yes "
            "Package List: User Manual, Charging case, Charging Cable"
        )
        unrelated = product(
            "CASE",
            "Protective Smartphone Carrying Case",
            "1.00",
            500,
        )
        unrelated["description"] = (
            "Compatible with sports use. Waterproof coating. "
            "Store your earbuds beside the phone."
        )

        with patch.object(llm, "gemini_available", return_value=False):
            selected = llm.source_offer(
                "sports waterproof earbuds",
                5.00,
                [unrelated, earbuds],
            )

        self.assertEqual(selected["sku"], "14:193#black")


if __name__ == "__main__":
    unittest.main()
