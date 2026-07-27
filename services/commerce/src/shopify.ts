import { createHash } from "node:crypto";
import type {
  FulfillmentResult,
  OrderStatus,
  StructuredShippingAddress,
  SupplierCostSnapshot,
  SupplierOrder,
  TrackingInfo,
  WalletOrder,
} from "@arb/shared";
import { config } from "./config.js";
import { ShopifyAdminClient } from "./shopify-client.js";
import {
  ShopifyStoreCurrency,
  requireUsdcParityCurrency,
  type StoreCurrencyOptions,
} from "./shopify-currency.js";
export interface OrderInput {
  orderRef: string;
  productId: string; // retained for compatibility; contains the variant id
  variantId?: string;
  sku?: string;
  title: string;
  amount: string; // USDC decimal string paid on-chain
  buyerAddress: string; // buyer identity wallet (agent wallet on the legacy path)
  shipTo: string;
  paymentReference?: string;
  txSignature: string;
  explorer: string;
  supplierCost?: SupplierCostSnapshot | null;
  shippingAddress?: StructuredShippingAddress | null;
}

export interface OrderResult {
  shopifyOrderId: string;
  name: string;
  mocked: boolean;
  supplierOrder: SupplierOrder;
}

export class OrderNotFoundError extends Error {}
export class OrderLifecycleConflictError extends Error {}

const shopifyAdmin = new ShopifyAdminClient(config.shopify);

export async function shopifyGraphQL<T>(
  query: string,
  variables: Record<string, unknown>,
): Promise<T> {
  return shopifyAdmin.graphql<T>(query, variables);
}

const shopifyStoreCurrency = new ShopifyStoreCurrency(shopifyGraphQL);

export const SUPPLIER_FULFILLMENT_DISABLED_MESSAGE =
  "Supplier fulfillment is disabled; no structured Shopify shipping address or supplier order was created.";
export const SUPPLIER_FULFILLMENT_BLOCKED_MESSAGE =
  "Supplier fulfillment is enabled but the buyer did not provide a complete structured shipping address; no supplier order was created.";
export const SUPPLIER_FULFILLMENT_PENDING_MESSAGE =
  "A complete structured shipping address was submitted to Shopify for DSers auto-fulfillment; the supplier order is not yet confirmed and no supplier reference or tracking number is available.";

export function disabledSupplierOrder(): SupplierOrder {
  return {
    provider: "dsers",
    status: "disabled",
    ref: null,
    message: SUPPLIER_FULFILLMENT_DISABLED_MESSAGE,
  };
}

const PLACEHOLDER_ADDRESS_VALUE =
  /^(?:n\/?a|none|unknown|test|testing|placeholder|todo|tbd|-+)$/i;

function cleanRequiredAddressValue(value: unknown): string | null {
  if (typeof value !== "string") return null;
  const cleaned = value.trim();
  if (!cleaned || PLACEHOLDER_ADDRESS_VALUE.test(cleaned)) return null;
  return cleaned;
}

/** Revalidate at the irreversible Shopify boundary; never synthesize fields. */
export function completeShippingAddress(
  address: StructuredShippingAddress | null | undefined,
): StructuredShippingAddress | null {
  if (!address) return null;
  const name = cleanRequiredAddressValue(address.name);
  const address1 = cleanRequiredAddressValue(address.address1);
  const city = cleanRequiredAddressValue(address.city);
  const province = cleanRequiredAddressValue(address.province);
  const country = cleanRequiredAddressValue(address.country)?.toUpperCase();
  const zip = cleanRequiredAddressValue(address.zip);
  const address2 = address.address2?.trim() || null;
  const phone = address.phone?.trim() || null;
  if (
    !name ||
    !address1 ||
    !city ||
    !province ||
    !country ||
    !/^[A-Z]{2}$/.test(country) ||
    !zip ||
    zip.length < 3 ||
    (address2 && PLACEHOLDER_ADDRESS_VALUE.test(address2)) ||
    (phone &&
      (phone.length < 5 || PLACEHOLDER_ADDRESS_VALUE.test(phone)))
  ) {
    return null;
  }
  return {
    name,
    address1,
    address2,
    city,
    province,
    country,
    zip,
    phone,
  };
}

export function shopifyShippingAddress(
  address: StructuredShippingAddress | null | undefined,
): Record<string, string> | null {
  const complete = completeShippingAddress(address);
  if (!complete) return null;
  const nameParts = complete.name.split(/\s+/);
  const firstName =
    nameParts.length > 1
      ? nameParts.slice(0, -1).join(" ")
      : complete.name;
  const lastName =
    nameParts.length > 1 ? nameParts[nameParts.length - 1]! : "";
  return {
    firstName,
    ...(lastName ? { lastName } : {}),
    address1: complete.address1,
    ...(complete.address2 ? { address2: complete.address2 } : {}),
    city: complete.city,
    provinceCode: complete.province,
    countryCode: complete.country,
    zip: complete.zip,
    ...(complete.phone ? { phone: complete.phone } : {}),
  };
}

