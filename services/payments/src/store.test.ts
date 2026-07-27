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
    refundReference: `refund-${crypto.randomUUID()}`,
    refundStatus: "not_refunded",
    refundSubmittedTxSignature: null,
    refundTxSignature: null,
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

test("a paid order refund can be acquired and completed only once", () => {
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
    status: "paid",
    submittedTxSignature: "payment-signature",
    paidTxSignature: "payment-signature",
    refundReference: `refund-${crypto.randomUUID()}`,
    refundStatus: "not_refunded",
    refundSubmittedTxSignature: null,
    refundTxSignature: null,
  };
  store.put(request);

  assert.equal(store.beginRefund(reference).state, "started");
  assert.equal(store.beginRefund(reference).state, "refunding");

  store.recordRefundSubmitted(reference, "refund-signature");
  store.markRefunded(reference, "refund-signature");

  const replay = store.beginRefund(reference);
  assert.equal(replay.state, "refunded");
  assert.equal(replay.request.refundTxSignature, "refund-signature");
  assert.throws(
    () => store.markRefunded(reference, "different-signature"),
    /already refunded by another transaction/,
  );
});

test("an unpaid order cannot enter the refund state machine", () => {
  const reference = `reference-${crypto.randomUUID()}`;
  store.put({
    reference,
    amount: "1.00",
    recipient: "merchant",
    orderRef: "unpaid-order",
    productId: "product",
    title: "title",
    createdAt: Date.now(),
    expiresAt: Date.now() + 60_000,
    status: "pending",
    submittedTxSignature: null,
    paidTxSignature: null,
    refundReference: `refund-${crypto.randomUUID()}`,
    refundStatus: "not_refunded",
    refundSubmittedTxSignature: null,
    refundTxSignature: null,
  });

  assert.throws(
    () => store.beginRefund(reference),
    /has no verified paid transaction/,
  );
});
