const DECIMAL = /^\d+(\.\d{1,6})?$/;
const SHOPIFY_PRODUCT_ID = /^gid:\/\/shopify\/Product\/\d+$/;
const SHOPIFY_VARIANT_ID = /^gid:\/\/shopify\/ProductVariant\/\d+$/;

export const SUPPLIER_METAFIELD_NAMESPACE = "relay";
export const SUPPLIER_METAFIELD_KEYS = [
  "supplier_cost",
  "supplier_cost_currency",
  "supplier_cost_source",
  "supplier_cost_captured_at",
  "supplier_cost_ship_to",
  "supplier_url",
  "supplier_product_id",
  "dsers_product_id",
];

export function validateSupplierCostSnapshot(snapshot) {
  if (snapshot.source !== "dsers_mcp_snapshot") {
    throw new Error("supplier snapshot source must be dsers_mcp_snapshot");
  }
  if (snapshot.currency !== "USD" || snapshot.shipTo !== "US") {
    throw new Error("supplier snapshot must use the verified USD / ship-to US basis");
  }
  if (!/^\d{4}-\d{2}-\d{2}$/.test(snapshot.capturedAt)) {
    throw new Error("supplier snapshot capturedAt must be YYYY-MM-DD");
  }
  if (!snapshot.vendor || !Array.isArray(snapshot.products)) {
    throw new Error("supplier snapshot vendor and products are required");
  }

  const productIds = new Set();
  const variantIds = new Set();
  for (const product of snapshot.products) {
    if (!SHOPIFY_PRODUCT_ID.test(product.shopifyProductId)) {
      throw new Error(`invalid Shopify product id ${product.shopifyProductId}`);
    }
    if (productIds.has(product.shopifyProductId)) {
      throw new Error(`duplicate Shopify product id ${product.shopifyProductId}`);
    }
    productIds.add(product.shopifyProductId);
    if (!product.supplierUrl.includes(product.supplierProductId)) {
      throw new Error(
        `supplier URL does not contain ${product.supplierProductId}`,
      );
    }
    if (!Array.isArray(product.variants) || product.variants.length === 0) {
      throw new Error(`${product.shopifyProductId} has no variants`);
    }
    for (const variant of product.variants) {
      if (!SHOPIFY_VARIANT_ID.test(variant.shopifyVariantId)) {
        throw new Error(`invalid Shopify variant id ${variant.shopifyVariantId}`);
      }
      if (variantIds.has(variant.shopifyVariantId)) {
        throw new Error(`duplicate Shopify variant id ${variant.shopifyVariantId}`);
      }
      variantIds.add(variant.shopifyVariantId);
      if (!variant.sku || !DECIMAL.test(variant.cost) || Number(variant.cost) <= 0) {
        throw new Error(
          `${variant.shopifyVariantId} has an invalid SKU or supplier cost`,
        );
      }
    }
  }
  return snapshot;
}

/**
 * Bind by immutable Shopify product + variant IDs and verify vendor/SKU.
 * Titles are deliberately ignored, and duplicate SKUs across products remain
 * valid because the variant GID is the cost-record owner.
 */
export function validateLiveSupplierProducts(snapshot, liveProducts) {
  validateSupplierCostSnapshot(snapshot);
  const byProductId = new Map(liveProducts.map((product) => [product.id, product]));
  for (const expected of snapshot.products) {
    const live = byProductId.get(expected.shopifyProductId);
    if (!live) {
      throw new Error(`Shopify product ${expected.shopifyProductId} was not found`);
    }
    if (live.vendor !== snapshot.vendor || live.status !== "ACTIVE") {
      throw new Error(
        `${expected.shopifyProductId} must be ACTIVE vendor ${snapshot.vendor}`,
      );
    }
    const byVariantId = new Map(
      live.variants.nodes.map((variant) => [variant.id, variant]),
    );
    for (const variant of expected.variants) {
      const liveVariant = byVariantId.get(variant.shopifyVariantId);
      if (!liveVariant) {
        throw new Error(
          `Shopify variant ${variant.shopifyVariantId} was not found`,
        );
      }
      if (liveVariant.sku !== variant.sku) {
        throw new Error(
          `${variant.shopifyVariantId} SKU changed from ${variant.sku} to ${liveVariant.sku}`,
        );
      }
    }
  }
  return liveProducts;
}

export function supplierMetafields(snapshot) {
  validateSupplierCostSnapshot(snapshot);
  return snapshot.products.flatMap((product) =>
    product.variants.flatMap((variant) => {
      const common = {
        ownerId: variant.shopifyVariantId,
        namespace: SUPPLIER_METAFIELD_NAMESPACE,
      };
      return [
        {
          ...common,
          key: "supplier_cost",
          type: "number_decimal",
          value: variant.cost,
        },
        {
          ...common,
          key: "supplier_cost_currency",
          type: "single_line_text_field",
          value: snapshot.currency,
        },
        {
          ...common,
          key: "supplier_cost_source",
          type: "single_line_text_field",
          value: snapshot.source,
        },
        {
          ...common,
          key: "supplier_cost_captured_at",
          type: "date",
          value: snapshot.capturedAt,
        },
        {
          ...common,
          key: "supplier_cost_ship_to",
          type: "single_line_text_field",
          value: snapshot.shipTo,
        },
        {
          ...common,
          key: "supplier_url",
          type: "url",
          value: product.supplierUrl,
        },
        {
          ...common,
          key: "supplier_product_id",
          type: "single_line_text_field",
          value: product.supplierProductId,
        },
        {
          ...common,
          key: "dsers_product_id",
          type: "single_line_text_field",
          value: product.dsersProductId,
        },
      ];
    }),
  );
}