export function supplierOrderForInput(
  input: Pick<OrderInput, "shippingAddress">,
  enabled = config.supplierFulfillmentEnabled,
): SupplierOrder {
  if (!enabled) return disabledSupplierOrder();
  if (!completeShippingAddress(input.shippingAddress)) {
    return {
      provider: "dsers",
      status: "blocked",
      ref: null,
      message: SUPPLIER_FULFILLMENT_BLOCKED_MESSAGE,
    };
  }
  return {
    provider: "dsers",
    status: "pending",
    ref: null,
    message: SUPPLIER_FULFILLMENT_PENDING_MESSAGE,
  };
}

interface MarginEvidence {
  supplierCostAmount: string;
  supplierCostCurrency: "USD";
  saleAmount: string;
  saleCurrency: "USDC";
  projectedGrossMarginAmount: string;
  projectedGrossMarginPct: string;
  basis: "snapshot_usd_usdc_parity_excludes_shipping_tax";
}

function decimalMicros(value: string): bigint {
  const [whole, fraction = ""] = value.split(".");
  if (!whole || !/^\d+$/.test(whole) || !/^\d*$/.test(fraction)) {
    throw new Error(`Invalid decimal amount ${value}`);
  }
  return BigInt(whole) * 1_000_000n + BigInt((fraction + "000000").slice(0, 6));
}

function formatMicros(value: bigint): string {
  const whole = value / 1_000_000n;
  const fraction = (value % 1_000_000n)
    .toString()
    .padStart(6, "0")
    .replace(/0+$/, "");
  return fraction ? `${whole}.${fraction}` : `${whole}`;
}

export function marginEvidence(
  saleAmount: string,
  supplierCost: SupplierCostSnapshot | null | undefined,
): MarginEvidence | null {
  if (!supplierCost || supplierCost.currency !== "USD") return null;
  const sale = decimalMicros(saleAmount);
  const cost = decimalMicros(supplierCost.amount);
  if (sale <= 0n || cost <= 0n || sale < cost) return null;
  const margin = sale - cost;
  const marginPctHundredths = (margin * 1_000_000n) / sale;
  const marginPct = Number(marginPctHundredths) / 10_000;
  return {
    supplierCostAmount: supplierCost.amount,
    supplierCostCurrency: "USD",
    saleAmount,
    saleCurrency: "USDC",
    projectedGrossMarginAmount: formatMicros(margin),
    projectedGrossMarginPct: marginPct.toFixed(2),
    basis: "snapshot_usd_usdc_parity_excludes_shipping_tax",
  };
}

export async function requireShopifyUsdcParityCurrency(
  options: StoreCurrencyOptions = {},
): Promise<string> {
  return shopifyStoreCurrency.requireUsdcParity(options);
}

const ORDER_CREATE = /* GraphQL */ `
  mutation OrderCreate($order: OrderCreateOrderInput!) {
    orderCreate(order: $order) {
      order { id name displayFinancialStatus }
      userErrors { field message }
    }
  }
`;

const ORDER_MARK_AS_PAID = /* GraphQL */ `
  mutation OrderMarkAsPaid($input: OrderMarkAsPaidInput!) {
    orderMarkAsPaid(input: $input) {
      order { id displayFinancialStatus }
      userErrors { field message }
    }
  }
`;

const ORDER_FIELDS = /* GraphQL */ `
  id
  name
  createdAt
  displayFinancialStatus
  displayFulfillmentStatus
  customAttributes { key value }
  totalPriceSet { shopMoney { amount currencyCode } }
  lineItems(first: 50) {
    nodes { id title sku quantity }
  }
  fulfillments(first: 10) {
    id
    status
    trackingInfo(first: 10) { company number url }
  }
  transactions(first: 50) {
    id
    kind
    status
    gateway
    amountSet { shopMoney { amount currencyCode } }
  }
`;

const FIND_ORDER = /* GraphQL */ `
  query FindOrder($query: String!, $after: String) {
    orders(first: 50, after: $after, query: $query, sortKey: CREATED_AT, reverse: true) {
      nodes { ${ORDER_FIELDS} }
      pageInfo { hasNextPage endCursor }
    }
  }
`;

const LIST_RELAY_ORDERS = /* GraphQL */ `
  query ListRelayOrders($query: String!, $after: String) {
    orders(first: 50, after: $after, query: $query, sortKey: CREATED_AT, reverse: true) {
      nodes {
        id
        name
        createdAt
        displayFinancialStatus
        customAttributes { key value }
        lineItems(first: 1) { nodes { title } }
      }
      pageInfo { hasNextPage endCursor }
    }
  }
`;

const ORDER_UPDATE_ATTRIBUTES = /* GraphQL */ `
  mutation OrderUpdateAttributes($input: OrderInput!) {
    orderUpdate(input: $input) {
      order { id displayFinancialStatus customAttributes { key value } }
      userErrors { field message }
    }
  }
`;

