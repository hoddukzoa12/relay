#!/usr/bin/env node

import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { ShopifyAdminClient } from "../services/commerce/src/shopify-client.ts";
import {
  SEED_VENDOR,
  activeSupplierProducts,
  relaySeedProductsBySku,
} from "./shopify-seed-policy.mjs";

const here = dirname(fileURLToPath(import.meta.url));
const repoRoot = resolve(here, "..");

function loadEnv(path) {
  for (const raw of readFileSync(path, "utf8").split(/\r?\n/)) {
    const line = raw.trim();
    if (!line || line.startsWith("#")) continue;
    const separator = line.indexOf("=");
    if (separator < 1) continue;
    const key = line.slice(0, separator).trim();
    let value = line.slice(separator + 1).trim();
    if (
      (value.startsWith('"') && value.endsWith('"')) ||
      (value.startsWith("'") && value.endsWith("'"))
    ) {
      value = value.slice(1, -1);
    }
    process.env[key] ??= value;
  }
}

loadEnv(resolve(repoRoot, ".env"));

const domain = process.env.SHOPIFY_STORE_DOMAIN;
const adminAccessToken = process.env.SHOPIFY_ADMIN_ACCESS_TOKEN;
const clientId = process.env.SHOPIFY_CLIENT_ID;
const clientSecret = process.env.SHOPIFY_CLIENT_SECRET;
const apiVersion = process.env.SHOPIFY_API_VERSION ?? "2025-01";
if (!domain || (!adminAccessToken && !(clientId && clientSecret))) {
  throw new Error(
    "SHOPIFY_STORE_DOMAIN and either SHOPIFY_ADMIN_ACCESS_TOKEN or both SHOPIFY_CLIENT_ID and SHOPIFY_CLIENT_SECRET are required in .env",
  );
}

const seedCatalog = JSON.parse(
  readFileSync(
    resolve(repoRoot, "services/commerce/src/demo-catalog.json"),
    "utf8",
  ),
);
const shopifyAdmin = new ShopifyAdminClient({
  domain,
  apiVersion,
  adminAccessToken,
  clientId,
  clientSecret,
});

async function shopify(query, variables = {}) {
  return shopifyAdmin.graphql(query, variables);
}

function requireMutation(result, name) {
  const errors = result.userErrors ?? [];
  if (errors.length) {
    throw new Error(`${name} failed: ${JSON.stringify(errors)}`);
  }
  return result;
}

const SETUP_QUERY = /* GraphQL */ `
  query CatalogSeedSetup {
    locations(first: 20) {
      nodes { id name isActive }
    }
    publications(first: 20) {
      nodes { id name }
    }
    products(first: 250, sortKey: TITLE) {
      nodes {
        id
        title
        vendor
        status
        variants(first: 100) {
          nodes {
            id
            sku
            price
            inventoryItem { id tracked }
          }
        }
      }
    }
  }
`;

const PRODUCT_CREATE = /* GraphQL */ `
  mutation CreateSeedProduct($product: ProductCreateInput!) {
    productCreate(product: $product) {
      product {
        id
        title
        variants(first: 1) {
          nodes { id inventoryItem { id tracked } }
        }
      }
      userErrors { field message }
    }
  }
`;

const PRODUCT_UPDATE = /* GraphQL */ `
  mutation UpdateSeedProduct($product: ProductUpdateInput!) {
    productUpdate(product: $product) {
      product { id title status }
      userErrors { field message }
    }
  }
`;

const VARIANT_UPDATE = /* GraphQL */ `
  mutation UpdateSeedVariant(
    $productId: ID!
    $variants: [ProductVariantsBulkInput!]!
  ) {
    productVariantsBulkUpdate(
      productId: $productId
      variants: $variants
    ) {
      productVariants {
        id
        sku
        price
        inventoryItem { id tracked }
      }
      userErrors { field message }
    }
  }
`;

const INVENTORY_SET = /* GraphQL */ `
  mutation SetSeedInventory($input: InventorySetQuantitiesInput!) {
    inventorySetQuantities(input: $input) {
      inventoryAdjustmentGroup {
        createdAt
        reason
        changes { name delta quantityAfterChange }
      }
      userErrors { field message code }
    }
  }
`;

const PUBLISH = /* GraphQL */ `
  mutation PublishSeedProduct($id: ID!, $input: [PublicationInput!]!) {
    publishablePublish(id: $id, input: $input) {
      userErrors { field message }
    }
  }
`;

const READBACK_QUERY = /* GraphQL */ `
  query CatalogSeedReadback($publicationId: ID!) {
    products(first: 250, sortKey: TITLE) {
      nodes {
        id
        title
        vendor
        status
        totalInventory
        onlineStoreUrl
        publishedOnPublication(publicationId: $publicationId)
        variants(first: 100) {
          nodes { id sku price inventoryQuantity }
        }
      }
    }
  }
`;

