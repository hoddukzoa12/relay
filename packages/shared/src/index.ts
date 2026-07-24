/**
 * @arb/shared — canonical data contracts for the Agentic Resell Broker.
 *
 * These mirror PRD §6 (Payment Request Schema). The JSON Schema files under
 * ./schemas are the language-neutral source of truth; the Python agents carry
 * a hand-mirrored copy in `agents/agentic_broker/common/contracts.py`.
 * If you change a field here, change it in all three places.
 */
import { z } from "zod";

export const NETWORKS = ["solana-devnet", "solana-mainnet"] as const;
export type Network = (typeof NETWORKS)[number];

/** Money is carried as a decimal STRING (never a float) + a currency tag. */
export const MoneySchema = z.object({
  amount: z.string().regex(/^\d+(\.\d{1,6})?$/, "decimal string, ≤6 dp"),
  currency: z.literal("USDC"),
});
export type Money = z.infer<typeof MoneySchema>;

/**
 * Step 3 — shopping agent → buyer agent.
 * The agent-native payment request. NOT a Shopify web-checkout link:
 * the wallet must be able to sign this without a human click.
 */
export const PaymentRequestSchema = z.object({
  productId: z.string(), // Shopify product/variant id
  title: z.string(),
  price: MoneySchema,
  payTo: z.string(), // merchant (shopping agent) receiving wallet pubkey (base58)
  reference: z.string(), // unique per-order pubkey — the on-chain verification key
  orderRef: z.string(), // internal order-correlation id
  network: z.enum(NETWORKS),
  expiresAt: z.string(), // ISO-8601
  /** Optional convenience: a Solana Pay URL encoding the same request. */
  solanaPayUrl: z.string().optional(),
});
export type PaymentRequest = z.infer<typeof PaymentRequestSchema>;

/** A2A — buyer agent → shopping agent (Step 1): the purchase intent. */
export const PurchaseIntentSchema = z.object({
  query: z.string(), // natural-language product ask
  budget: MoneySchema, // ceiling the buyer will pay
  shipTo: z.string(), // destination address (free text for the demo)
});
export type PurchaseIntent = z.infer<typeof PurchaseIntentSchema>;

/** A2A — buyer agent → shopping agent (Step 5/7 handoff): proof of payment. */
export const SettlementRequestSchema = z.object({
  orderRef: z.string(),
  reference: z.string(),
  txSignature: z.string(),
});
export type SettlementRequest = z.infer<typeof SettlementRequestSchema>;

export const PaymentStatus = ["pending", "paid", "expired", "invalid"] as const;
export type PaymentStatusT = (typeof PaymentStatus)[number];

/**
 * Step 11 — shopping agent → buyer agent: the final order confirmation.
 * (Also the shape returned by the buyer's /buy endpoint to the demo UI.)
 */
export const OrderConfirmationSchema = z.object({
  orderRef: z.string(),
  status: z.enum(PaymentStatus),
  txSignature: z.string().nullable(),
  explorer: z.string().nullable(),
  shopifyOrderId: z.string().nullable(),
});
export type OrderConfirmation = z.infer<typeof OrderConfirmationSchema>;

/** Result of the payments service verifying a reference on-chain (Step 7/8). */
export const VerificationResultSchema = z.object({
  status: z.enum(PaymentStatus),
  txSignature: z.string().nullable(),
  explorer: z.string().nullable(),
  amount: z.string().nullable(),
});
export type VerificationResult = z.infer<typeof VerificationResultSchema>;

/** Build a cluster-scoped explorer link for a signature. */
export function explorerTxUrl(signature: string, cluster = "devnet"): string {
  return `https://explorer.solana.com/tx/${signature}?cluster=${cluster}`;
}
