import assert from "node:assert/strict";
import test from "node:test";
import {
  catalogProductsFromShopify,
  listProducts,
  rankAndLimit,
  selectCatalogVariant,
} from "./catalog.js";
import {
  ShopifyCurrencyMismatchError,
  ShopifyStoreCurrency,
} from "./shopify-currency.js";
import {
  ShopifyAdminClient,
  ShopifyTokenProvider,
} from "./shopify-client.js";
import {
  buildShopifyOrderInput,
  createPaidOrder,
  fulfillOrder,
  getOrderStatus,
  isTemporaryOrderMutationError,
  listOrdersByWallet,
  marginEvidence,
  markOrderRefunded,
  disabledSupplierOrder,
  orderTag,
  supplierOrderForInput,
  trackOrder,
  trackingInfoFromFulfillments,
  type OrderInput,
} from "./shopify.js";

const input = (orderRef: string): OrderInput => ({
  orderRef,
  productId: "product",
  title: "Idempotency test",
  sku: "RELAY-IDEMPOTENCY",
  amount: "1.00",
  buyerAddress: "buyer",
  shipTo: "destination",
  paymentReference: `reference-${orderRef}`,
  txSignature: "signature",
  explorer: "https://explorer.test/signature",
  supplierCost: {
    amount: "1.00",
    currency: "USD",
    source: "dsers_mcp_snapshot",
    capturedAt: "2026-07-27",
    shipTo: "US",
    supplierUrl: "https://supplier.test/item/1",
  },
});

const realFormatShippingAddress = {
  name: "Grace Hopper",
  address1: "123 Main St",
  address2: null,
  city: "Arlington",
  province: "VA",
  country: "US",
  zip: "22201",
  phone: null,
} as const;

test("mock order creation returns one result for sequential replays", async () => {
  const order = input(`order-${crypto.randomUUID()}`);
  const first = await createPaidOrder(order);
  const second = await createPaidOrder(order);

  assert.deepEqual(second, first);
});

test("mock order creation coalesces concurrent replays", async () => {
  const order = input(`order-${crypto.randomUUID()}`);
  const [first, second] = await Promise.all([
    createPaidOrder(order),
    createPaidOrder(order),
  ]);

  assert.deepEqual(second, first);
});

test("wallet order lookup returns only the signed-in wallet's orders", async () => {
  const wallet = `wallet-${crypto.randomUUID()}`;
  const ownOrder = {
    ...input(`order-${crypto.randomUUID()}`),
    buyerAddress: wallet,
  };
  const otherOrder = {
    ...input(`order-${crypto.randomUUID()}`),
    buyerAddress: `wallet-${crypto.randomUUID()}`,
  };
  await createPaidOrder(ownOrder);
  await createPaidOrder(otherOrder);

  const orders = await listOrdersByWallet(wallet);

  assert.equal(orders.length, 1);
  assert.equal(orders[0]?.orderRef, ownOrder.orderRef);
  assert.equal(orders[0]?.buyerWallet, wallet);
});

test("mock order lifecycle exposes supplier state, refuses fake fulfillment, and refunds", async () => {
  const order = input(`order-${crypto.randomUUID()}`);
  const created = await createPaidOrder(order);

  const byRef = await getOrderStatus(order.orderRef);
  const byName = await getOrderStatus(created.name);
  assert.deepEqual(byName, byRef);
  assert.equal(byRef.financialStatus, "PAID");
  assert.equal(byRef.fulfillmentStatus, "UNFULFILLED");
  assert.equal(byRef.lineItems[0]?.sku, "RELAY-IDEMPOTENCY");
  assert.equal(byRef.payment.reference, order.paymentReference);
  assert.equal(byRef.refund.status, "not_refunded");
  assert.deepEqual(byRef.supplierOrder, disabledSupplierOrder());
  assert.equal(byRef.tracking, null);

  await assert.rejects(
    fulfillOrder(order.orderRef),
    /cannot be fulfilled.*supplier fulfillment is disabled/i,
  );
  await assert.rejects(
    trackOrder(order.orderRef),
    /no real tracking number.*supplier fulfillment is disabled/i,
  );

  const refunded = await markOrderRefunded(
    order.orderRef,
    "refund-reference",
    "refund-signature",
    "https://explorer.test/refund-signature",
  );
  const refundReplay = await markOrderRefunded(
    order.orderRef,
    "refund-reference",
    "refund-signature",
    "https://explorer.test/refund-signature",
  );
  assert.equal(refunded.financialStatus, "REFUNDED");
  assert.equal(refunded.refund.status, "refunded");
  assert.deepEqual(refundReplay, refunded);
  await assert.rejects(
    markOrderRefunded(
      order.orderRef,
      "different-refund-reference",
      "different-refund-signature",
      "https://explorer.test/different-refund-signature",
    ),
    /already refunded by refund-signature/,
  );
});

