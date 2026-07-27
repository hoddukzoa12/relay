#!/usr/bin/env node

import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { ShopifyAdminClient } from "../services/commerce/src/shopify-client.ts";
import {
  SUPPLIER_METAFIELD_KEYS,
  supplierMetafields,
  validateLiveSupplierProducts,
  validateSupplierCostSnapshot,
} from "./dsers-supplier-cost-policy.mjs";

const here = dirname(fileURLToPath(import.meta.url));
const repoRoot = resolve(here, "..");
const apply = process.argv.includes("--apply");

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
const snapshot = validateSupplierCostSnapshot(
  JSON.parse(
    readFileSync(resolve(here, "dsers-supplier-costs.json"), "utf8"),
  ),
);

const domain = process.env.SHOPIFY_STORE_DOMAIN;
const adminAccessToken = process.env.SHOPIFY_ADMIN_ACCESS_TOKEN;
const clientId = process.env.SHOPIFY_CLIENT_ID;
const clientSecret = process.env.SHOPIFY_CLIENT_SECRET;
const apiVersion = process.env.SHOPIFY_API_VERSION ?? "2025-01";
if (!domain || (!adminAccessToken && !(clientId && clientSecret))) {
  throw new Error(
    "Shopify Admin credentials are required to inspect or sync supplier costs",
  );
}

const shopifyAdmin = new ShopifyAdminClient({
  domain,
  apiVersion,
  adminAccessToken,
  clientId,
  clientSecret,
});
const shopify = (query, variables = {}) =>
  shopifyAdmin.graphql(query, variables);

const INSPECT = /* GraphQL */ `
  query InspectSupplierCostTargets($variantIds: [ID!]!) {
    nodes(ids: $variantIds) {
      ... on ProductVariant {
        id
        sku
        product {
          id
          vendor
          status
        }
        metafields(first: 10, namespace: "relay") {
          nodes {
            namespace
            key
            type
            value
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

const METAFIELDS_SET = /* GraphQL */ `
  mutation SetSupplierCostMetafields($metafields: [MetafieldsSetInput!]!) {
    metafieldsSet(metafields: $metafields) {
      metafields { ownerType namespace key type value }
      userErrors { field message code }
    }
  }
`;

function chunks(values, size) {
  const result = [];
  for (let index = 0; index < values.length; index += size) {
    result.push(values.slice(index, index + size));
  }
  return result;
}

function assertAdminOnlyDefinitions(definitions) {
  const protectedKeys = new Set(SUPPLIER_METAFIELD_KEYS);
  const exposed = definitions.filter(
    (definition) =>
      protectedKeys.has(definition.key) &&
      definition.access?.storefront !== "NONE",
  );
  if (exposed.length) {
    throw new Error(
      `Refusing to write storefront-readable supplier costs: ${exposed
        .map((definition) => definition.key)
        .join(", ")}`,
    );
  }
}

const variantIds = snapshot.products.flatMap((product) =>
  product.variants.map((variant) => variant.shopifyVariantId),
);

function productsFromVariantNodes(nodes) {
  const products = new Map();
  for (const variant of nodes.filter(Boolean)) {
    const product = variant.product;
    const existing = products.get(product.id) ?? {
      id: product.id,
      vendor: product.vendor,
      status: product.status,
      variants: { nodes: [] },
    };
    existing.variants.nodes.push({
      id: variant.id,
      sku: variant.sku,
      metafields: variant.metafields,
    });
    products.set(product.id, existing);
  }
  return [...products.values()];
}

const inspected = await shopify(INSPECT, { variantIds });
const inspectedProducts = productsFromVariantNodes(inspected.nodes);
validateLiveSupplierProducts(snapshot, inspectedProducts);
assertAdminOnlyDefinitions(inspected.metafieldDefinitions.nodes);

const inputs = supplierMetafields(snapshot);
const summary = {
  mode: apply ? "apply" : "dry-run",
  source: snapshot.source,
  capturedAt: snapshot.capturedAt,
  products: snapshot.products.length,
  variants: snapshot.products.reduce(
    (total, product) => total + product.variants.length,
    0,
  ),
  metafields: inputs.length,
  matching: "Shopify product GID + variant GID + vendor + SKU (never title)",
  storefrontAccess: "NONE / unstructured Admin-only",
};
console.log(JSON.stringify(summary, null, 2));

if (!apply) {
  console.log("Dry run only. Re-run with --apply to persist Admin-only metafields.");
  process.exit(0);
}

for (const batch of chunks(inputs, 25)) {
  const data = await shopify(METAFIELDS_SET, { metafields: batch });
  const errors = data.metafieldsSet.userErrors;
  if (errors.length) {
    throw new Error(`metafieldsSet failed: ${JSON.stringify(errors)}`);
  }
}

const readback = await shopify(INSPECT, { variantIds });
const readbackProducts = productsFromVariantNodes(readback.nodes);
validateLiveSupplierProducts(snapshot, readbackProducts);
assertAdminOnlyDefinitions(readback.metafieldDefinitions.nodes);
const expected = new Map(
  inputs.map((input) => [
    `${input.ownerId}:${input.namespace}:${input.key}`,
    input.value,
  ]),
);
for (const product of readbackProducts) {
  for (const variant of product.variants.nodes) {
    for (const metafield of variant.metafields.nodes) {
      const key = `${variant.id}:${metafield.namespace}:${metafield.key}`;
      if (expected.has(key)) {
        if (expected.get(key) !== metafield.value) {
          throw new Error(`Supplier-cost readback mismatch for ${key}`);
        }
        expected.delete(key);
      }
    }
  }
}
if (expected.size) {
  throw new Error(
    `Supplier-cost readback missing ${expected.size} expected metafields`,
  );
}
console.log("Supplier-cost snapshot persisted and verified through Admin API.");
