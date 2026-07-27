export const SEED_VENDOR = "Relay";

export function activeSupplierProducts(products) {
  return products.filter(
    (product) =>
      product.status === "ACTIVE" && product.vendor !== SEED_VENDOR,
  );
}

/**
 * Seed updates may only target products explicitly owned by Relay. Matching a
 * title or SKU on another vendor is never sufficient authorization to mutate
 * a supplier product.
 */
export function relaySeedProductsBySku(products) {
  const productsBySku = new Map();
  for (const product of products) {
    if (product.vendor !== SEED_VENDOR) continue;
    for (const variant of product.variants.nodes) {
      if (variant.sku) productsBySku.set(variant.sku, { product, variant });
    }
  }
  return productsBySku;
}