test("margin evidence uses exact DSers cost without floating-point drift", () => {
  const margin = marginEvidence("4.54", {
    amount: "3.96",
    currency: "USD",
    source: "dsers_mcp_snapshot",
    capturedAt: "2026-07-27",
    shipTo: "US",
    supplierUrl: "https://www.aliexpress.com/item/1005007183896560.html",
  });

  assert.equal(margin?.projectedGrossMarginAmount, "0.58");
  assert.equal(margin?.projectedGrossMarginPct, "12.78");
  assert.equal(
    margin?.basis,
    "snapshot_usd_usdc_parity_excludes_shipping_tax",
  );
});

test("mock catalog is deterministic and query-ranked", async () => {
  const products = await listProducts("wireless earbuds", 3);

  assert.equal(products.length, 3);
  assert.match(products[0]?.title ?? "", /Earbuds/);
  assert.ok(products.every((product) => product.inventoryQuantity > 0));
  assert.ok(products.every((product) => product.sku.startsWith("RELAY-")));
});

test("live catalog selects the cheapest in-stock variant with a real SKU", () => {
  const products = catalogProductsFromShopify({
    products: {
      nodes: [
        {
          id: "gid://shopify/Product/without-sku",
          title: "Wireless Accessory",
          description: "An active product without a merchant SKU.",
          status: "ACTIVE",
          tags: ["wireless"],
          variants: {
            nodes: [
              {
                id: "gid://shopify/ProductVariant/without-sku",
                sku: null,
                price: "1.00",
                inventoryQuantity: 50,
              },
              {
                id: "gid://shopify/ProductVariant/alternate-sku",
                sku: "RELAY-ALTERNATE",
                price: "3.00",
                inventoryQuantity: 5,
              },
              {
                id: "gid://shopify/ProductVariant/out-of-stock",
                sku: "RELAY-CHEAP",
                price: "2.00",
                inventoryQuantity: 0,
              },
              {
                id: "gid://shopify/ProductVariant/deeper-stock",
                sku: "RELAY-DEEP",
                price: "3.00",
                inventoryQuantity: 20,
              },
            ],
          },
        },
        {
          id: "gid://shopify/Product/with-sku",
          title: "Wireless Accessory",
          description: "A second product for deterministic sorting.",
          status: "ACTIVE",
          tags: ["wired"],
          variants: {
            nodes: [
              {
                id: "gid://shopify/ProductVariant/with-sku",
                sku: "RELAY-WIRED",
                price: "2.00",
                inventoryQuantity: 5,
              },
            ],
          },
        },
      ],
    },
  });

  const ranked = rankAndLimit(products, "wireless", 10);
  assert.equal(ranked.length, 2);
  assert.equal(ranked[0]?.variantId, "gid://shopify/ProductVariant/deeper-stock");
  assert.equal(ranked[0]?.sku, "RELAY-DEEP");
  assert.equal(ranked[0]?.supplierCost, null);
});

