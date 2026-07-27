import assert from "node:assert/strict";
import test from "node:test";
import {
  markAutonomousSourcedProduct,
  resolveShopifyProductByHandle,
} from "./sourcing.js";

const sourceUrl =
  "https://www.aliexpress.com/item/1005001234567890.html";

function input() {
  return {
    productId: "gid://shopify/Product/1234",
    vendor: "Relay DSers Autonomous",
    tags: ["relay:autonomous-sourced", "relay:dsers"],
    importItemId: "import-1",
    sourceUrl,
    supplierProductId: "1005001234567890",
    dsersProductId: "dsers-9",
    capturedAt: "2026-07-27",
    shipTo: "US",
    variants: [
      { sku: "WATCH-BLACK", cost: "4.00", supplierInventory: 12 },
    ],
  };
}

const baseProduct = {
  id: "gid://shopify/Product/1234",
  title: "Smart Watch",
  description: "A watch",
  vendor: "DSers",
  status: "DRAFT",
  tags: ["supplier"],
  variants: {
    nodes: [
      {
        id: "gid://shopify/ProductVariant/5678",
        sku: "WATCH-BLACK",
        price: "4.60",
        inventoryQuantity: 12,
        metafields: { nodes: [] as Record<string, string>[] },
      },
    ],
  },
};

test("marks exact product/variant identities and verifies readback", async () => {
  const calls: { query: string; variables: Record<string, unknown> }[] = [];
  let readback = false;
  const graphql = async <T>(
    query: string,
    variables: Record<string, unknown>,
  ): Promise<T> => {
    calls.push({ query, variables });
    if (query.includes("InspectAutonomousSourcing")) {
      const product = structuredClone(baseProduct);
      if (readback) {
        product.vendor = "Relay DSers Autonomous";
        product.status = "ACTIVE";
        product.tags.push("relay:autonomous-sourced", "relay:dsers");
        product.variants.nodes[0]!.metafields.nodes = [
          {
            namespace: "relay",
            key: "supplier_cost",
            type: "number_decimal",
            value: "4.00",
          },
          {
            namespace: "relay",
            key: "supplier_cost_currency",
            type: "single_line_text_field",
            value: "USD",
          },
          {
            namespace: "relay",
            key: "supplier_cost_source",
            type: "single_line_text_field",
            value: "dsers_mcp_snapshot",
          },
          {
            namespace: "relay",
            key: "supplier_cost_captured_at",
            type: "date",
            value: "2026-07-27",
          },
          {
            namespace: "relay",
            key: "supplier_cost_ship_to",
            type: "single_line_text_field",
            value: "US",
          },
          {
            namespace: "relay",
            key: "supplier_url",
            type: "url",
            value: sourceUrl,
          },
          {
            namespace: "relay",
            key: "supplier_product_id",
            type: "single_line_text_field",
            value: "1005001234567890",
          },
          {
            namespace: "relay",
            key: "dsers_product_id",
            type: "single_line_text_field",
            value: "dsers-9",
          },
        ];
      }
      return {
        product,
        metafieldDefinitions: { nodes: [] },
      } as T;
    }
    if (query.includes("MarkAutonomousSourcing")) {
      return {
        productUpdate: {
          product: {
            id: input().productId,
            vendor: input().vendor,
            status: "ACTIVE",
            tags: input().tags,
          },
          userErrors: [],
        },
      } as T;
    }
    if (query.includes("SetAutonomousSupplierCosts")) {
      readback = true;
      return {
        metafieldsSet: {
          metafields: [],
          userErrors: [],
        },
      } as T;
    }
    throw new Error("unexpected query");
  };

  const result = await markAutonomousSourcedProduct(input(), {
    graphql,
    mock: false,
  });

  assert.equal(
    (result.product as Record<string, unknown>).productId,
    input().productId,
  );
  assert.equal(
    (
      (result.product as Record<string, unknown>)
        .supplierCost as Record<string, unknown>
    ).amount,
    "4.00",
  );
  const update = calls.find((call) =>
    call.query.includes("MarkAutonomousSourcing"),
  );
  assert.deepEqual(
    (update!.variables.product as Record<string, unknown>).tags,
    ["supplier", "relay:autonomous-sourced", "relay:dsers"],
  );
  assert.equal(
    (update!.variables.product as Record<string, unknown>).status,
    "ACTIVE",
  );
  assert.ok(
    calls.every(
      (call) =>
        JSON.stringify(call.variables).includes(input().productId) ||
        call.query.includes("SetAutonomousSupplierCosts"),
    ),
  );
});

test("never falls back to title matching when the product ID is absent", async () => {
  await assert.rejects(
    markAutonomousSourcedProduct(input(), {
      mock: false,
      graphql: async <T>() =>
        ({
          product: null,
          metafieldDefinitions: { nodes: [] },
        }) as T,
    }),
    /was not found/,
  );
});

test("refuses supplier metafields exposed to the storefront", async () => {
  await assert.rejects(
    markAutonomousSourcedProduct(input(), {
      mock: false,
      graphql: async <T>() =>
        ({
          product: baseProduct,
          metafieldDefinitions: {
            nodes: [
              {
                namespace: "relay",
                key: "supplier_cost",
                access: { storefront: "PUBLIC_READ" },
              },
            ],
          },
        }) as T,
    }),
    /storefront-readable supplier metadata/,
  );
});

test("resolves only the exact Shopify handle and returns live variant SKUs", async () => {
  const handle = "relay-sourced-smart-watch";
  const result = await resolveShopifyProductByHandle(handle, {
    mock: false,
    graphql: async <T>(_query: string, variables: Record<string, unknown>) => {
      assert.equal(variables.handle, handle);
      return {
        productByHandle: {
          id: "gid://shopify/Product/1234",
          handle,
          title: "Smart Watch",
          variants: {
            nodes: [
              {
                id: "gid://shopify/ProductVariant/5678",
                sku: "WATCH-BLACK",
                title: "Black",
                price: "4.60",
                inventoryQuantity: 12,
              },
            ],
          },
        },
      } as T;
    },
  });

  assert.equal(result.productId, "gid://shopify/Product/1234");
  assert.equal(result.matching, "exact Shopify handle (never title)");
  assert.deepEqual(result.variants, [
    {
      variantId: "gid://shopify/ProductVariant/5678",
      sku: "WATCH-BLACK",
      title: "Black",
      price: "4.60",
      inventoryQuantity: 12,
    },
  ]);
});
