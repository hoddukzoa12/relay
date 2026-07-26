import demoCatalog from "./demo-catalog.json" with { type: "json" };
import type { CatalogProduct } from "@arb/shared";
import { config } from "./config.js";
import { shopifyGraphQL } from "./shopify.js";

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
          }
        }
      }
    }
  }
`;

let liveCatalogCache: CatalogProduct[] = [];

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
  }));
}

export function catalogProductsFromShopify(
  data: ShopifyCatalogData,
): CatalogProduct[] {
  return data.products.nodes.flatMap((product) => {
    if (product.status !== "ACTIVE") return [];
    const variant = [...product.variants.nodes].sort(
      (a, b) =>
        Number((b.inventoryQuantity ?? 0) > 0) -
          Number((a.inventoryQuantity ?? 0) > 0) ||
        (a.sku ?? "").localeCompare(b.sku ?? ""),
    )[0];
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
      },
    ];
  });
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

  try {
    const products = await fetchLiveCatalog();
    liveCatalogCache = products;
    return rankAndLimit(products, query, limit);
  } catch (error) {
    if (!liveCatalogCache.length) throw error;
    console.warn(`[commerce] Shopify catalog unavailable; using cached variants: ${String(error)}`);
    return rankAndLimit(liveCatalogCache, query, limit);
  }
}
