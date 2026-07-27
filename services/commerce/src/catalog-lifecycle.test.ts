import assert from "node:assert/strict";
import test from "node:test";
import type { CatalogProduct } from "@arb/shared";
import {
  deliveredProductIds,
  hasAuthoritativeDeliverySignal,
  reconcileCompletedCatalogLifecycle,
  retireAutonomousSourcedProduct,
  type CatalogLifecycleFulfillment,
} from "./catalog-lifecycle.js";
import {
  forgetCatalogProduct,
  mergeRecentlySourcedProducts,
  rememberRecentlySourcedProduct,
} from "./catalog.js";

function fulfillment(
  productIds: string[],
  overrides: Partial<CatalogLifecycleFulfillment> = {},
): CatalogLifecycleFulfillment {
  return {
    id: "gid://shopify/Fulfillment/1",
    status: "SUCCESS",
    trackingInfo: [
      {
        company: "UPS",
        number: "1Z9999999999999999",
        url: "https://carrier.test/track/1Z9999999999999999",
      },
    ],
    fulfillmentLineItems: {
      nodes: productIds.map((productId) => ({
        lineItem: { variant: { product: { id: productId } } },
      })),
    },
    ...overrides,
  };
}

test("delivery proof requires SUCCESS plus carrier and tracking number", () => {
  const complete = fulfillment(["gid://shopify/Product/1"]);
  assert.equal(hasAuthoritativeDeliverySignal(complete), true);
  assert.equal(
    hasAuthoritativeDeliverySignal({
      ...complete,
      status: "FAILURE",
    }),
    false,
  );
  assert.equal(
    hasAuthoritativeDeliverySignal({
      ...complete,
      trackingInfo: [{ company: "UPS", number: null, url: null }],
    }),
    false,
  );
  assert.equal(
    hasAuthoritativeDeliverySignal({
      ...complete,
      trackingInfo: [{ company: null, number: "waybill", url: null }],
    }),
    false,
  );
});

test("only proven fulfillment line items produce exact product GIDs", () => {
  assert.deepEqual(
    deliveredProductIds({
      id: "gid://shopify/Order/1",
      name: "#1",
      fulfillments: [
        fulfillment(["gid://shopify/Product/1", "not-a-product"]),
        fulfillment(["gid://shopify/Product/2"], { status: "FAILURE" }),
      ],
    }),
    ["gid://shopify/Product/1"],
  );
});

test("fulfilled sourced product is drafted by exact GID and never deleted", async () => {
  const productId = "gid://shopify/Product/100";
  const calls: { query: string; variables: Record<string, unknown> }[] = [];
  const graphql = async <T>(
    query: string,
    variables: Record<string, unknown>,
  ): Promise<T> => {
    calls.push({ query, variables });
    if (query.includes("InspectCatalogLifecycleProduct")) {
      return {
        product: {
          id: productId,
          vendor: "Relay DSers Autonomous",
          status: "ACTIVE",
          tags: [],
        },
      } as T;
    }
    if (query.includes("DraftFulfilledAutonomousProduct")) {
      return {
        productUpdate: {
          product: {
            id: productId,
            vendor: "Relay DSers Autonomous",
            status: "DRAFT",
            tags: [],
          },
          userErrors: [],
        },
      } as T;
    }
    throw new Error("unexpected query");
  };

  const result = await retireAutonomousSourcedProduct(productId, { graphql });

  assert.deepEqual(result, {
    productId,
    action: "retired",
    previousStatus: "ACTIVE",
  });
  const mutation = calls.find((call) =>
    call.query.includes("DraftFulfilledAutonomousProduct"),
  );
  assert.deepEqual(mutation?.variables, {
    product: { id: productId, status: "DRAFT" },
  });
  assert.ok(calls.every((call) => !/delete/i.test(call.query)));
});

