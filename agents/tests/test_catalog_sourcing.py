from __future__ import annotations

import unittest
from unittest.mock import patch

from agentic_broker.common import llm
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


if __name__ == "__main__":
    unittest.main()
