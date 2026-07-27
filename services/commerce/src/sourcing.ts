import type { CatalogProduct, SupplierCostSnapshot } from "@arb/shared";
import { config } from "./config.js";
import { rememberRecentlySourcedProduct } from "./catalog.js";
import { shopifyGraphQL } from "./shopify-admin.js";

const PRODUCT_ID = /^gid:\/\/shopify\/Product\/\d+$/;
const DECIMAL = /^\d+(\.\d{1,6})?$/;
const AUTONOMOUS_VENDOR = "Relay DSers Autonomous";
const REQUIRED_TAGS = ["relay:autonomous-sourced", "relay:dsers"];
const NAMESPACE = "relay";
const PROTECTED_KEYS = new Set([
  "supplier_cost",
  "supplier_cost_currency",
  "supplier_cost_source",
  "supplier_cost_captured_at",
  "supplier_cost_ship_to",
  "supplier_url",
  "supplier_product_id",
  "dsers_product_id",
]);

export interface SourcedVariantInput {
  sku: string;
  cost: string;
  supplierInventory: number;
}

export interface SourcingMetadataInput {
  productId: string;
  vendor: string;
  tags: string[];
  importItemId: string;
  sourceUrl: string;
  supplierProductId: string;
  dsersProductId: string;
  capturedAt: string;
  shipTo: string;
  variants: SourcedVariantInput[];
}

interface ShopifySourcingVariant {
  id: string;
  sku: string | null;
  title: string;
  price: string;
  inventoryQuantity: number | null;
  metafields?: {
    nodes: { namespace: string; key: string; type: string; value: string }[];
  };
}

interface ShopifySourcingProduct {
  id: string;
  title: string;
  description: string;
  vendor: string;
  status: string;
  tags: string[];
  variants: { nodes: ShopifySourcingVariant[] };
}

interface InspectData {
  product: ShopifySourcingProduct | null;
  metafieldDefinitions: {
    nodes: {
      namespace: string;
      key: string;
      access: { storefront: string };
    }[];
  };
}

interface ResolveHandleData {
  productByHandle: {
    id: string;
    handle: string;
    title: string;
    variants: {
      nodes: {
        id: string;
        sku: string | null;
        title: string;
        price: string;
        inventoryQuantity: number | null;
      }[];
    };
  } | null;
}