function refundCreateMutation(useIdempotencyDirective: boolean): string {
  const keyVariable = useIdempotencyDirective
    ? ", $idempotencyKey: String!"
    : "";
  const directive = useIdempotencyDirective
    ? " @idempotent(key: $idempotencyKey)"
    : "";
  return /* GraphQL */ `
    mutation RefundCreate($input: RefundInput!${keyVariable}) {
      refundCreate(input: $input)${directive} {
        refund { id }
        order { id displayFinancialStatus }
        userErrors { field message }
      }
    }
  `;
}

interface ShopifyMoney {
  amount: string;
  currencyCode: string;
}

interface ShopifyLineItem {
  id: string;
  title: string;
  sku: string | null;
  quantity: number;
}

interface ShopifyFulfillment {
  id: string;
  status: string;
  trackingInfo: {
    company: string | null;
    number: string | null;
    url: string | null;
  }[];
}

interface ShopifyTransaction {
  id: string;
  kind: string;
  status: string;
  gateway: string | null;
  amountSet: { shopMoney: ShopifyMoney };
}

interface ShopifyOrder {
  id: string;
  name: string;
  displayFinancialStatus: string;
  displayFulfillmentStatus?: string;
  createdAt?: string;
  customAttributes?: { key: string; value: string | null }[];
  totalPriceSet?: { shopMoney: ShopifyMoney };
  lineItems?: { nodes: ShopifyLineItem[] };
  fulfillments?: ShopifyFulfillment[];
  transactions?: ShopifyTransaction[];
}

interface OrderCreateData {
  orderCreate: {
    order: ShopifyOrder | null;
    userErrors: { field: string[] | null; message: string }[];
  };
}

interface OrderMarkAsPaidData {
  orderMarkAsPaid: {
    order: { id: string; displayFinancialStatus: string } | null;
    userErrors: { field: string[] | null; message: string }[];
  };
}

interface FindOrderData {
  orders: {
    nodes: ShopifyOrder[];
    pageInfo: { hasNextPage: boolean; endCursor: string | null };
  };
}

interface OrderUpdateData {
  orderUpdate: {
    order: ShopifyOrder | null;
    userErrors: { field: string[] | null; message: string }[];
  };
}

interface RefundCreateData {
  refundCreate: {
    refund: { id: string } | null;
    order: { id: string; displayFinancialStatus: string } | null;
    userErrors: { field: string[] | null; message: string }[];
  };
}

interface MockOrderRecord {
  input: OrderInput;
  result: OrderResult;
  createdAt: string;
  financialStatus: string;
  fulfillmentStatus: string;
  refundTxSignature: string | null;
  refundReference: string | null;
  refundExplorer: string | null;
}

let mockCounter = 1000;
const MARK_PAID_RETRY_DELAYS_MS = [1_000, 2_000, 4_000, 8_000, 16_000];
const completedOrders = new Map<string, OrderResult>();
const inFlightOrders = new Map<string, Promise<OrderResult>>();
const inFlightRefunds = new Map<string, Promise<OrderStatus>>();
const inFlightFulfillments = new Map<string, Promise<FulfillmentResult>>();
const mockOrders = new Map<string, MockOrderRecord>();

export function orderTag(orderRef: string): string {
  const compactRef = orderRef.startsWith("ord_") ? orderRef.slice(4) : orderRef;
  return `relay_${compactRef}`.slice(0, 40);
}

function customAttribute(order: ShopifyOrder, key: string): string {
  return (
    order.customAttributes?.find((attribute) => attribute.key === key)?.value ??
    ""
  );
}

function mergedAttributes(
  order: ShopifyOrder,
  additions: Record<string, string>,
): { key: string; value: string }[] {
  const merged = new Map(
    (order.customAttributes ?? []).map((attribute) => [
      attribute.key,
      attribute.value ?? "",
    ]),
  );
  for (const [key, value] of Object.entries(additions)) {
    merged.set(key, value);
  }
  return [...merged].map(([key, value]) => ({ key, value }));
}

async function findOrder(
  identifier: string,
  options: { required: boolean },
): Promise<ShopifyOrder | null> {
  const byName = identifier.startsWith("#");
  const query = byName
    ? `name:${JSON.stringify(identifier)}`
    : `tag:${JSON.stringify(orderTag(identifier))}`;
  let after: string | null = null;
  do {
    const data: FindOrderData = await shopifyGraphQL<FindOrderData>(FIND_ORDER, {
      query,
      after,
    });
    const match = data.orders.nodes.find((order) =>
      byName
        ? order.name === identifier
        : customAttribute(order, "order_ref") === identifier,
    );
    if (match) return match;
    after = data.orders.pageInfo.hasNextPage
      ? data.orders.pageInfo.endCursor
      : null;
  } while (after);

  if (options.required) {
    throw new OrderNotFoundError(`Order ${identifier} was not found`);
  }
  return null;
}

async function findOrderByRef(orderRef: string): Promise<ShopifyOrder | null> {
  return findOrder(orderRef, { required: false });
}

