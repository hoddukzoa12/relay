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

/** Wallet-owned order projection used by the authenticated "my orders" view. */
export const WalletOrderSchema = z.object({
  shopifyOrderId: z.string(),
  name: z.string(),
  status: z.string(),
  createdAt: z.string(),
  orderRef: z.string(),
  title: z.string(),
  amount: z.string(),
  buyerWallet: z.string(),
  txSignature: z.string(),
  explorer: z.string(),
});
export type WalletOrder = z.infer<typeof WalletOrderSchema>;

export const WalletOrdersResponseSchema = z.object({
  orders: z.array(WalletOrderSchema),
});
export type WalletOrdersResponse = z.infer<typeof WalletOrdersResponseSchema>;

// ---------------------------------------------------------------------------
// Post-purchase order lifecycle — lookup, fulfillment, tracking, and refund.
// Keep these schemas mirrored in common/contracts.py and packages/shared/schemas.
// ---------------------------------------------------------------------------
export const OrderLineItemSchema = z.object({
  title: z.string(),
  sku: z.string(),
  quantity: z.number().int().positive(),
});
export type OrderLineItem = z.infer<typeof OrderLineItemSchema>;

export const OnChainProofSchema = z.object({
  txSignature: z.string().min(1),
  explorer: z.string().url(),
});
export type OnChainProof = z.infer<typeof OnChainProofSchema>;

export const PaymentProofSchema = OnChainProofSchema.extend({
  reference: z.string().min(1).nullable(),
});
export type PaymentProof = z.infer<typeof PaymentProofSchema>;

export const RefundStateSchema = z.object({
  status: z.enum(["not_refunded", "refunded"]),
  reference: z.string().min(1).nullable(),
  txSignature: z.string().min(1).nullable(),
  explorer: z.string().url().nullable(),
});
export type RefundState = z.infer<typeof RefundStateSchema>;

export const TrackingInfoSchema = z.object({
  provider: z.literal("easypost"),
  carrier: z.string().min(1),
  trackingNumber: z.string().min(1),
  status: z.string().min(1),
  statusDetail: z.string().nullable(),
  trackingUrl: z.string().url().nullable(),
  estimatedDeliveryAt: z.string().nullable(),
  demo: z.boolean(),
  message: z.string().min(1),
});
export type TrackingInfo = z.infer<typeof TrackingInfoSchema>;

export const OrderStatusSchema = z.object({
  shopifyOrderId: z.string(),
  orderRef: z.string(),
  name: z.string(),
  financialStatus: z.string(),
  fulfillmentStatus: z.string(),
  lineItems: z.array(OrderLineItemSchema).min(1),
  amount: MoneySchema,
  payment: PaymentProofSchema,
  refund: RefundStateSchema,
  tracking: TrackingInfoSchema.nullable(),
});
export type OrderStatus = z.infer<typeof OrderStatusSchema>;

export const RefundResultSchema = z.object({
  shopifyOrderId: z.string(),
  orderRef: z.string(),
  name: z.string(),
  status: z.literal("refunded"),
  financialStatus: z.literal("REFUNDED"),
  amount: MoneySchema,
  payment: PaymentProofSchema,
  refund: PaymentProofSchema,
  replayed: z.boolean(),
});
export type RefundResult = z.infer<typeof RefundResultSchema>;

export const FulfillmentResultSchema = z.object({
  shopifyOrderId: z.string(),
  orderRef: z.string(),
  name: z.string(),
  fulfillmentStatus: z.literal("FULFILLED"),
  tracking: TrackingInfoSchema,
  replayed: z.boolean(),
});
export type FulfillmentResult = z.infer<typeof FulfillmentResultSchema>;

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
  // Human identity wallet for browser-delegated intents. Omitted by the
  // retained agent-only path, whose configured buyer wallet signs the intent.
  signer_wallet: z.string().optional(),
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
 * (Also the shape returned by the buyer's /buy endpoint to the Shopify widget.)
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