test("live catalog reads a private supplier-cost snapshot from the selected variant", () => {
  const products = catalogProductsFromShopify({
    products: {
      nodes: [
        {
          id: "gid://shopify/Product/1",
          title: "TWS F9-5",
          description: "Supplier item",
          status: "ACTIVE",
          tags: [],
          variants: {
            nodes: [
              {
                id: "gid://shopify/ProductVariant/1",
                sku: "14:193#black",
                price: "3.95",
                inventoryQuantity: 1225,
                supplierCost: { value: "3.96" },
                supplierCostCurrency: { value: "USD" },
                supplierCostSource: { value: "dsers_mcp_snapshot" },
                supplierCostCapturedAt: { value: "2026-07-27" },
                supplierCostShipTo: { value: "US" },
                supplierUrl: {
                  value:
                    "https://www.aliexpress.com/item/1005007183896560.html",
                },
              },
            ],
          },
        },
      ],
    },
  });

  assert.deepEqual(products[0]?.supplierCost, {
    amount: "3.96",
    currency: "USD",
    source: "dsers_mcp_snapshot",
    capturedAt: "2026-07-27",
    shipTo: "US",
    supplierUrl: "https://www.aliexpress.com/item/1005007183896560.html",
  });
});

test("variant selection rejects unavailable and malformed choices", () => {
  assert.equal(
    selectCatalogVariant([
      {
        id: "gid://shopify/ProductVariant/out",
        sku: "OUT",
        price: "1.00",
        inventoryQuantity: 0,
      },
      {
        id: "gid://shopify/ProductVariant/bad-price",
        sku: "BAD",
        price: "not-money",
        inventoryQuantity: 10,
      },
      {
        id: "gid://shopify/ProductVariant/no-sku",
        sku: null,
        price: "1.00",
        inventoryQuantity: 10,
      },
    ]),
    undefined,
  );
});

test("Shopify currency is cached but mismatches fail closed", async () => {
  let reads = 0;
  const resolver = new ShopifyStoreCurrency(
    async <T>() => {
      reads += 1;
      return { shop: { currencyCode: reads === 1 ? "USD" : "KRW" } } as T;
    },
    { cacheTtlMs: 60_000 },
  );

  assert.equal(await resolver.requireUsdcParity(), "USD");
  assert.equal(await resolver.requireUsdcParity(), "USD");
  assert.equal(reads, 1);
  await assert.rejects(
    resolver.requireUsdcParity({ forceRefresh: true }),
    (error: unknown) =>
      error instanceof ShopifyCurrencyMismatchError &&
      /KRW.*not USD.*refusing/i.test(error.message),
  );
  assert.equal(reads, 2);
});

test("order payload binds the selected variant and SKU and rejects KRW", () => {
  const order = {
    ...input(`order-${crypto.randomUUID()}`),
    productId: "gid://shopify/Product/fallback",
    variantId: "gid://shopify/ProductVariant/selected",
    sku: "14:193#black",
    shippingAddress: realFormatShippingAddress,
  };
  const payload = buildShopifyOrderInput(order, "USD", false) as {
    currency: string;
    shippingAddress?: Record<string, string>;
    lineItems: { variantId: string; priceSet: { shopMoney: { currencyCode: string } } }[];
    customAttributes: { key: string; value: string }[];
  };

  assert.equal(payload.currency, "USD");
  assert.equal(payload.shippingAddress, undefined);
  assert.equal(
    payload.lineItems[0]?.variantId,
    "gid://shopify/ProductVariant/selected",
  );
  assert.equal(
    payload.lineItems[0]?.priceSet.shopMoney.currencyCode,
    "USD",
  );
  assert.equal(
    payload.customAttributes.find(({ key }) => key === "variant_id")?.value,
    "gid://shopify/ProductVariant/selected",
  );
  assert.equal(
    payload.customAttributes.find(({ key }) => key === "sku")?.value,
    "14:193#black",
  );
  assert.equal(
    payload.customAttributes.find(({ key }) => key === "ship_to")?.value,
    "Grace Hopper, 123 Main St, Arlington, VA, 22201, US",
  );
  assert.equal(
    payload.customAttributes.find(({ key }) => key === "supplier_cost_amount")
      ?.value,
    "1.00",
  );
  assert.equal(
    payload.customAttributes.find(({ key }) => key === "margin_status")?.value,
    "projected_snapshot",
  );
  assert.equal(
    payload.customAttributes.find(({ key }) => key === "supplier_order_status")
      ?.value,
    "disabled",
  );
  assert.throws(
    () => buildShopifyOrderInput(order, "KRW"),
    /KRW.*not USD.*refusing/i,
  );
});

