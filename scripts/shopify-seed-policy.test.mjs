import assert from "node:assert/strict";
import test from "node:test";
import {
  activeSupplierProducts,
  relaySeedProductsBySku,
} from "./shopify-seed-policy.mjs";

const product = (vendor, status, sku, title = "Same title") => ({
  id: `product:${vendor}`,
  title,
  vendor,
  status,
  variants: { nodes: [{ id: `variant:${vendor}`, sku }] },
});

test("an active supplier catalog disables fallback seeding", () => {
  const products = [
    product("Relay", "DRAFT", "RELAY-DEMO"),
    product("SolanaGCP", "ACTIVE", "14:193#black"),
  ];

  assert.deepEqual(
    activeSupplierProducts(products).map(({ vendor }) => vendor),
    ["SolanaGCP"],
  );
});

test("seed matching cannot capture another vendor by title or SKU", () => {
  const supplier = product("SolanaGCP", "DRAFT", "COLLIDING-SKU");
  const relay = product("Relay", "DRAFT", "RELAY-DEMO");
  const bySku = relaySeedProductsBySku([supplier, relay]);

  assert.equal(bySku.has("COLLIDING-SKU"), false);
  assert.equal(bySku.get("RELAY-DEMO")?.product.vendor, "Relay");
});
