import { config } from "./config.js";

export interface OrderInput {
  orderRef: string;
  productId: string;
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

interface GraphQLError {
  message: string;
}

async function shopifyGraphQL<T>(query: string, variables: Record<string, unknown>): Promise<T> {
  const url = `https://${config.shopify.domain}/admin/api/${config.shopify.apiVersion}/graphql.json`;
  const res = await fetch(url, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "X-Shopify-Access-Token": config.shopify.token,
    },
    body: JSON.stringify({ query, variables }),
  });
  if (!res.ok) {
    throw new Error(`Shopify HTTP ${res.status}: ${await res.text()}`);
  }
  const json = (await res.json()) as { data?: T; errors?: GraphQLError[] };
  if (json.errors?.length) {
    throw new Error(`Shopify GraphQL: ${json.errors.map((e) => e.message).join("; ")}`);
  }
  return json.data as T;
}

// PRD §5, Step 9 — orderCreate. A custom line item carries our resale price;
// on-chain payment metadata is attached as order attributes + note.
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

interface OrderCreateData {
  orderCreate: {
    order: { id: string; name: string; displayFinancialStatus: string } | null;
    userErrors: { field: string[] | null; message: string }[];
  };
}
interface OrderMarkAsPaidData {
  orderMarkAsPaid: {
    order: { id: string; displayFinancialStatus: string } | null;
    userErrors: { field: string[] | null; message: string }[];
  };
}

let mockCounter = 1000;

/** Create a paid order in Shopify (or a mock one if not configured). */
export async function createPaidOrder(input: OrderInput): Promise<OrderResult> {
  if (config.mock) {
    const n = ++mockCounter;
    console.log(`[commerce] MOCK order for ${input.orderRef} (tx=${input.txSignature.slice(0, 12)}…)`);
    return { shopifyOrderId: `gid://shopify/Order/${n}`, name: `#${n}`, mocked: true };
  }

  const created = await shopifyGraphQL<OrderCreateData>(ORDER_CREATE, {
    order: {
      currency: config.shopify.currency,
      lineItems: [
        {
          title: input.title,
          quantity: 1,
          priceSet: {
            shopMoney: { amount: input.amount, currencyCode: config.shopify.currency },
          },
        },
      ],
      note: `Autonomous agent order. Paid on-chain: ${input.explorer}`,
      customAttributes: [
        { key: "order_ref", value: input.orderRef },
        { key: "usdc_amount", value: input.amount },
        { key: "tx_signature", value: input.txSignature },
        { key: "network", value: `solana-${config.cluster}` },
        { key: "buyer_wallet", value: input.buyerAddress },
        { key: "ship_to", value: input.shipTo },
      ],
    },
  });

  const errs = created.orderCreate.userErrors;
  if (errs.length || !created.orderCreate.order) {
    throw new Error(`orderCreate failed: ${JSON.stringify(errs)}`);
  }
  const order = created.orderCreate.order;

  // Step 9 (cont.) — orderMarkAsPaid.
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

  return { shopifyOrderId: order.id, name: order.name, mocked: false };
}
