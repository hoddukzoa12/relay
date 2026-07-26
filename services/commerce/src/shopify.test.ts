import assert from "node:assert/strict";
import test from "node:test";
import { listProducts } from "./catalog.js";
import { createPaidOrder, orderTag, type OrderInput } from "./shopify.js";

const input = (orderRef: string): OrderInput => ({
  orderRef,
  productId: "product",
  title: "Idempotency test",
  amount: "1.00",
  buyerAddress: "buyer",
  shipTo: "destination",
  txSignature: "signature",
  explorer: "https://explorer.test/signature",
});

test("mock order creation returns one result for sequential replays", async () => {
  const order = input(`order-${crypto.randomUUID()}`);
  const first = await createPaidOrder(order);
  const second = await createPaidOrder(order);

  assert.deepEqual(second, first);
});

test("mock order creation coalesces concurrent replays", async () => {
  const order = input(`order-${crypto.randomUUID()}`);
  const [first, second] = await Promise.all([
    createPaidOrder(order),
    createPaidOrder(order),
  ]);

  assert.deepEqual(second, first);
});

test("mock catalog is deterministic and query-ranked", async () => {
  const products = await listProducts("wireless earbuds", 3);

  assert.equal(products.length, 3);
  assert.match(products[0]?.title ?? "", /Earbuds/);
  assert.ok(products.every((product) => product.inventoryQuantity > 0));
  assert.ok(products.every((product) => product.sku.startsWith("RELAY-")));
});

test("Shopify idempotency tags stay within the platform limit", () => {
  const tag = orderTag(`ord_${"a".repeat(32)}`);

  assert.equal(tag, `relay_${"a".repeat(32)}`);
  assert.ok(tag.length <= 40);
});