test("shippingAddress is written only when the money gate and complete real fields are present", () => {
  const order = {
    ...input(`order-${crypto.randomUUID()}`),
    shippingAddress: realFormatShippingAddress,
  };
  const enabled = buildShopifyOrderInput(order, "USD", true) as {
    shippingAddress?: Record<string, string>;
    customAttributes: { key: string; value: string }[];
  };

  assert.deepEqual(enabled.shippingAddress, {
    firstName: "Grace",
    lastName: "Hopper",
    address1: "123 Main St",
    city: "Arlington",
    provinceCode: "VA",
    countryCode: "US",
    zip: "22201",
  });
  assert.equal(
    enabled.customAttributes.find(
      ({ key }) => key === "supplier_order_status",
    )?.value,
    "pending",
  );
  assert.equal(
    enabled.customAttributes.find(
      ({ key }) => key === "supplier_fulfillment_gate",
    )?.value,
    "enabled",
  );
  assert.equal(supplierOrderForInput(order, true).ref, null);
});

test("enabled supplier fulfillment fails closed on placeholders or incomplete fields", () => {
  const placeholder = {
    ...input(`order-${crypto.randomUUID()}`),
    shippingAddress: {
      ...realFormatShippingAddress,
      name: "placeholder",
    },
  };
  const payload = buildShopifyOrderInput(placeholder, "USD", true) as {
    shippingAddress?: Record<string, string>;
    customAttributes: { key: string; value: string }[];
  };

  assert.equal(payload.shippingAddress, undefined);
  assert.equal(
    payload.customAttributes.find(
      ({ key }) => key === "supplier_order_status",
    )?.value,
    "blocked",
  );
  assert.equal(supplierOrderForInput(placeholder, true).ref, null);
});

test("tracking is exposed only from a real Shopify carrier and number", () => {
  const real = trackingInfoFromFulfillments([
    {
      id: "gid://shopify/Fulfillment/1",
      status: "SUCCESS",
      trackingInfo: [
        {
          company: "USPS",
          number: "TEST-ONLY-NOT-A-SHIPMENT",
          url: "https://carrier.test/fixture",
        },
      ],
    },
  ]);

  assert.equal(real?.provider, "shopify");
  assert.equal(real?.demo, false);
  assert.equal(real?.carrier, "USPS");
  assert.equal(real?.trackingNumber, "TEST-ONLY-NOT-A-SHIPMENT");
  assert.equal(
    trackingInfoFromFulfillments([
      {
        id: "gid://shopify/Fulfillment/2",
        status: "SUCCESS",
        trackingInfo: [{ company: null, number: null, url: null }],
      },
    ]),
    null,
  );
});

test("Shopify idempotency tags stay within the platform limit", () => {
  const tag = orderTag(`ord_${"a".repeat(32)}`);

  assert.equal(tag, `relay_${"a".repeat(32)}`);
  assert.ok(tag.length <= 40);
});

test("only Shopify's transient order mutation error is retryable", () => {
  assert.equal(
    isTemporaryOrderMutationError([
      {
        field: ["id"],
        message: "Order is temporarily unavailable to be modified.",
      },
    ]),
    true,
  );
  assert.equal(
    isTemporaryOrderMutationError([
      { field: ["id"], message: "Order does not exist." },
    ]),
    false,
  );
});

