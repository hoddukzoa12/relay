import type { WalletOrder } from "@arb/shared";
import { config } from "./config.js";
import { ShopifyAdminClient } from "./shopify-client.js";

export interface OrderInput {
  orderRef: string;
  productId: string; // retained for compatibility; contains the variant id
  variantId?: string;
  sku?: string;
  title: string;
  amount: string; // USDC decimal string paid on-chain
  buyerAddress: string; // buyer wallet pubkey
  shipTo: string;
  txSignature: string;
  explorer: string;
}

export interface OrderResult {
  shopifyOrderId: string;
  name: string;
  mocked: boolean;
}

const shopifyAdmin = new ShopifyAdminClient(config.shopify);

export async function shopifyGraphQL<T>(
  query: string,
  variables: Record<string, unknown>,
): Promise<T> {
  return shopifyAdmin.graphql<T>(query, variables);
}

// PRD §5, Step 9 — orderCreate. The real catalog variant stays attached while
// priceSet records the broker's marked-up resale price.
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

const FIND_ORDER_BY_REF = /* GraphQL */ `
  query FindOrderByRef($query: String!, $after: String) {
    orders(first: 50, after: $after, query: $query, sortKey: CREATED_AT, reverse: true) {
      nodes {
        id
        name
        displayFinancialStatus
        customAttributes { key value }
      }
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

interface ShopifyOrder {
  id: string;
  name: string;
  displayFinancialStatus: string;
  createdAt?: string;
  customAttributes?: { key: string; value: string | null }[];
  lineItems?: { nodes: { title: string }[] };
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

let mockCounter = 1000;
const completedOrders = new Map<string, OrderResult>();
const inFlightOrders = new Map<string, Promise<OrderResult>>();
const orderInputs = new Map<
  string,
  { input: OrderInput; result: OrderResult; createdAt: string }
>();

export function orderTag(orderRef: string): string {
  const compactRef = orderRef.startsWith("ord_") ? orderRef.slice(4) : orderRef;
  return `relay_${compactRef}`.slice(0, 40);
}

async function findOrderByRef(orderRef: string): Promise<ShopifyOrder | null> {
  let after: string | null = null;
  do {
    const data: FindOrderData = await shopifyGraphQL<FindOrderData>(
      FIND_ORDER_BY_REF,
      {
        query: `tag:${JSON.stringify(orderTag(orderRef))}`,
        after,
      },
    );
    const match = data.orders.nodes.find((order) =>
      order.customAttributes?.some(
        (attribute) =>
          attribute.key === "order_ref" && attribute.value === orderRef,
      ),
    );
    if (match) return match;
    after = data.orders.pageInfo.hasNextPage
      ? data.orders.pageInfo.endCursor
      : null;
  } while (after);
  return null;
}

async function createOrder(input: OrderInput): Promise<ShopifyOrder> {
  const existing = await findOrderByRef(input.orderRef);
  if (existing) {
    console.log(
      `[commerce] reusing Shopify order ${existing.id} for ${input.orderRef}`,
    );
    return existing;
  }

  const created = await shopifyGraphQL<OrderCreateData>(ORDER_CREATE, {
    order: {
      currency: config.shopify.currency,
      lineItems: [
        {
          variantId: input.variantId ?? input.productId,
          quantity: 1,
          priceSet: {
            shopMoney: { amount: input.amount, currencyCode: config.shopify.currency },
          },
        },
      ],
      note: `Autonomous agent order. Paid on-chain: ${input.explorer}`,
      tags: ["relay", orderTag(input.orderRef)],
      customAttributes: [
        { key: "order_ref", value: input.orderRef },
        { key: "usdc_amount", value: input.amount },
        { key: "tx_signature", value: input.txSignature },
        { key: "network", value: `solana-${config.cluster}` },
        { key: "buyer_wallet", value: input.buyerAddress },
        { key: "ship_to", value: input.shipTo },
        { key: "variant_id", value: input.variantId ?? input.productId },
        { key: "sku", value: input.sku ?? "" },
      ],
    },
  });

  const errs = created.orderCreate.userErrors;
  if (errs.length || !created.orderCreate.order) {
    throw new Error(`orderCreate failed: ${JSON.stringify(errs)}`);
  }
  return created.orderCreate.order;
}

async function markOrderPaid(order: ShopifyOrder): Promise<void> {
  if (order.displayFinancialStatus === "PAID") return;

  const paid = await shopifyGraphQL<OrderMarkAsPaidData>(ORDER_MARK_AS_PAID, {
    input: { id: order.id },
  });
  if (paid.orderMarkAsPaid.userErrors.length) {
    throw new Error(`orderMarkAsPaid failed: ${JSON.stringify(paid.orderMarkAsPaid.userErrors)}`);
  }
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
}

async function createPaidOrderOnce(input: OrderInput): Promise<OrderResult> {
  if (config.mock) {
    const n = ++mockCounter;
    console.log(`[commerce] MOCK order for ${input.orderRef} (tx=${input.txSignature.slice(0, 12)}…)`);
    return { shopifyOrderId: `gid://shopify/Order/${n}`, name: `#${n}`, mocked: true };
  }

  // Creation and mark-paid are deliberately separate. If mark-paid fails,
  // retry finds the custom-attribute-tagged order and skips orderCreate.
  const order = await createOrder(input);
  await markOrderPaid(order);
  return { shopifyOrderId: order.id, name: order.name, mocked: false };
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
    orderInputs.set(input.orderRef, {
      input,
      result,
      createdAt: new Date().toISOString(),
    });
    return result;
  } finally {
    if (inFlightOrders.get(input.orderRef) === operation) {
      inFlightOrders.delete(input.orderRef);
    }
  }
}

function customAttribute(order: ShopifyOrder, key: string): string {
  return (
    order.customAttributes?.find((attribute) => attribute.key === key)?.value ??
    ""
  );
}

function projectOrder(order: ShopifyOrder): WalletOrder {
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
      ? `https://explorer.solana.com/tx/${encodeURIComponent(txSignature)}?cluster=${encodeURIComponent(config.cluster)}`
      : "",
  };
}

/** Return only orders whose buyer_wallet attribute matches the signed-in wallet. */
export async function listOrdersByWallet(
  buyerWallet: string,
): Promise<WalletOrder[]> {
  if (config.mock) {
    return [...orderInputs.values()]
      .filter(({ input }) => input.buyerAddress === buyerWallet)
      .sort((a, b) => b.createdAt.localeCompare(a.createdAt))
      .map(({ input, result, createdAt }) => ({
        shopifyOrderId: result.shopifyOrderId,
        name: result.name,
        status: "PAID",
        createdAt,
        orderRef: input.orderRef,
        title: input.title,
        amount: input.amount,
        buyerWallet: input.buyerAddress,
        txSignature: input.txSignature,
        explorer: input.explorer,
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
        .map(projectOrder),
    );
    after = data.orders.pageInfo.hasNextPage
      ? data.orders.pageInfo.endCursor
      : null;
  } while (after);
  return matches;
}