async function createOrder(input: OrderInput): Promise<ShopifyOrder> {
  // Re-read immediately before the irreversible order write. The catalog path
  // also validates currency before a quote, but this closes the window if an
  // administrator changes the store currency while a quote is in flight.
  const storeCurrency = await requireShopifyUsdcParityCurrency({
    forceRefresh: true,
  });
  const existing = await findOrderByRef(input.orderRef);
  if (existing) {
    console.log(
      `[commerce] reusing Shopify order ${existing.id} for ${input.orderRef}`,
    );
    return existing;
  }

  const created = await shopifyGraphQL<OrderCreateData>(ORDER_CREATE, {
    order: buildShopifyOrderInput(input, storeCurrency),
  });

  const errors = created.orderCreate.userErrors;
  if (errors.length || !created.orderCreate.order) {
    throw new Error(`orderCreate failed: ${JSON.stringify(errors)}`);
  }
  return created.orderCreate.order;
}

export function buildShopifyOrderInput(
  input: OrderInput,
  storeCurrency: string,
  supplierFulfillmentEnabled = config.supplierFulfillmentEnabled,
): Record<string, unknown> {
  const currency = requireUsdcParityCurrency(storeCurrency);
  const variantId = input.variantId ?? input.productId;
  const margin = marginEvidence(input.amount, input.supplierCost);
  const supplierOrder = supplierOrderForInput(
    input,
    supplierFulfillmentEnabled,
  );
  const completeAddress = completeShippingAddress(input.shippingAddress);
  const shippingAddress = supplierFulfillmentEnabled
    ? shopifyShippingAddress(completeAddress)
    : null;
  const shipTo = completeAddress
    ? [
        completeAddress.name,
        completeAddress.address1,
        completeAddress.address2,
        completeAddress.city,
        completeAddress.province,
        completeAddress.zip,
        completeAddress.country,
      ]
        .filter(Boolean)
        .join(", ")
    : input.shipTo;
  return {
    currency,
    ...(shippingAddress ? { shippingAddress } : {}),
    lineItems: [
      {
        variantId,
        quantity: 1,
        priceSet: {
          shopMoney: {
            amount: input.amount,
            currencyCode: currency,
          },
        },
      },
    ],
    note: `Autonomous agent order. Paid on-chain: ${input.explorer}`,
    tags: ["relay", orderTag(input.orderRef)],
    customAttributes: [
      { key: "order_ref", value: input.orderRef },
      { key: "usdc_amount", value: input.amount },
      { key: "payment_reference", value: input.paymentReference ?? "" },
      { key: "tx_signature", value: input.txSignature },
      { key: "network", value: `solana-${config.cluster}` },
      { key: "buyer_wallet", value: input.buyerAddress },
      { key: "ship_to", value: shipTo },
      { key: "variant_id", value: variantId },
      { key: "sku", value: input.sku ?? "" },
      { key: "refund_status", value: "not_refunded" },
      { key: "supplier_order_provider", value: supplierOrder.provider },
      { key: "supplier_order_status", value: supplierOrder.status },
      { key: "supplier_order_ref", value: "" },
      { key: "supplier_order_message", value: supplierOrder.message },
      {
        key: "supplier_fulfillment_gate",
        value: supplierFulfillmentEnabled ? "enabled" : "disabled",
      },
      {
        key: "supplier_cost_status",
        value: input.supplierCost ? "snapshot" : "unavailable",
      },
      {
        key: "supplier_cost_amount",
        value: input.supplierCost?.amount ?? "",
      },
      {
        key: "supplier_cost_currency",
        value: input.supplierCost?.currency ?? "",
      },
      {
        key: "supplier_cost_source",
        value: input.supplierCost?.source ?? "",
      },
      {
        key: "supplier_cost_captured_at",
        value: input.supplierCost?.capturedAt ?? "",
      },
      {
        key: "supplier_cost_ship_to",
        value: input.supplierCost?.shipTo ?? "",
      },
      {
        key: "supplier_url",
        value: input.supplierCost?.supplierUrl ?? "",
      },
      {
        key: "margin_status",
        value: margin ? "projected_snapshot" : "unavailable",
      },
      {
        key: "projected_gross_margin_amount",
        value: margin?.projectedGrossMarginAmount ?? "",
      },
      {
        key: "projected_gross_margin_pct",
        value: margin?.projectedGrossMarginPct ?? "",
      },
      {
        key: "margin_basis",
        value: margin?.basis ?? "",
      },
    ],
  };
}

