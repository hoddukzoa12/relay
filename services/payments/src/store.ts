/**
 * In-memory store of issued payment requests, keyed by `reference` pubkey.
 * The verify step looks up the expected {amount, recipient} here so it can
 * validate the on-chain transfer WITHOUT trusting the client (PRD §3).
 *
 * NOTE: process-local only. A restart forgets pending requests. For production
 * this belongs in Firestore/Redis — fine for the demo, called out in the README.
 */
export interface StoredRequest {
  reference: string;
  amount: string; // decimal USDC string
  recipient: string; // merchant wallet pubkey (base58)
  orderRef: string;
  productId: string;
  title: string;
  createdAt: number;
  expiresAt: number; // epoch ms
  status: "pending" | "paying" | "paid";
  submittedTxSignature: string | null;
  paidTxSignature: string | null;
  refundReference: string;
  refundStatus: "not_refunded" | "refunding" | "refunded";
  refundSubmittedTxSignature: string | null;
  refundTxSignature: string | null;
}

const requests = new Map<string, StoredRequest>();

export const store = {
  put(r: StoredRequest): void {
    requests.set(r.reference, r);
  },
  get(reference: string): StoredRequest | undefined {
    return requests.get(reference);
  },
  /**
   * Synchronous Map mutation is the compare-and-set boundary for this
   * process. Only one caller can move a reference from pending to paying.
   */
  beginPayment(reference: string):
    | { state: "started"; request: StoredRequest }
    | { state: "paying"; request: StoredRequest }
    | { state: "paid"; request: StoredRequest } {
    const request = requests.get(reference);
    if (!request) {
      throw new Error("Unknown payment reference");
    }
    if (request.status === "pending") {
      request.status = "paying";
      return { state: "started", request };
    }
    return { state: request.status, request };
  },
  recordSubmitted(reference: string, signature: string): void {
    const request = requests.get(reference);
    if (!request || request.status !== "paying") {
      throw new Error(`Cannot record submitted transaction for ${reference}`);
    }
    if (
      request.submittedTxSignature !== null &&
      request.submittedTxSignature !== signature
    ) {
      throw new Error(`Payment reference ${reference} already has a submitted transaction`);
    }
    request.submittedTxSignature = signature;
  },
  markPaid(reference: string, signature: string): void {
    const request = requests.get(reference);
    if (!request) {
      throw new Error("Unknown payment reference");
    }
    if (request.paidTxSignature !== null && request.paidTxSignature !== signature) {
      throw new Error(`Payment reference ${reference} is already paid by another transaction`);
    }
    request.status = "paid";
    request.submittedTxSignature ??= signature;
    request.paidTxSignature = signature;
  },
  resetUnsubmitted(reference: string): void {
    const request = requests.get(reference);
    if (
      request?.status === "paying" &&
      request.submittedTxSignature === null
    ) {
      request.status = "pending";
    }
  },
  /**
   * A second process-local compare-and-set boundary protects the reverse money
   * movement. A paid reference can enter refunding exactly once.
   */
  beginRefund(reference: string):
    | { state: "started"; request: StoredRequest }
    | { state: "refunding"; request: StoredRequest }
    | { state: "refunded"; request: StoredRequest } {
    const request = requests.get(reference);
    if (!request) {
      throw new Error("Unknown payment reference");
    }
    if (request.status !== "paid" || request.paidTxSignature === null) {
      throw new Error(`Order ${request.orderRef} has no verified paid transaction`);
    }
    if (request.refundStatus === "not_refunded") {
      request.refundStatus = "refunding";
      return { state: "started", request };
    }
    return { state: request.refundStatus, request };
  },
  recordRefundSubmitted(reference: string, signature: string): void {
    const request = requests.get(reference);
    if (!request || request.refundStatus !== "refunding") {
      throw new Error(`Cannot record submitted refund for ${reference}`);
    }
    if (
      request.refundSubmittedTxSignature !== null &&
      request.refundSubmittedTxSignature !== signature
    ) {
      throw new Error(`Payment reference ${reference} already has a submitted refund`);
    }
    request.refundSubmittedTxSignature = signature;
  },
  markRefunded(reference: string, signature: string): void {
    const request = requests.get(reference);
    if (!request) {
      throw new Error("Unknown payment reference");
    }
    if (
      request.refundTxSignature !== null &&
      request.refundTxSignature !== signature
    ) {
      throw new Error(`Order ${request.orderRef} is already refunded by another transaction`);
    }
    request.refundStatus = "refunded";
    request.refundSubmittedTxSignature ??= signature;
    request.refundTxSignature = signature;
  },
  resetUnsubmittedRefund(reference: string): void {
    const request = requests.get(reference);
    if (
      request?.refundStatus === "refunding" &&
      request.refundSubmittedTxSignature === null
    ) {
      request.refundStatus = "not_refunded";
    }
  },
  all(): StoredRequest[] {
    return [...requests.values()];
  },
};
