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
}

const requests = new Map<string, StoredRequest>();

export const store = {
  put(r: StoredRequest): void {
    requests.set(r.reference, r);
  },
  get(reference: string): StoredRequest | undefined {
    return requests.get(reference);
  },
  all(): StoredRequest[] {
    return [...requests.values()];
  },
};