async function markOrderPaid(order: ShopifyOrder): Promise<void> {
  if (order.displayFinancialStatus === "PAID") return;

  for (let attempt = 0; ; attempt += 1) {
    const paid = await shopifyGraphQL<OrderMarkAsPaidData>(ORDER_MARK_AS_PAID, {
      input: { id: order.id },
    });
    const errors = paid.orderMarkAsPaid.userErrors;
    if (errors.length === 0) {
      if (
        !paid.orderMarkAsPaid.order ||
        paid.orderMarkAsPaid.order.displayFinancialStatus !== "PAID"
      ) {
        throw new Error(
          `orderMarkAsPaid returned unexpected status: ${
            paid.orderMarkAsPaid.order?.displayFinancialStatus ?? "missing order"
          }`,
        );
      }
      return;
    }

    const delayMs = MARK_PAID_RETRY_DELAYS_MS[attempt];
    if (!isTemporaryOrderMutationError(errors) || delayMs === undefined) {
      throw new Error(`orderMarkAsPaid failed: ${JSON.stringify(errors)}`);
    }
    console.warn(
      `[commerce] Shopify order ${order.id} is temporarily unavailable; ` +
        `retrying mark-paid in ${delayMs}ms`,
    );
    await new Promise((resolve) => setTimeout(resolve, delayMs));
  }
}

export function isTemporaryOrderMutationError(
  errors: { field: string[] | null; message: string }[],
): boolean {
  return errors.some(
    (error) =>
      error.field?.includes("id") === true &&
      error.message.toLowerCase().includes("temporarily unavailable"),
  );
}

async function createPaidOrderOnce(input: OrderInput): Promise<OrderResult> {
  const margin = marginEvidence(input.amount, input.supplierCost);
  if (margin && input.supplierCost) {
    console.log(
      `[economics] orderRef=${input.orderRef} supplierCost=${input.supplierCost.amount} USD ` +
        `sale=${input.amount} USDC projectedGrossMargin=${margin.projectedGrossMarginAmount} USDC ` +
        `marginPct=${margin.projectedGrossMarginPct} basis=${margin.basis} ` +
        `capturedAt=${input.supplierCost.capturedAt}`,
    );
  } else {
    console.warn(
      `[economics] orderRef=${input.orderRef} supplier-cost snapshot unavailable; ` +
        "margin was not calculated",
    );
  }
  const supplierOrder = supplierOrderForInput(
    input,
    config.supplierFulfillmentEnabled && !config.mock,
  );
  if (config.mock) {
    const n = ++mockCounter;
    console.log(
      `[commerce] MOCK order for ${input.orderRef} (tx=${input.txSignature.slice(0, 12)}…)`,
    );
    return {
      shopifyOrderId: `gid://shopify/Order/${n}`,
      name: `#${n}`,
      mocked: true,
      supplierOrder,
    };
  }

  const order = await createOrder(input);
  await markOrderPaid(order);
  return {
    shopifyOrderId: order.id,
    name: order.name,
    mocked: false,
    supplierOrder,
  };
}

/** Create a paid order once per orderRef (or return the original result). */
export async function createPaidOrder(input: OrderInput): Promise<OrderResult> {
  const completed = completedOrders.get(input.orderRef);
  if (completed) return completed;

  const inFlight = inFlightOrders.get(input.orderRef);
  if (inFlight) return inFlight;

  const operation = createPaidOrderOnce(input);
  inFlightOrders.set(input.orderRef, operation);
  try {
    const result = await operation;
    completedOrders.set(input.orderRef, result);
    if (config.mock) {
      mockOrders.set(input.orderRef, {
        input,
        result,
        createdAt: new Date().toISOString(),
        financialStatus: "PAID",
        fulfillmentStatus: "UNFULFILLED",
        refundTxSignature: null,
        refundReference: null,
        refundExplorer: null,
      });
    }
    return result;
  } finally {
    if (inFlightOrders.get(input.orderRef) === operation) {
      inFlightOrders.delete(input.orderRef);
    }
  }
}

function projectSupplierOrder(order: ShopifyOrder): SupplierOrder {
  const status = customAttribute(order, "supplier_order_status");
  const allowed = new Set<SupplierOrder["status"]>([
    "disabled",
    "blocked",
    "not_connected",
    "pending",
    "submitted",
    "confirmed",
    "failed",
  ]);
  if (!allowed.has(status as SupplierOrder["status"])) {
    return disabledSupplierOrder();
  }
  const typedStatus = status as SupplierOrder["status"];
  const ref = customAttribute(order, "supplier_order_ref") || null;
  return {
    provider: "dsers",
    status: typedStatus,
    ref:
      typedStatus === "disabled" ||
      typedStatus === "blocked" ||
      typedStatus === "not_connected"
        ? null
        : ref,
    message:
      customAttribute(order, "supplier_order_message") ||
      (typedStatus === "disabled"
        ? SUPPLIER_FULFILLMENT_DISABLED_MESSAGE
        : typedStatus === "blocked"
          ? SUPPLIER_FULFILLMENT_BLOCKED_MESSAGE
          : typedStatus === "not_connected"
            ? "Legacy order: supplier integration state was not connected."
            : `DSers supplier order is ${typedStatus}.`),
  };
}