test("Shopify token provider coalesces concurrent fetches and reuses its cache", async () => {
  let tokenRequests = 0;
  const fetchImpl = (async () => {
    tokenRequests += 1;
    return Response.json({
      access_token: "cached-token",
      scope: "read_products",
      expires_in: 3600,
    });
  }) as typeof fetch;
  const provider = new ShopifyTokenProvider(
    {
      domain: "shop.test",
      clientId: "client-id",
      clientSecret: "client-secret",
    },
    { fetch: fetchImpl, now: () => 1_000 },
  );

  const [first, concurrent] = await Promise.all([
    provider.getAccessToken(),
    provider.getAccessToken(),
  ]);
  const cached = await provider.getAccessToken();

  assert.equal(first, "cached-token");
  assert.equal(concurrent, "cached-token");
  assert.equal(cached, "cached-token");
  assert.equal(tokenRequests, 1);
});

test("Shopify token provider refreshes inside the pre-expiry margin", async () => {
  let now = 10_000;
  let tokenRequests = 0;
  const fetchImpl = (async () => {
    tokenRequests += 1;
    return Response.json({
      access_token: `token-${tokenRequests}`,
      scope: "read_products",
      expires_in: 120,
    });
  }) as typeof fetch;
  const provider = new ShopifyTokenProvider(
    {
      domain: "shop.test",
      clientId: "client-id",
      clientSecret: "client-secret",
    },
    { fetch: fetchImpl, now: () => now, refreshMarginMs: 60_000 },
  );

  assert.equal(await provider.getAccessToken(), "token-1");
  now += 59_999;
  assert.equal(await provider.getAccessToken(), "token-1");
  now += 1;
  assert.equal(await provider.getAccessToken(), "token-2");
  assert.equal(tokenRequests, 2);
});

test("Shopify Admin client refreshes and retries once after a 401", async () => {
  let tokenRequests = 0;
  const adminTokens: string[] = [];
  const fetchImpl = (async (input, init) => {
    const url = String(input);
    if (url.endsWith("/admin/oauth/access_token")) {
      tokenRequests += 1;
      return Response.json({
        access_token: tokenRequests === 1 ? "expired-token" : "fresh-token",
        scope: "read_products",
        expires_in: 3600,
      });
    }

    const token = new Headers(init?.headers).get("X-Shopify-Access-Token");
    adminTokens.push(token ?? "");
    if (token === "expired-token") {
      return Response.json({ error: "Unauthorized" }, { status: 401 });
    }
    return Response.json({ data: { shop: { name: "Relay" } } });
  }) as typeof fetch;
  const client = new ShopifyAdminClient(
    {
      domain: "shop.test",
      apiVersion: "2025-01",
      clientId: "client-id",
      clientSecret: "client-secret",
    },
    { fetch: fetchImpl, now: () => 10_000 },
  );

  const data = await client.graphql<{ shop: { name: string } }>(
    "query { shop { name } }",
  );

  assert.deepEqual(data, { shop: { name: "Relay" } });
  assert.equal(tokenRequests, 2);
  assert.deepEqual(adminTokens, ["expired-token", "fresh-token"]);
});

test("Shopify Admin client prefers the static-token fallback", async () => {
  let tokenRequests = 0;
  const adminTokens: string[] = [];
  const fetchImpl = (async (input, init) => {
    const url = String(input);
    if (url.endsWith("/admin/oauth/access_token")) {
      tokenRequests += 1;
      throw new Error("client credentials must not be used");
    }
    adminTokens.push(
      new Headers(init?.headers).get("X-Shopify-Access-Token") ?? "",
    );
    return Response.json({ data: { shop: { name: "Legacy" } } });
  }) as typeof fetch;
  const client = new ShopifyAdminClient(
    {
      domain: "shop.test",
      apiVersion: "2025-01",
      adminAccessToken: "static-token",
      clientId: "client-id",
      clientSecret: "client-secret",
    },
    { fetch: fetchImpl },
  );

  const data = await client.graphql<{ shop: { name: string } }>(
    "query { shop { name } }",
  );

  assert.deepEqual(data, { shop: { name: "Legacy" } });
  assert.equal(tokenRequests, 0);
  assert.deepEqual(adminTokens, ["static-token"]);
});
