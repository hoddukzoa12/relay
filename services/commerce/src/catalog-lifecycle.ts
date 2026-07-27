import { shopifyGraphQL } from "./shopify-admin.js";

const PRODUCT_ID = /^gid:\/\/shopify\/Product\/\d+$/;
const AUTONOMOUS_VENDOR = "Relay DSers Autonomous";
const AUTONOMOUS_TAG = "relay:autonomous-sourced";

type GraphQL = <T>(
  query: string,
  variables: Record<string, unknown>,
) => Promise<T>;

export interface CatalogLifecycleFulfillment {
  id: string;
  status: string;
  trackingInfo: {
    company: string | null;
    number: string | null;
    url: string | null;
  }[];
  fulfillmentLineItems?: {
    nodes: {
      lineItem: {
        variant: {
          product: { id: string };
        } | null;
      };
    }[];
  };
}

export interface CatalogLifecycleOrder {
  id: string;
  name: string;
  fulfillments?: CatalogLifecycleFulfillment[];
}

export interface CatalogRetirementResult {
  productId: string;
  action: "retired" | "already_unlisted" | "not_autonomous" | "missing";
  previousStatus: string | null;
}

interface ProductInspectionData {
  product: {
    id: string;
    vendor: string;
    status: string;
    tags: string[];
  } | null;
}

interface ProductUpdateData {
  productUpdate: {
    product: {
      id: string;
      vendor: string;
      status: string;
      tags: string[];
    } | null;
    userErrors: { field: string[] | null; message: string }[];
  };
}

interface RelayOrdersData {
  orders: {
    nodes: CatalogLifecycleOrder[];
    pageInfo: { hasNextPage: boolean; endCursor: string | null };
  };
}

const INSPECT_PRODUCT = /* GraphQL */ `
  query InspectCatalogLifecycleProduct($id: ID!) {
    product(id: $id) {
      id
      vendor
      status
      tags
    }
  }
`;

const DRAFT_PRODUCT = /* GraphQL */ `
  mutation DraftFulfilledAutonomousProduct($product: ProductUpdateInput!) {
    productUpdate(product: $product) {
      product { id vendor status tags }
      userErrors { field message }
    }
  }
`;

const RELAY_ORDER_FULFILLMENTS = /* GraphQL */ `
  query RelayOrderCatalogLifecycle($query: String!, $after: String) {
    orders(
      first: 50
      after: $after
      query: $query
      sortKey: CREATED_AT
      reverse: true
    ) {
      nodes {
        id
        name
        fulfillments(first: 50) {
          id
          status
          trackingInfo(first: 10) { company number url }
          fulfillmentLineItems(first: 50) {
            nodes {
              lineItem {
                variant { product { id } }
              }
            }
          }
        }
      }
      pageInfo { hasNextPage endCursor }
    }
  }
`;

function present(value: string | null | undefined): boolean {
  return Boolean(value?.trim());
}

/**
 * Shopify's display fulfillment status is not delivery proof. Relay requires
 * the fulfillment itself to be SUCCESS and to carry a real carrier + waybill.
 */
export function hasAuthoritativeDeliverySignal(
  fulfillment: CatalogLifecycleFulfillment,
): boolean {
  return (
    fulfillment.status === "SUCCESS" &&
    fulfillment.trackingInfo.some(
      (tracking) => present(tracking.company) && present(tracking.number),
    )
  );
}

/** Return exact product GIDs from only proven successful tracked fulfillments. */
export function deliveredProductIds(
  order: CatalogLifecycleOrder,
): string[] {
  return [
    ...new Set(
      (order.fulfillments ?? [])
        .filter(hasAuthoritativeDeliverySignal)
        .flatMap(
          (fulfillment) => fulfillment.fulfillmentLineItems?.nodes ?? [],
        )
        .map((item) => item.lineItem.variant?.product.id ?? "")
        .filter((id) => PRODUCT_ID.test(id)),
    ),
  ];
}