interface UpdateData {
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

interface MetafieldsSetData {
  metafieldsSet: {
    metafields: {
      ownerType: string;
      namespace: string;
      key: string;
      type: string;
      value: string;
    }[];
    userErrors: {
      field: string[] | null;
      message: string;
      code?: string;
    }[];
  };
}

type GraphQL = <T>(
  query: string,
  variables: Record<string, unknown>,
) => Promise<T>;

const INSPECT = /* GraphQL */ `
  query InspectAutonomousSourcing($id: ID!) {
    product(id: $id) {
      id
      title
      description
      vendor
      status
      tags
      variants(first: 100) {
        nodes {
          id
          sku
          title
          price
          inventoryQuantity
          metafields(first: 12, namespace: "relay") {
            nodes { namespace key type value }
          }
        }
      }
    }
    metafieldDefinitions(
      first: 100
      ownerType: PRODUCTVARIANT
      namespace: "relay"
    ) {
      nodes {
        namespace
        key
        access { storefront }
      }
    }
  }
`;

const RESOLVE_HANDLE = /* GraphQL */ `
  query ResolveAutonomousSourcingHandle($handle: String!) {
    productByHandle(handle: $handle) {
      id
      handle
      title
      variants(first: 100) {
        nodes { id sku title price inventoryQuantity }
      }
    }
  }
`;

const UPDATE_PRODUCT = /* GraphQL */ `
  mutation MarkAutonomousSourcing($product: ProductUpdateInput!) {
    productUpdate(product: $product) {
      product { id vendor status tags }
      userErrors { field message }
    }
  }
`;

const METAFIELDS_SET = /* GraphQL */ `
  mutation SetAutonomousSupplierCosts(
    $metafields: [MetafieldsSetInput!]!
  ) {
    metafieldsSet(metafields: $metafields) {
      metafields { ownerType namespace key type value }
      userErrors { field message code }
    }
  }
`;

function validateInput(input: SourcingMetadataInput): void {
  if (!PRODUCT_ID.test(input.productId)) {
    throw new Error("autonomous sourcing requires an exact Shopify product GID");
  }
  if (input.vendor !== AUTONOMOUS_VENDOR) {
    throw new Error(`autonomous sourcing vendor must be ${AUTONOMOUS_VENDOR}`);
  }
  if (!REQUIRED_TAGS.every((tag) => input.tags.includes(tag))) {
    throw new Error("autonomous sourcing provenance tags are required");
  }
  if (
    !input.importItemId ||
    !input.dsersProductId ||
    !input.supplierProductId ||
    !input.sourceUrl.includes(input.supplierProductId)
  ) {
    throw new Error("DSers and supplier identities are required");
  }
  try {
    const url = new URL(input.sourceUrl);
    if (url.protocol !== "https:") throw new Error("not HTTPS");
  } catch {
    throw new Error("sourceUrl must be an HTTPS supplier URL");
  }
  if (
    !/^\d{4}-\d{2}-\d{2}$/.test(input.capturedAt) ||
    !/^[A-Z]{2}$/.test(input.shipTo)
  ) {
    throw new Error("capturedAt and two-letter shipTo are required");
  }
  if (!input.variants.length) {
    throw new Error("at least one sourced variant is required");
  }
  const skus = new Set<string>();
  for (const variant of input.variants) {
    if (
      !variant.sku ||
      skus.has(variant.sku) ||
      !DECIMAL.test(variant.cost) ||
      Number(variant.cost) <= 0 ||
      !Number.isInteger(variant.supplierInventory) ||
      variant.supplierInventory < 0
    ) {
      throw new Error(`invalid or duplicate sourced SKU ${variant.sku}`);
    }
    skus.add(variant.sku);
  }
}

function assertAdminOnly(
  definitions: InspectData["metafieldDefinitions"]["nodes"],
): void {
  const exposed = definitions.filter(
    (definition) =>
      PROTECTED_KEYS.has(definition.key) &&
      definition.access.storefront !== "NONE",
  );
  if (exposed.length) {
    throw new Error(
      `refusing to write storefront-readable supplier metadata: ${exposed
        .map((definition) => definition.key)
        .join(", ")}`,
    );
  }
}

function bindVariants(
  input: SourcingMetadataInput,
  product: ShopifySourcingProduct,
): {
  live: ShopifySourcingVariant;
  supplied: SourcedVariantInput;
}[] {
  const liveBySku = new Map(
    product.variants.nodes
      .filter((variant) => variant.sku)
      .map((variant) => [variant.sku!, variant]),
  );
  return input.variants.map((supplied) => {
    const live = liveBySku.get(supplied.sku);
    if (!live) {
      throw new Error(
        `Shopify product ${product.id} has no exact variant SKU ${supplied.sku}`,
      );
    }
    return { live, supplied };
  });
}

function metafields(
  input: SourcingMetadataInput,
  bound: ReturnType<typeof bindVariants>,
): {
  ownerId: string;
  namespace: string;
  key: string;
  type: string;
  value: string;
}[] {
  return bound.flatMap(({ live, supplied }) => {
    const common = { ownerId: live.id, namespace: NAMESPACE };
    return [
      {
        ...common,
        key: "supplier_cost",
        type: "number_decimal",
        value: supplied.cost,
      },
      {
        ...common,
        key: "supplier_cost_currency",
        type: "single_line_text_field",
        value: "USD",
      },
      {
        ...common,
        key: "supplier_cost_source",
        type: "single_line_text_field",
        value: "dsers_mcp_snapshot",
      },
      {
        ...common,
        key: "supplier_cost_captured_at",
        type: "date",
        value: input.capturedAt,
      },
      {
        ...common,
        key: "supplier_cost_ship_to",
        type: "single_line_text_field",
        value: input.shipTo,
      },
      {
        ...common,
        key: "supplier_url",
        type: "url",
        value: input.sourceUrl,
      },
      {
        ...common,
        key: "supplier_product_id",
        type: "single_line_text_field",
        value: input.supplierProductId,
      },
      {
        ...common,
        key: "dsers_product_id",
        type: "single_line_text_field",
        value: input.dsersProductId,
      },
    ];
  });
}

function chunks<T>(values: T[], size: number): T[][] {
  const result: T[][] = [];
  for (let index = 0; index < values.length; index += size) {
    result.push(values.slice(index, index + size));
  }
  return result;
}

function supplierCost(
  variant: ShopifySourcingVariant,
): SupplierCostSnapshot | null {
  const values = new Map(
    (variant.metafields?.nodes ?? []).map((field) => [
      field.key,
      field.value,
    ]),
  );
  const amount = values.get("supplier_cost");
  const source = values.get("supplier_cost_source");
  if (!amount || source !== "dsers_mcp_snapshot") return null;
  return {
    amount,
    currency: "USD",
    source: "dsers_mcp_snapshot",
    capturedAt: values.get("supplier_cost_captured_at") ?? "",
    shipTo: values.get("supplier_cost_ship_to") ?? "",
    supplierUrl: values.get("supplier_url") ?? null,
  };
}

function projectProduct(product: ShopifySourcingProduct): CatalogProduct {
  const variant = [...product.variants.nodes]
    .filter(
      (item) =>
        item.sku &&
        (item.inventoryQuantity ?? 0) > 0 &&
        Number(item.price) > 0 &&
        supplierCost(item),
    )
    .sort(
      (a, b) =>
        Number(a.price) - Number(b.price) ||
        (a.sku ?? "").localeCompare(b.sku ?? ""),
    )[0];
  if (!variant?.sku) {
    throw new Error(
      `Shopify product ${product.id} has no sellable cost-backed variant`,
    );
  }
  return {
    productId: product.id,
    variantId: variant.id,
    sku: variant.sku,
    title: product.title,
    description: product.description,
    price: variant.price,
    inventoryQuantity: variant.inventoryQuantity ?? 0,
    status: "ACTIVE",
    tags: product.tags,
    supplierCost: supplierCost(variant),
  };
}

export async function resolveShopifyProductByHandle(
  handle: string,
  options: { graphql?: GraphQL; mock?: boolean } = {},
): Promise<Record<string, unknown>> {
  if (
    handle.length > 255 ||
    !/^[a-z0-9]+(?:-[a-z0-9]+)*$/.test(handle)
  ) {
    throw new Error("invalid exact Shopify product handle");
  }
  if (options.mock ?? config.mock) {
    throw new Error(
      "DSers product resolution requires the live Shopify catalog",
    );
  }
  const graphql = options.graphql ?? shopifyGraphQL;
  const result = await graphql<ResolveHandleData>(RESOLVE_HANDLE, { handle });
  const product = result.productByHandle;
  if (!product || product.handle !== handle || !PRODUCT_ID.test(product.id)) {
    throw new Error(`Shopify product handle ${handle} was not found exactly`);
  }
  return {
    productId: product.id,
    handle: product.handle,
    title: product.title,
    variants: product.variants.nodes.map((variant) => ({
      variantId: variant.id,
      sku: variant.sku,
      title: variant.title,
      price: variant.price,
      inventoryQuantity: variant.inventoryQuantity ?? 0,
    })),
    matching: "exact Shopify handle (never title)",
  };
}

export async function markAutonomousSourcedProduct(
  input: SourcingMetadataInput,
  options: { graphql?: GraphQL; mock?: boolean } = {},
): Promise<Record<string, unknown>> {
  validateInput(input);
  if (options.mock ?? config.mock) {
    throw new Error(
      "DSers sourcing metadata requires the live Shopify catalog; "
        + "the existing mock catalog is unchanged",
    );
  }
  const graphql = options.graphql ?? shopifyGraphQL;
  let inspected = await graphql<InspectData>(INSPECT, {
    id: input.productId,
  });
  if (!inspected.product || inspected.product.id !== input.productId) {
    throw new Error(`Shopify product ${input.productId} was not found`);
  }
  assertAdminOnly(inspected.metafieldDefinitions.nodes);
  const bound = bindVariants(input, inspected.product);
  const tags = [
    ...new Set([...inspected.product.tags, ...input.tags, ...REQUIRED_TAGS]),
  ];
  const updated = await graphql<UpdateData>(UPDATE_PRODUCT, {
    product: {
      id: input.productId,
      vendor: AUTONOMOUS_VENDOR,
      status: "ACTIVE",
      tags,
    },
  });
  if (
    updated.productUpdate.userErrors.length ||
    !updated.productUpdate.product ||
    updated.productUpdate.product.id !== input.productId
  ) {
    throw new Error(
      `productUpdate failed: ${JSON.stringify(
        updated.productUpdate.userErrors,
      )}`,
    );
  }

  const expected = metafields(input, bound);
  for (const batch of chunks(expected, 25)) {
    const result = await graphql<MetafieldsSetData>(METAFIELDS_SET, {
      metafields: batch,
    });
    if (result.metafieldsSet.userErrors.length) {
      throw new Error(
        `metafieldsSet failed: ${JSON.stringify(
          result.metafieldsSet.userErrors,
        )}`,
      );
    }
  }

  inspected = await graphql<InspectData>(INSPECT, { id: input.productId });
  if (
    !inspected.product ||
    inspected.product.vendor !== AUTONOMOUS_VENDOR ||
    inspected.product.status !== "ACTIVE" ||
    !REQUIRED_TAGS.every((tag) => inspected.product!.tags.includes(tag))
  ) {
    throw new Error("autonomous sourcing provenance readback failed");
  }
  const actual = new Map(
    inspected.product.variants.nodes.flatMap((variant) =>
      (variant.metafields?.nodes ?? []).map((field) => [
        `${variant.id}:${field.key}`,
        field.value,
      ]),
    ),
  );
  for (const field of expected) {
    if (actual.get(`${field.ownerId}:${field.key}`) !== field.value) {
      throw new Error(
        `supplier metadata readback failed for ${field.ownerId}:${field.key}`,
      );
    }
  }
  const product = projectProduct(inspected.product);
  rememberRecentlySourcedProduct(product);
  return {
    product,
    provenance: {
      vendor: AUTONOMOUS_VENDOR,
      tags: REQUIRED_TAGS,
      productId: input.productId,
      importItemId: input.importItemId,
      matching: "Shopify product GID + exact variant SKU (never title)",
    },
  };
}