const setup = await shopify(SETUP_QUERY);
const supplierProducts = activeSupplierProducts(setup.products.nodes);
if (supplierProducts.length) {
  console.log(
    JSON.stringify(
      {
        skipped: true,
        reason:
          "active supplier catalog present; Relay demo seeding is fallback-only",
        supplierProducts: supplierProducts.map((product) => ({
          id: product.id,
          vendor: product.vendor,
          title: product.title,
        })),
      },
      null,
      2,
    ),
  );
  process.exit(0);
}

const location = setup.locations.nodes.find((candidate) => candidate.isActive);
const onlineStore = setup.publications.nodes.find(
  (publication) => publication.name === "Online Store",
);
if (!location) throw new Error("No active Shopify inventory location found");
if (!onlineStore) throw new Error("Online Store publication not found");

const productsBySku = relaySeedProductsBySku(setup.products.nodes);

const inventoryQuantities = [];
const publishableProductIds = new Set();

for (const item of seedCatalog) {
  const match = productsBySku.get(item.sku);
  let product;
  let variant;

  if (!match) {
    const data = await shopify(PRODUCT_CREATE, {
      product: {
        title: item.title,
        descriptionHtml: `<p>${item.description}</p>`,
        status: "ACTIVE",
        vendor: SEED_VENDOR,
        productType: item.productType,
        tags: ["relay-demo", "relay-seed", ...item.tags],
      },
    });
    const created = requireMutation(data.productCreate, "productCreate");
    product = created.product;
    variant = product?.variants.nodes[0];
    if (!product || !variant) {
      throw new Error(`productCreate returned no default variant for ${item.sku}`);
    }
    console.log(`[seed] created ${item.sku} (${product.id})`);
  } else {
    ({ product, variant } = match);
    const data = await shopify(PRODUCT_UPDATE, {
      product: {
        id: product.id,
        title: item.title,
        descriptionHtml: `<p>${item.description}</p>`,
        status: "ACTIVE",
        vendor: SEED_VENDOR,
        productType: item.productType,
        tags: ["relay-demo", "relay-seed", ...item.tags],
      },
    });
    requireMutation(data.productUpdate, "productUpdate");
    console.log(`[seed] reusing ${item.sku} (${product.id})`);
  }

  const variantData = await shopify(VARIANT_UPDATE, {
    productId: product.id,
    variants: [
      {
        id: variant.id,
        price: item.price,
        inventoryItem: {
          sku: item.sku,
          tracked: true,
          requiresShipping: true,
        },
      },
    ],
  });
  const updated = requireMutation(
    variantData.productVariantsBulkUpdate,
    "productVariantsBulkUpdate",
  ).productVariants[0];
  if (!updated?.inventoryItem?.id) {
    throw new Error(`No inventory item returned for ${item.sku}`);
  }
  inventoryQuantities.push({
    inventoryItemId: updated.inventoryItem.id,
    locationId: location.id,
    quantity: item.inventory,
  });
  publishableProductIds.add(product.id);
}

const inventoryData = await shopify(INVENTORY_SET, {
  input: {
    name: "available",
    reason: "correction",
    ignoreCompareQuantity: true,
    referenceDocumentUri: "relay://catalog-seed/fallback",
    quantities: inventoryQuantities,
  },
});
requireMutation(inventoryData.inventorySetQuantities, "inventorySetQuantities");

for (const productId of publishableProductIds) {
  const data = await shopify(PUBLISH, {
    id: productId,
    input: [{ publicationId: onlineStore.id }],
  });
  requireMutation(data.publishablePublish, "publishablePublish");
}

const readback = await shopify(READBACK_QUERY, {
  publicationId: onlineStore.id,
});
const seedSkus = new Set(seedCatalog.map((item) => item.sku));
const catalog = readback.products.nodes.filter(
  (product) =>
    product.vendor === SEED_VENDOR &&
    product.variants.nodes.some((variant) => seedSkus.has(variant.sku)),
);
const summarizedCatalog = catalog.map((product) => ({
  id: product.id,
  title: product.title,
  status: product.status,
  totalInventory: product.totalInventory,
  publishedOnOnlineStore: product.publishedOnPublication,
  variants:
    product.variants.nodes.length <= 10
      ? product.variants.nodes
      : {
          count: product.variants.nodes.length,
          sample: product.variants.nodes.slice(0, 3),
        },
}));
console.log(
  JSON.stringify(
    {
      location: { id: location.id, name: location.name },
      publication: { id: onlineStore.id, name: onlineStore.name },
      activeInStockPublishedProducts: catalog.filter(
        (product) =>
          product.status === "ACTIVE" &&
          product.totalInventory > 0 &&
          product.publishedOnPublication,
      ).length,
      products: summarizedCatalog,
    },
    null,
    2,
  ),
);