export function trackingInfoFromFulfillments(
  fulfillments: ShopifyFulfillment[] | null | undefined,
): TrackingInfo | null {
  const tracking = fulfillments
    ?.flatMap((fulfillment) =>
      fulfillment.trackingInfo.map((info) => ({
        ...info,
        fulfillmentStatus: fulfillment.status,
      })),
    )
    .find((info) => info.number && info.company);
  if (!tracking?.number || !tracking.company) return null;
  return {
    provider: "shopify",
    carrier: tracking.company,
    trackingNumber: tracking.number,
    status: tracking.fulfillmentStatus || "unknown",
    statusDetail: null,
    trackingUrl: tracking.url,
    estimatedDeliveryAt: null,
    demo: false,
    message:
      "Real carrier tracking was read from the Shopify fulfillment record; Relay did not synthesize it.",
  };
}

function projectOrderStatus(order: ShopifyOrder): OrderStatus {
  const txSignature = customAttribute(order, "tx_signature");
  const refundTxSignature = customAttribute(order, "refund_tx_signature");
  const amount =
    customAttribute(order, "usdc_amount") ||
    order.totalPriceSet?.shopMoney.amount ||
    "";
  const fallbackSku = customAttribute(order, "sku");
  const lineItems = (order.lineItems?.nodes ?? []).map((lineItem, index) => ({
    title: lineItem.title,
    sku: lineItem.sku || (index === 0 ? fallbackSku : ""),
    quantity: lineItem.quantity,
  }));
  return {
    shopifyOrderId: order.id,
    orderRef: customAttribute(order, "order_ref"),
    name: order.name,
    financialStatus: order.displayFinancialStatus,
    fulfillmentStatus: order.displayFulfillmentStatus ?? "UNFULFILLED",
    lineItems,
    amount: { amount, currency: "USDC" },
    payment: {
      reference: customAttribute(order, "payment_reference") || null,
      txSignature,
      explorer: `https://explorer.solana.com/tx/${encodeURIComponent(
        txSignature,
      )}?cluster=${encodeURIComponent(config.cluster)}`,
    },
    refund: {
      status: refundTxSignature ? "refunded" : "not_refunded",
      reference: customAttribute(order, "refund_reference") || null,
      txSignature: refundTxSignature || null,
      explorer: customAttribute(order, "refund_explorer") || null,
    },
    supplierOrder: projectSupplierOrder(order),
    // A fulfillment status alone is not supplier proof. Expose tracking only
    // when Shopify carries both a real carrier and number.
    tracking: trackingInfoFromFulfillments(order.fulfillments),
  };
}

function projectMockOrderStatus(record: MockOrderRecord): OrderStatus {
  return {
    shopifyOrderId: record.result.shopifyOrderId,
    orderRef: record.input.orderRef,
    name: record.result.name,
    financialStatus: record.financialStatus,
    fulfillmentStatus: record.fulfillmentStatus,
    lineItems: [
      {
        title: record.input.title,
        sku: record.input.sku ?? "",
        quantity: 1,
      },
    ],
    amount: { amount: record.input.amount, currency: "USDC" },
    payment: {
      reference: record.input.paymentReference ?? null,
      txSignature: record.input.txSignature,
      explorer: record.input.explorer,
    },
    refund: {
      status: record.refundTxSignature ? "refunded" : "not_refunded",
      reference: record.refundReference,
      txSignature: record.refundTxSignature,
      explorer: record.refundExplorer,
    },
    supplierOrder: record.result.supplierOrder,
    tracking: null,
  };
}

function findMockOrder(identifier: string): MockOrderRecord {
  const record = identifier.startsWith("#")
    ? [...mockOrders.values()].find(({ result }) => result.name === identifier)
    : mockOrders.get(identifier);
  if (!record) {
    throw new OrderNotFoundError(`Order ${identifier} was not found`);
  }
  return record;
}

/** Resolve a Relay order by orderRef or Shopify order name (for example #1006). */
export async function getOrderStatus(identifier: string): Promise<OrderStatus> {
  if (config.mock) return projectMockOrderStatus(findMockOrder(identifier));
  const order = await findOrder(identifier, { required: true });
  return projectOrderStatus(order!);
}

function projectWalletOrder(order: ShopifyOrder): WalletOrder {
  const txSignature = customAttribute(order, "tx_signature");
  return {
    shopifyOrderId: order.id,
    name: order.name,
    status: order.displayFinancialStatus,
    createdAt: order.createdAt ?? "",
    orderRef: customAttribute(order, "order_ref"),
    title: order.lineItems?.nodes[0]?.title ?? "",
    amount: customAttribute(order, "usdc_amount"),
    buyerWallet: customAttribute(order, "buyer_wallet"),
    txSignature,
    explorer: txSignature
      ? `https://explorer.solana.com/tx/${encodeURIComponent(
        txSignature,
      )}?cluster=${encodeURIComponent(config.cluster)}`
      : "",
    supplierOrder: projectSupplierOrder(order),
  };
}

