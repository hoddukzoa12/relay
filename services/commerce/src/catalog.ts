import demoCatalog from "./demo-catalog.json" with { type: "json" };
import type { CatalogProduct, SupplierCostSnapshot } from "@arb/shared";
import { config } from "./config.js";
import { reconcileCompletedCatalogLifecycle } from "./catalog-lifecycle.js";
import { shopifyGraphQL } from "./shopify-admin.js";
import {
  requireShopifyUsdcParityCurrency,
} from "./shopify.js";

export interface ShopifyCatalogData {
  products: {
    nodes: {
      id: string;
      title: string;
      description: string;
      status: string;
      tags: string[];
      variants: {
        nodes: {
          id: string;
          sku: string | null;
          price: string;
          inventoryQuantity: number | null;
          supplierCost?: { value: string } | null;
          supplierCostCurrency?: { value: string } | null;
          supplierCostSource?: { value: string } | null;
          supplierCostCapturedAt?: { value: string } | null;
          supplierCostShipTo?: { value: string } | null;
          supplierUrl?: { value: string } | null;
        }[];
      };
    }[];
  };
}

const CATALOG_PRODUCTS = /* GraphQL */ `
  query CatalogProducts {
    products(first: 100, query: "status:active", sortKey: TITLE) {
      nodes {
        id
        title
        description
        status
        tags
        variants(first: 100) {
          nodes {
            id
            sku
            price
            inventoryQuantity
            supplierCost: metafield(namespace: "relay", key: "supplier_cost") {
              value
            }
            supplierCostCurrency: metafield(
              namespace: "relay"
              key: "supplier_cost_currency"
            ) {
              value
            }
            supplierCostSource: metafield(
              namespace: "relay"
              key: "supplier_cost_source"
            ) {
              value
            }
            supplierCostCapturedAt: metafield(
              namespace: "relay"
              key: "supplier_cost_captured_at"
            ) {
              value
            }
            supplierCostShipTo: metafield(
              namespace: "relay"
              key: "supplier_cost_ship_to"
            ) {
              value
            }
            supplierUrl: metafield(namespace: "relay", key: "supplier_url") {
              value
            }
          }
        }
      }
    }
  }
`;

let liveCatalogCache: CatalogProduct[] = [];
const RECENT_SOURCING_TTL_MS = 60_000;
const recentlySourcedProducts = new Map<
  string,
  { product: CatalogProduct; expiresAt: number }
>();

/**
 * Bridge Shopify's post-mutation indexing window so a just-sourced product is
 * immediately visible through the same catalog endpoint used by every agent.
 */
export function rememberRecentlySourcedProduct(
  product: CatalogProduct,
  now = Date.now(),
): void {
  recentlySourcedProducts.set(product.productId, {
    product,
    expiresAt: now + RECENT_SOURCING_TTL_MS,
  });
}

export function forgetCatalogProduct(productId: string): void {
  recentlySourcedProducts.delete(productId);
  liveCatalogCache = liveCatalogCache.filter(
    (product) => product.productId !== productId,
  );
}

export function mergeRecentlySourcedProducts(
  products: CatalogProduct[],
  now = Date.now(),
): CatalogProduct[] {
  const merged = new Map(
    products.map((product) => [product.productId, product]),
  );
  for (const [productId, pending] of recentlySourcedProducts) {
    if (pending.expiresAt <= now) {
      recentlySourcedProducts.delete(productId);
    } else if (merged.has(productId)) {
      // The authoritative catalog query now sees the write, so the bridge is
      // no longer needed.
      recentlySourcedProducts.delete(productId);
    } else {
      merged.set(productId, pending.product);
    }
  }
  return [...merged.values()];
}

function normalize(value: string): string[] {
  return value
    .toLowerCase()
    .split(/[^a-z0-9]+/)
    .filter((token) => token.length > 1);
}

function relevance(product: CatalogProduct, query: string): number {
  const tokens = normalize(query);
  if (!tokens.length) return 0;

  const title = product.title.toLowerCase();
  const sku = (product.sku ?? "").toLowerCase();
  const searchable = `${title} ${product.description.toLowerCase()} ${product.tags.join(" ").toLowerCase()}`;
  return tokens.reduce((score, token) => {
    if (title.includes(token)) return score + 4;
    if (sku.includes(token)) return score + 3;
    if (searchable.includes(token)) return score + 1;
    return score;
  }, 0);
}

export function rankAndLimit(
  products: CatalogProduct[],
  query: string,
  limit: number,
): CatalogProduct[] {
  return [...products]
    .sort((a, b) => {
      const relevanceDelta = relevance(b, query) - relevance(a, query);
      if (relevanceDelta) return relevanceDelta;
      return (
        a.title.localeCompare(b.title) ||
        (a.sku ?? "").localeCompare(b.sku ?? "")
      );
    })
    .slice(0, limit);
}

