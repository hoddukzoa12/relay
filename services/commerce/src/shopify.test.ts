import assert from "node:assert/strict";
import test from "node:test";
import {
  catalogProductsFromShopify,
  listProducts,
  rankAndLimit,
} from "./catalog.js";
import {
  ShopifyAdminClient,
  ShopifyTokenProvider,
} from "./shopify-client.js";
import {
  createPaidOrder,
  fulfillOrder,
  getOrderStatus,
  isTemporaryOrderMutationError,
  listOrdersByWallet,
  markOrderRefunded,
  orderTag,
  type OrderInput,
} from "./shopify.js";
import {
  DEMO_TRACKING_NUMBER,
  EasyPostTrackingProvider,
  demoTrackingInfo,
} from "./tracking.js";

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
});

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

test("mock order lifecycle supports lookup, idempotent fulfillment, and refund", async () => {
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

  const fulfilled = await fulfillOrder(order.orderRef);
  const fulfillmentReplay = await fulfillOrder(order.orderRef);
  assert.equal(fulfilled.fulfillmentStatus, "FULFILLED");
  assert.equal(fulfilled.tracking.trackingNumber, DEMO_TRACKING_NUMBER);
  assert.equal(fulfilled.tracking.demo, true);
  assert.equal(fulfilled.replayed, false);
  assert.equal(fulfillmentReplay.replayed, true);

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

test("demo tracking metadata never claims a real shipment", () => {
  const tracking = demoTrackingInfo();
  assert.equal(tracking.demo, true);
  assert.match(tracking.message, /DEMO.*not.*real parcel/i);
});

test("EasyPost provider uses the official tracker endpoint", async () => {
  let requestedUrl = "";
  let requestedBody = "";
  const fetchImpl = (async (url, init) => {
    requestedUrl = String(url);
    requestedBody = String(init?.body ?? "");
    return Response.json({
      tracking_code: DEMO_TRACKING_NUMBER,
      carrier: "USPS",
      status: "in_transit",
      status_detail: "in_transit",
      public_url: "https://track.easypost.test/demo",
      est_delivery_date: null,
    });
  }) as typeof fetch;

  const result = await new EasyPostTrackingProvider(
    "test-key",
    fetchImpl,
  ).lookup(DEMO_TRACKING_NUMBER, "USPS");

  assert.equal(requestedUrl, "https://api.easypost.com/v2/trackers");
  assert.match(requestedBody, new RegExp(DEMO_TRACKING_NUMBER));
  assert.equal(result.status, "in_transit");
  assert.equal(result.demo, true);
});

test("mock catalog is deterministic and query-ranked", async () => {
  const products = await listProducts("wireless earbuds", 3);

  assert.equal(products.length, 3);
  assert.match(products[0]?.title ?? "", /Earbuds/);
  assert.ok(products.every((product) => product.inventoryQuantity > 0));
  assert.ok(products.every((product) => product.sku.startsWith("RELAY-")));
});

test("live catalog tolerates Shopify variants with a null SKU", () => {
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
                price: "3.00",
                inventoryQuantity: 5,
              },
              {
                id: "gid://shopify/ProductVariant/alternate-sku",
                sku: "RELAY-ALTERNATE",
                price: "3.00",
                inventoryQuantity: 5,
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
  assert.equal(ranked[0]?.variantId, "gid://shopify/ProductVariant/without-sku");
  assert.equal(ranked[0]?.sku, "");
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