/** Return only orders whose buyer_wallet attribute matches the signed-in wallet. */
export async function listOrdersByWallet(
  buyerWallet: string,
): Promise<WalletOrder[]> {
  if (config.mock) {
    return [...mockOrders.values()]
      .filter(({ input }) => input.buyerAddress === buyerWallet)
      .sort((a, b) => b.createdAt.localeCompare(a.createdAt))
      .map(({ input, result, createdAt, financialStatus }) => ({
        shopifyOrderId: result.shopifyOrderId,
        name: result.name,
        status: financialStatus,
        createdAt,
        orderRef: input.orderRef,
        title: input.title,
        amount: input.amount,
        buyerWallet: input.buyerAddress,
        txSignature: input.txSignature,
        explorer: input.explorer,
        supplierOrder: result.supplierOrder,
      }));
  }

  const matches: WalletOrder[] = [];
  let after: string | null = null;
  do {
    const data: FindOrderData = await shopifyGraphQL<FindOrderData>(
      LIST_RELAY_ORDERS,
      { query: "tag:relay", after },
    );
    matches.push(
      ...data.orders.nodes
        .filter(
          (order) => customAttribute(order, "buyer_wallet") === buyerWallet,
        )
        .map(projectWalletOrder),
    );
    after = data.orders.pageInfo.hasNextPage
      ? data.orders.pageInfo.endCursor
      : null;
  } while (after);
  return matches;
}

function deterministicRefundIdempotencyKey(orderRef: string): string {
  const compact = orderRef.replace(/^ord_/, "");
  if (/^[0-9a-f]{32}$/i.test(compact)) {
    return [
      compact.slice(0, 8),
      compact.slice(8, 12),
      compact.slice(12, 16),
      compact.slice(16, 20),
      compact.slice(20),
    ].join("-");
  }
  const bytes = Buffer.from(
    createHash("sha256").update(`relay-refund:${orderRef}`).digest().subarray(0, 16),
  );
  bytes[6] = (bytes[6]! & 0x0f) | 0x50;
  bytes[8] = (bytes[8]! & 0x3f) | 0x80;
  const hex = bytes.toString("hex");
  return [
    hex.slice(0, 8),
    hex.slice(8, 12),
    hex.slice(12, 16),
    hex.slice(16, 20),
    hex.slice(20),
  ].join("-");
}

async function updateRefundAttributes(
  order: ShopifyOrder,
  refundReference: string,
  refundTxSignature: string,
  refundExplorer: string,
): Promise<void> {
  const updated = await shopifyGraphQL<OrderUpdateData>(ORDER_UPDATE_ATTRIBUTES, {
    input: {
      id: order.id,
      customAttributes: mergedAttributes(order, {
        refund_status: "refunded",
        refund_reference: refundReference,
        refund_tx_signature: refundTxSignature,
        refund_explorer: refundExplorer,
      }),
    },
  });
  const errors = updated.orderUpdate.userErrors;
  if (errors.length || !updated.orderUpdate.order) {
    throw new Error(`orderUpdate refund attributes failed: ${JSON.stringify(errors)}`);
  }
}

async function createShopifyRefund(order: ShopifyOrder): Promise<void> {
  if (order.displayFinancialStatus !== "PAID") {
    throw new OrderLifecycleConflictError(
      `Order ${customAttribute(order, "order_ref")} is not paid ` +
        `(financialStatus=${order.displayFinancialStatus})`,
    );
  }
  const parent = order.transactions?.find(
    (transaction) =>
      transaction.status === "SUCCESS" &&
      (transaction.kind === "SALE" || transaction.kind === "CAPTURE"),
  );
  if (!parent) {
    throw new Error(`Shopify order ${order.id} has no refundable payment transaction`);
  }
  const amount =
    customAttribute(order, "usdc_amount") ||
    order.totalPriceSet?.shopMoney.amount;
  if (!amount) {
    throw new Error(`Shopify order ${order.id} has no recorded refund amount`);
  }
  const lineItems = order.lineItems?.nodes ?? [];
  if (lineItems.length === 0) {
    throw new Error(`Shopify order ${order.id} has no refundable line items`);
  }

  const useIdempotencyDirective = config.shopify.apiVersion >= "2026-01";
  const variables: Record<string, unknown> = {
    input: {
      orderId: order.id,
      notify: false,
      note:
        "Full ledger refund after autonomous on-chain USDC merchant-to-buyer transfer.",
      refundLineItems: lineItems.map((lineItem) => ({
        lineItemId: lineItem.id,
        quantity: lineItem.quantity,
      })),
      transactions: [
        {
          orderId: order.id,
          parentId: parent.id,
          gateway: parent.gateway || "manual",
          kind: "REFUND",
          amount,
        },
      ],
    },
  };
  if (useIdempotencyDirective) {
    variables.idempotencyKey = deterministicRefundIdempotencyKey(
      customAttribute(order, "order_ref"),
    );
  }

  const result = await shopifyGraphQL<RefundCreateData>(
    refundCreateMutation(useIdempotencyDirective),
    variables,
  );
  const errors = result.refundCreate.userErrors;
  if (errors.length || !result.refundCreate.refund) {
    throw new Error(`refundCreate failed: ${JSON.stringify(errors)}`);
  }
  if (result.refundCreate.order?.displayFinancialStatus !== "REFUNDED") {
    throw new Error(
      `refundCreate returned unexpected financial status: ${
        result.refundCreate.order?.displayFinancialStatus ?? "missing order"
      }`,
    );
  }
}

