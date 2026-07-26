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

/** Commerce-service catalog variant used for grounded sourcing. */
export const CatalogProductSchema = z.object({
  productId: z.string(),
  variantId: z.string(),
  sku: z.string(),
  title: z.string(),
  description: z.string(),
  price: z.string().regex(/^\d+(\.\d{1,6})?$/, "decimal string, ≤6 dp"),
  inventoryQuantity: z.number().int(),
  status: z.literal("ACTIVE"),
  tags: z.array(z.string()),
});
export type CatalogProduct = z.infer<typeof CatalogProductSchema>;

export const CatalogProductsResponseSchema = z.object({
  products: z.array(CatalogProductSchema),
});
export type CatalogProductsResponse = z.infer<
  typeof CatalogProductsResponseSchema
>;

// ---------------------------------------------------------------------------
// AP2 mandates — carried as A2A DataParts alongside the existing REST contracts.
// Field names follow google-agentic-commerce/AP2's v0.1 mandate types. Relay's
// price/shipping/binding fields make the autonomous Solana Pay authorization
// explicit without changing PaymentRequest or SettlementRequest.
// ---------------------------------------------------------------------------
export const AP2_MANDATE_DATA_KEYS = {
  intent: "ap2.mandates.IntentMandate",
  cart: "ap2.mandates.CartMandate",
  payment: "ap2.mandates.PaymentMandate",
} as const;

export const IntentMandateSchema = z.object({
  user_cart_confirmation_required: z.boolean(),
  natural_language_description: z.string(),
  merchants: z.array(z.string()).nullish(),
  skus: z.array(z.string()).nullish(),
  requires_refundability: z.boolean().nullish(),
  price_ceiling: MoneySchema,
  ship_to: z.string(),
  intent_expiry: z.string(),
  signature: z.string().min(1),
});
export type IntentMandate = z.infer<typeof IntentMandateSchema>;

export const CartItemSchema = z.object({
  sku: z.string(),
  variant_id: z.string().optional(),
  name: z.string(),
  price: MoneySchema,
});
export type CartItem = z.infer<typeof CartItemSchema>;

export const CartContentsSchema = z.object({
  id: z.string(),
  user_cart_confirmation_required: z.boolean(),
  cart_items: z.array(CartItemSchema).min(1),
  total: MoneySchema,
  shipping: MoneySchema,
  tax: MoneySchema,
  refund_period: z.number().int().nonnegative(),
  cart_expiry: z.string(),
  merchant_name: z.string(),
  payment_request: PaymentRequestSchema,
  intent_mandate_signature: z.string().min(1),
});
export type CartContents = z.infer<typeof CartContentsSchema>;

export const CartMandateSchema = z.object({
  contents: CartContentsSchema,
  signature: z.string().min(1),
});
export type CartMandate = z.infer<typeof CartMandateSchema>;

export const PayerInfoSchema = z.object({
  wallet_address: z.string(),
  ship_to: z.string().optional(),
});
export type PayerInfo = z.infer<typeof PayerInfoSchema>;

export const PaymentMandateContentsSchema = z.object({
  payment_mandate_id: z.string(),
  payment_details_id: z.string(),
  payment_method_token: z.string(),
  amount: MoneySchema,
  merchant_name: z.string(),
  payer: PayerInfoSchema,
  timestamp: z.string(),
  cart_mandate_signature: z.string().min(1),
  intent_mandate_signature: z.string().min(1),
});
export type PaymentMandateContents = z.infer<typeof PaymentMandateContentsSchema>;

export const PaymentMandateSchema = z.object({
  payment_mandate_contents: PaymentMandateContentsSchema,
  signature: z.string().min(1),
});
export type PaymentMandate = z.infer<typeof PaymentMandateSchema>;

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

export const VerificationFailureReason = [
  "unknown_reference",
  "transaction_not_found",
  "transaction_not_confirmed",
  "transaction_failed",
  "amount_mismatch",
  "recipient_mismatch",
  "reference_mismatch",
  "transfer_mismatch",
  "malformed_transaction",
  "validation_failed",
] as const;
export type VerificationFailureReasonT = (typeof VerificationFailureReason)[number];

/** Result of the payments service verifying a reference on-chain (Step 7/8). */
export const VerificationResultSchema = z.object({
  status: z.enum(PaymentStatus),
  txSignature: z.string().nullable(),
  explorer: z.string().nullable(),
  amount: z.string().nullable(),
  reason: z.enum(VerificationFailureReason).nullable(),
});
export type VerificationResult = z.infer<typeof VerificationResultSchema>;

/** Build a cluster-scoped explorer link for a signature. */
export function explorerTxUrl(signature: string, cluster = "devnet"): string {
  return `https://explorer.solana.com/tx/${signature}?cluster=${cluster}`;
}
