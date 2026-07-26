import assert from "node:assert/strict";
import test from "node:test";
import { store, type StoredRequest } from "./store.js";

test("a payment reference can be acquired and paid only once", () => {
  const reference = `reference-${crypto.randomUUID()}`;
  const request: StoredRequest = {
    reference,
    amount: "1.00",
    recipient: "merchant",
    orderRef: "order",
    productId: "product",
    title: "title",
    createdAt: Date.now(),
    expiresAt: Date.now() + 60_000,
    status: "pending",
    submittedTxSignature: null,
    paidTxSignature: null,
  };
  store.put(request);

  assert.equal(store.beginPayment(reference).state, "started");
  assert.equal(store.beginPayment(reference).state, "paying");

  store.recordSubmitted(reference, "signature");
  store.markPaid(reference, "signature");

  const replay = store.beginPayment(reference);
  assert.equal(replay.state, "paid");
  assert.equal(replay.request.paidTxSignature, "signature");
  assert.throws(
    () => store.markPaid(reference, "different-signature"),
    /already paid by another transaction/,
  );
});