test("six human products remain active while one sourced product is retired", async () => {
  const sourcedId = "gid://shopify/Product/700";
  const humanIds = Array.from(
    { length: 6 },
    (_value, index) => `gid://shopify/Product/${index + 1}`,
  );
  const drafted: string[] = [];
  const graphql = async <T>(
    query: string,
    variables: Record<string, unknown>,
  ): Promise<T> => {
    if (query.includes("RelayOrderCatalogLifecycle")) {
      return {
        orders: {
          nodes: [
            {
              id: "gid://shopify/Order/1",
              name: "#1",
              fulfillments: [fulfillment([...humanIds, sourcedId])],
            },
          ],
          pageInfo: { hasNextPage: false, endCursor: null },
        },
      } as T;
    }
    if (query.includes("InspectCatalogLifecycleProduct")) {
      const id = String(variables.id);
      return {
        product: {
          id,
          vendor: id === sourcedId ? "Relay DSers Autonomous" : "Human Vendor",
          status: "ACTIVE",
          tags: id === sourcedId ? [] : ["curated"],
        },
      } as T;
    }
    if (query.includes("DraftFulfilledAutonomousProduct")) {
      const id = String(
        (variables.product as Record<string, unknown>).id,
      );
      drafted.push(id);
      return {
        productUpdate: {
          product: {
            id,
            vendor: "Relay DSers Autonomous",
            status: "DRAFT",
            tags: [],
          },
          userErrors: [],
        },
      } as T;
    }
    throw new Error("unexpected query");
  };

  const results = await reconcileCompletedCatalogLifecycle({ graphql });

  assert.deepEqual(drafted, [sourcedId]);
  assert.equal(
    results.filter((result) => result.action === "not_autonomous").length,
    6,
  );
  assert.equal(
    results.find((result) => result.productId === sourcedId)?.action,
    "retired",
  );
});

test("missing delivery proof performs no product reads or writes", async () => {
  let productCalls = 0;
  const graphql = async <T>(query: string): Promise<T> => {
    if (query.includes("RelayOrderCatalogLifecycle")) {
      return {
        orders: {
          nodes: [
            {
              id: "gid://shopify/Order/2",
              name: "#2",
              fulfillments: [
                fulfillment(["gid://shopify/Product/800"], {
                  trackingInfo: [],
                }),
              ],
            },
          ],
          pageInfo: { hasNextPage: false, endCursor: null },
        },
      } as T;
    }
    productCalls += 1;
    throw new Error("product mutation must not run without delivery proof");
  };

  assert.deepEqual(
    await reconcileCompletedCatalogLifecycle({ graphql }),
    [],
  );
  assert.equal(productCalls, 0);
});

test("recent sourcing is immediately searchable until Shopify catches up", () => {
  const product: CatalogProduct = {
    productId: "gid://shopify/Product/900",
    variantId: "gid://shopify/ProductVariant/901",
    sku: "RELAY-RECENT",
    title: "Recently sourced lamp",
    description: "Immediate catalog visibility",
    price: "2.84",
    inventoryQuantity: 13,
    status: "ACTIVE",
    tags: ["relay:autonomous-sourced"],
    supplierCost: {
      amount: "2.47",
      currency: "USD",
      source: "dsers_mcp_snapshot",
      capturedAt: "2026-07-27",
      shipTo: "US",
      supplierUrl:
        "https://www.aliexpress.com/item/1005010477996975.html",
    },
  };
  const now = 1_000;

  rememberRecentlySourcedProduct(product, now);
  assert.deepEqual(mergeRecentlySourcedProducts([], now), [product]);
  assert.deepEqual(mergeRecentlySourcedProducts([product], now), [product]);
  assert.deepEqual(mergeRecentlySourcedProducts([], now), []);

  rememberRecentlySourcedProduct(product, now);
  forgetCatalogProduct(product.productId);
  assert.deepEqual(mergeRecentlySourcedProducts([], now), []);
});