async function markOrderRefundedOnce(
  orderRef: string,
  refundReference: string,
  refundTxSignature: string,
  refundExplorer: string,
): Promise<OrderStatus> {
  if (config.mock) {
    const record = findMockOrder(orderRef);
    if (
      record.refundTxSignature &&
      record.refundTxSignature !== refundTxSignature
    ) {
      throw new OrderLifecycleConflictError(
        `Order ${orderRef} is already refunded by ${record.refundTxSignature}`,
      );
    }
    record.refundReference = refundReference;
    record.refundTxSignature = refundTxSignature;
    record.refundExplorer = refundExplorer;
    record.financialStatus = "REFUNDED";
    return projectMockOrderStatus(record);
  }

  let order = (await findOrder(orderRef, { required: true }))!;
  const existingSignature = customAttribute(order, "refund_tx_signature");
  const existingReference = customAttribute(order, "refund_reference");
  if (existingSignature) {
    if (existingSignature !== refundTxSignature) {
      throw new OrderLifecycleConflictError(
        `Order ${orderRef} is already refunded by ${existingSignature}`,
      );
    }
    if (existingReference && existingReference !== refundReference) {
      throw new OrderLifecycleConflictError(
        `Order ${orderRef} already records refund reference ${existingReference}`,
      );
    }
    if (!existingReference) {
      await updateRefundAttributes(
        order,
        refundReference,
        refundTxSignature,
        refundExplorer,
      );
      order = (await findOrder(orderRef, { required: true }))!;
    }
    return projectOrderStatus(order);
  }
  if (
    order.displayFinancialStatus !== "PAID" &&
    order.displayFinancialStatus !== "REFUNDED"
  ) {
    throw new OrderLifecycleConflictError(
      `Order ${orderRef} cannot be refunded from financialStatus=` +
        order.displayFinancialStatus,
    );
  }

  // If refundCreate committed but attribute persistence failed, a retry sees
  // REFUNDED and only repairs the custom attributes.
  if (order.displayFinancialStatus !== "REFUNDED") {
    await createShopifyRefund(order);
    order = (await findOrder(orderRef, { required: true }))!;
  }
  await updateRefundAttributes(
    order,
    refundReference,
    refundTxSignature,
    refundExplorer,
  );
  order = (await findOrder(orderRef, { required: true }))!;
  if (order.displayFinancialStatus !== "REFUNDED") {
    throw new Error(
      `Shopify order ${orderRef} did not become REFUNDED ` +
        `(financialStatus=${order.displayFinancialStatus})`,
    );
  }
  return projectOrderStatus(order);
}

/** Mark the already-on-chain-refunded order in Shopify, idempotently. */
export async function markOrderRefunded(
  orderRef: string,
  refundReference: string,
  refundTxSignature: string,
  refundExplorer: string,
): Promise<OrderStatus> {
  const inFlight = inFlightRefunds.get(orderRef);
  if (inFlight) return inFlight;
  const operation = markOrderRefundedOnce(
    orderRef,
    refundReference,
    refundTxSignature,
    refundExplorer,
  );
  inFlightRefunds.set(orderRef, operation);
  try {
    return await operation;
  } finally {
    if (inFlightRefunds.get(orderRef) === operation) {
      inFlightRefunds.delete(orderRef);
    }
  }
}

async function fulfillOrderOnce(orderRef: string): Promise<FulfillmentResult> {
  const status = await getOrderStatus(orderRef);
  throw new OrderLifecycleConflictError(
    `Order ${status.orderRef} cannot be fulfilled: ${status.supplierOrder.message}`,
  );
}

/** Refuse fulfillment until a real supplier order and carrier number exist. */
export async function fulfillOrder(orderRef: string): Promise<FulfillmentResult> {
  const inFlight = inFlightFulfillments.get(orderRef);
  if (inFlight) return inFlight;
  const operation = fulfillOrderOnce(orderRef);
  inFlightFulfillments.set(orderRef, operation);
  try {
    return await operation;
  } finally {
    if (inFlightFulfillments.get(orderRef) === operation) {
      inFlightFulfillments.delete(orderRef);
    }
  }
}

/** Refuse tracking until a real supplier order supplies a real waybill. */
export async function trackOrder(identifier: string): Promise<TrackingInfo> {
  const status = await getOrderStatus(identifier);
  if (status.tracking && !status.tracking.demo) return status.tracking;
  throw new OrderLifecycleConflictError(
    `Order ${status.orderRef} has no real tracking number: ${status.supplierOrder.message}`,
  );
}