async function retireAutonomousSourcedProductOnce(
  productId: string,
  graphql: GraphQL,
): Promise<CatalogRetirementResult> {
  if (!PRODUCT_ID.test(productId)) {
    throw new Error("catalog retirement requires an exact Shopify product GID");
  }
  const inspected = await graphql<ProductInspectionData>(INSPECT_PRODUCT, {
    id: productId,
  });
  const product = inspected.product;
  if (!product || product.id !== productId) {
    return { productId, action: "missing", previousStatus: null };
  }
  const autonomous =
    product.vendor === AUTONOMOUS_VENDOR ||
    product.tags.includes(AUTONOMOUS_TAG);
  if (!autonomous) {
    return {
      productId,
      action: "not_autonomous",
      previousStatus: product.status,
    };
  }
  if (product.status !== "ACTIVE") {
    return {
      productId,
      action: "already_unlisted",
      previousStatus: product.status,
    };
  }

  const updated = await graphql<ProductUpdateData>(DRAFT_PRODUCT, {
    product: { id: productId, status: "DRAFT" },
  });
  const errors = updated.productUpdate.userErrors;
  const result = updated.productUpdate.product;
  if (
    errors.length ||
    !result ||
    result.id !== productId ||
    result.status !== "DRAFT"
  ) {
    throw new Error(
      `productUpdate catalog retirement failed: ${JSON.stringify(errors)}`,
    );
  }
  return { productId, action: "retired", previousStatus: product.status };
}

const inFlightRetirements = new Map<
  string,
  Promise<CatalogRetirementResult>
>();

/**
 * Draft one exact product only when Shopify proves autonomous provenance.
 * Product deletion and title matching are intentionally absent.
 */
export async function retireAutonomousSourcedProduct(
  productId: string,
  options: { graphql?: GraphQL } = {},
): Promise<CatalogRetirementResult> {
  const existing = inFlightRetirements.get(productId);
  if (existing) return existing;
  const operation = retireAutonomousSourcedProductOnce(
    productId,
    options.graphql ?? shopifyGraphQL,
  );
  inFlightRetirements.set(productId, operation);
  try {
    return await operation;
  } finally {
    if (inFlightRetirements.get(productId) === operation) {
      inFlightRetirements.delete(productId);
    }
  }
}

export async function reconcileOrderCatalogLifecycle(
  order: CatalogLifecycleOrder,
  options: { graphql?: GraphQL } = {},
): Promise<CatalogRetirementResult[]> {
  return Promise.all(
    deliveredProductIds(order).map((productId) =>
      retireAutonomousSourcedProduct(productId, options),
    ),
  );
}

/**
 * Reconcile the full Relay order ledger before catalog search. Orders without
 * SUCCESS + carrier + tracking number cause no product mutation.
 */
export async function reconcileCompletedCatalogLifecycle(
  options: { graphql?: GraphQL; maxPages?: number } = {},
): Promise<CatalogRetirementResult[]> {
  const graphql = options.graphql ?? shopifyGraphQL;
  const maxPages = options.maxPages ?? 20;
  const results: CatalogRetirementResult[] = [];
  const seenProducts = new Set<string>();
  let after: string | null = null;
  for (let page = 0; page < maxPages; page += 1) {
    const data: RelayOrdersData = await graphql<RelayOrdersData>(
      RELAY_ORDER_FULFILLMENTS,
      { query: "tag:relay", after },
    );
    for (const order of data.orders.nodes) {
      for (const productId of deliveredProductIds(order)) {
        if (seenProducts.has(productId)) continue;
        seenProducts.add(productId);
        results.push(
          await retireAutonomousSourcedProduct(productId, { graphql }),
        );
      }
    }
    if (!data.orders.pageInfo.hasNextPage) return results;
    after = data.orders.pageInfo.endCursor;
    if (!after) {
      throw new Error("Shopify order pagination omitted its next cursor");
    }
  }
  throw new Error(
    `catalog lifecycle reconciliation exceeded ${maxPages} order pages`,
  );
}
