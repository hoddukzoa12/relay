import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import {
  supplierMetafields,
  validateLiveSupplierProducts,
  validateSupplierCostSnapshot,
} from "./dsers-supplier-cost-policy.mjs";

const snapshot = JSON.parse(
  readFileSync(new URL("./dsers-supplier-costs.json", import.meta.url), "utf8"),
);

test("supplier snapshot is exact and keyed by unique Shopify variant ids", () => {
  validateSupplierCostSnapshot(snapshot);
  const variantIds = snapshot.products.flatMap((product) =>
    product.variants.map((variant) => variant.shopifyVariantId),
  );
  assert.equal(new Set(variantIds).size, variantIds.length);
  assert.equal(variantIds.length, 18);
});

test("duplicate SKUs across products remain separate variant-owned costs", () => {
  const black = snapshot.products.flatMap((product) =>
    product.variants
      .filter((variant) => variant.sku === "14:193#black")
      .map((variant) => [variant.shopifyVariantId, variant.cost]),
  );
  assert.deepEqual(black, [
    ["gid://shopify/ProductVariant/59696201072926", "3.96"],
    ["gid://shopify/ProductVariant/59696201564446", "3.64"],
    ["gid://shopify/ProductVariant/59696199074078", "3.88"],
  ]);
});

test("live validation rejects a vendor mismatch without relying on titles", () => {
  const first = snapshot.products[0];
  const live = [
    {
      id: first.shopifyProductId,
      title: "Any mutable title",
      vendor: "WrongVendor",
      status: "ACTIVE",
      variants: {
        nodes: first.variants.map((variant) => ({
          id: variant.shopifyVariantId,
          sku: variant.sku,
        })),
      },
    },
  ];
  assert.throws(
    () =>
      validateLiveSupplierProducts(
        { ...snapshot, products: [first] },
        live,
      ),
    /must be ACTIVE vendor SolanaGCP/,
  );
});

test("metafield inputs store cost on the exact variant and stay namespaced", () => {
  const inputs = supplierMetafields(snapshot);
  assert.equal(inputs.length, 18 * 8);
  const f95 = inputs.filter(
    (input) =>
      input.ownerId === "gid://shopify/ProductVariant/59696201072926",
  );
  assert.equal(f95.length, 8);
  assert.equal(
    f95.find((input) => input.key === "supplier_cost")?.value,
    "3.96",
  );
  assert.ok(f95.every((input) => input.namespace === "relay"));
});
