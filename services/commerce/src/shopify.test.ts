import assert from "node:assert/strict";
import test from "node:test";
import { createPaidOrder, type OrderInput } from "./shopify.js";

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