function mockCatalog(): CatalogProduct[] {
  return demoCatalog.map((product) => ({
    productId: `mock-product:${product.sku}`,
    variantId: `mock-variant:${product.sku}`,
    sku: product.sku,
    title: product.title,
    description: product.description,
    price: product.price,
    inventoryQuantity: product.inventory,
    status: "ACTIVE",
    tags: product.tags,
    supplierCost: {
      amount: product.price,
      currency: "USD",
      source: "relay_demo_catalog",
      capturedAt: "1970-01-01",
      shipTo: "US",
      supplierUrl: null,
    },
  }));
}

export function supplierCostFromShopifyVariant(
  variant: ShopifyCatalogVariant,
): SupplierCostSnapshot | null {
  const amount = variant.supplierCost?.value ?? "";
  const currency = variant.supplierCostCurrency?.value;
  const source = variant.supplierCostSource?.value;
  const capturedAt = variant.supplierCostCapturedAt?.value ?? "";
  const shipTo = variant.supplierCostShipTo?.value ?? "";
  const supplierUrl = variant.supplierUrl?.value ?? "";
  if (
    !/^\d+(\.\d{1,6})?$/.test(amount) ||
    Number(amount) <= 0 ||
    currency !== "USD" ||
    source !== "dsers_mcp_snapshot" ||
    !capturedAt ||
    !shipTo ||
    !supplierUrl
  ) {
    return null;
  }
  try {
    new URL(supplierUrl);
  } catch {
    return null;
  }
  return {
    amount,
    currency,
    source,
    capturedAt,
    shipTo,
    supplierUrl,
  };
}

export function catalogProductsFromShopify(
  data: ShopifyCatalogData,
): CatalogProduct[] {
  return data.products.nodes.flatMap((product) => {
    if (product.status !== "ACTIVE") return [];
    const variant = selectCatalogVariant(product.variants.nodes);
    if (!variant) return [];
    return [
      {
        productId: product.id,
        variantId: variant.id,
        sku: variant.sku ?? "",
        title: product.title,
        description: product.description,
        price: variant.price,
        inventoryQuantity: variant.inventoryQuantity ?? 0,
        status: "ACTIVE" as const,
        tags: product.tags,
        supplierCost: supplierCostFromShopifyVariant(variant),
      },
    ];
  });
}

export type ShopifyCatalogVariant =
  ShopifyCatalogData["products"]["nodes"][number]["variants"]["nodes"][number];

/**
 * Choose the cheapest actually sellable variant, then prefer deeper stock for
 * equal prices. A real SKU is required because it is bound into the CartMandate
 * and copied into the Shopify order evidence.
 */
export function selectCatalogVariant(
  variants: ShopifyCatalogVariant[],
): ShopifyCatalogVariant | undefined {
  return [...variants]
    .filter((variant) => {
      const price = Number(variant.price);
      return (
        variant.inventoryQuantity !== null &&
        variant.inventoryQuantity > 0 &&
        variant.sku !== null &&
        variant.sku.trim().length > 0 &&
        Number.isFinite(price) &&
        price > 0
      );
    })
    .sort(
      (a, b) =>
        Number(a.price) - Number(b.price) ||
        (b.inventoryQuantity ?? 0) - (a.inventoryQuantity ?? 0) ||
        (a.sku ?? "").localeCompare(b.sku ?? "") ||
        a.id.localeCompare(b.id),
    )[0];
}

async function fetchLiveCatalog(): Promise<CatalogProduct[]> {
  const data = await shopifyGraphQL<ShopifyCatalogData>(CATALOG_PRODUCTS, {});
  return catalogProductsFromShopify(data);
}

/** Return real Shopify variants, with a last-known-good cache for API outages. */
export async function listProducts(
  query: string,
  limit: number,
): Promise<CatalogProduct[]> {
  if (config.mock) return rankAndLimit(mockCatalog(), query, limit);

  // Currency validation is intentionally outside the catalog-cache fallback:
  // stale products must never bypass a failed or mismatched currency check.
  await requireShopifyUsdcParityCurrency();

  try {
    try {
      const lifecycle = await reconcileCompletedCatalogLifecycle();
      for (const result of lifecycle) {
        if (
          result.action === "retired" ||
          result.action === "already_unlisted"
        ) {
          forgetCatalogProduct(result.productId);
        }
      }
    } catch (error) {
      // Do not let an order-history read outage replace the catalog's existing
      // last-known-good behavior. A later search or order lookup retries.
      console.warn(
        `[commerce] catalog lifecycle reconciliation deferred: ${String(error)}`,
      );
    }
    const products = mergeRecentlySourcedProducts(await fetchLiveCatalog());
    liveCatalogCache = products;
    return rankAndLimit(products, query, limit);
  } catch (error) {
    if (!liveCatalogCache.length) throw error;
    console.warn(`[commerce] Shopify catalog unavailable; using cached variants: ${String(error)}`);
    return rankAndLimit(liveCatalogCache, query, limit);
  }
}
