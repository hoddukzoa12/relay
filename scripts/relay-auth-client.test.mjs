import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import vm from "node:vm";

const source = await readFile(
  new URL("../agents/agentic_broker/web/auth-client.js", import.meta.url),
  "utf8",
);
const storefront = await readFile(
  new URL("../sections/relay-agent-chat.liquid", import.meta.url),
  "utf8",
);

function storage(initial = {}) {
  const values = new Map(Object.entries(initial));
  return {
    getItem(key) {
      return values.has(key) ? values.get(key) : null;
    },
    removeItem(key) {
      values.delete(key);
    },
    setItem(key, value) {
      values.set(key, String(value));
    },
  };
}

function harness({
  backendIdentity = true,
  localStore = storage(),
  session = false,
  sessionStore = storage(),
  url = "https://shop.test/",
} = {}) {
  const events = [];
  const assigned = [];
  const redirects = [];
  const replaced = [];
  const scheduled = [];
  const listeners = [];
  const locationUrl = new URL(url);
  const clerk = {
    session: session
      ? { async getToken() { return "session-token"; } }
      : null,
    user: session
      ? {
          firstName: "Relay",
          lastName: "Buyer",
          primaryEmailAddress: { emailAddress: "buyer@example.com" },
        }
      : null,
    opened: 0,
    async load() {},
    addListener(listener) {
      listeners.push(listener);
    },
    openSignIn() {
      this.opened += 1;
    },
    async redirectToSignIn(options) {
      redirects.push(options);
    },
    async signOut() {},
  };
  const location = {
    href: locationUrl.href,
    origin: locationUrl.origin,
    pathname: locationUrl.pathname,
    assign(value) {
      assigned.push(value);
    },
  };
  const publishableKey =
    `pk_test_${Buffer.from("clerk.test$", "utf8").toString("base64")}`;
  const context = {
    URL,
    Uint8Array,
    TextEncoder,
    atob(value) {
      return Buffer.from(value, "base64").toString("binary");
    },
    btoa(value) {
      return Buffer.from(value, "binary").toString("base64");
    },
    console: { info() {} },
    CSS: { escape(value) { return value; } },
    CustomEvent: class {
      constructor(type, options) {
        this.type = type;
        this.detail = options?.detail;
      }
    },
    document: {
      querySelector() {
        return { dataset: { relayLoaded: "true" } };
      },
    },
    async fetch(requestUrl) {
      if (requestUrl.endsWith("/auth/config")) {
        return {
          ok: true,
          async json() {
            return { configured: true, publishableKey };
          },
        };
      }
      if (requestUrl.endsWith("/auth/me") && session && backendIdentity) {
        return {
          ok: true,
          async json() {
            return {
              userId: "user_test",
              walletAddress: "BuyerWallet1111111111111111111111111111111",
            };
          },
        };
      }
      return { ok: false, async json() { return {}; } };
    },
    window: {
      Clerk: clerk,
      __internal_ClerkUICtor: class {},
      dispatchEvent(event) {
        events.push(event.detail);
      },
      history: {
        state: null,
        replaceState(_state, _title, replacement) {
          replaced.push(replacement);
        },
      },
      localStorage: localStore,
      location,
      sessionStorage: sessionStore,
      setTimeout(callback, _milliseconds, ...args) {
        scheduled.push(() => callback(...args));
        return scheduled.length;
      },
    },
  };
  vm.runInNewContext(source, context);
  return {
    assigned,
    clerk,
    client: context.window.RelayAuth.client("https://buyer.test"),
    events,
    localStorage: localStore,
    redirects,
    replaced,
    async runScheduled() {
      while (scheduled.length) await scheduled.shift()();
      await Promise.resolve();
    },
    scheduled,
    sessionStorage: sessionStore,
  };
}

test("uses an active Clerk session without showing another login", async () => {
  const { client, events } = harness({ session: true });
  const status = await client.ready;

  assert.deepEqual(
    { branch: status.branch, source: status.source },
    { branch: "clerk_session_active", source: "initial" },
  );
  assert.equal(client.identity.displayName, "Relay Buyer");
  assert.equal(events.at(-1).event, "clerk_session_active");
});

test("does not restart Shopify login when the Clerk session lacks a wallet", async () => {
  const { assigned, client } = harness({ backendIdentity: false, session: true });
  const status = await client.ready;

  assert.equal(status.branch, "clerk_session_active");
  assert.equal(status.sessionActive, true);
  assert.equal(status.walletVerified, false);
  assert.equal(client.identity.displayName, "Relay Buyer");
  assert.equal(await client.signInViaShopify(), false);
  assert.equal(assigned.length, 0);
});

test("sends an anonymous buyer through Shopify and preserves preview state", async () => {
  const {
    assigned,
    client,
    sessionStorage,
  } = harness({
    url: "https://shop.test/?preview_theme_id=204473499934",
  });
  const status = await client.ready;
  assert.equal(status.branch, "shopify_login_required");

  await client.signInViaShopify(
    "/customer_authentication/login?locale=en&ui_hint=full",
  );

  assert.ok(sessionStorage.getItem(client.returnMarkerKey()));
  const redirect = new URL(assigned[0], "https://shop.test");
  assert.equal(redirect.pathname, "/customer_authentication/login");
  assert.equal(redirect.searchParams.get("locale"), "en");
  assert.equal(
    redirect.searchParams.get("return_to"),
    "/?preview_theme_id=204473499934&relay_auth_return=1",
  );
});

test("redirects to Clerk SSO when Shopify returns without a Clerk session", async () => {
  const {
    clerk,
    client,
    events,
    redirects,
    replaced,
  } = harness({
    url: "https://shop.test/?preview_theme_id=204473499934&relay_auth_return=1",
  });
  const status = await client.ready;

  assert.equal(status.branch, "shopify_return_fallback");
  assert.equal(status.fallbackRequired, true);
  assert.equal(replaced[0], "/?preview_theme_id=204473499934");

  await client.openFallbackSignIn();
  assert.equal(clerk.opened, 0);
  assert.equal(redirects.length, 1);
  const redirect = new URL(redirects[0].redirectUrl);
  assert.equal(redirect.origin, "https://shop.test");
  assert.equal(redirect.searchParams.get("preview_theme_id"), "204473499934");
  assert.equal(redirect.searchParams.get("relay_clerk_sso_return"), "1");
  assert.equal(redirect.searchParams.get("relay_clerk_sso_attempt"), "1");
  assert.deepEqual(
    events.slice(-2).map(({ event }) => event),
    ["shopify_return_fallback", "clerk_sso_redirect"],
  );
});

test("retries Clerk SSO once, then stops redirects and exposes a failure state", async () => {
  const sessionStore = storage();
  const first = harness({
    sessionStore,
    url: "https://shop.test/?preview_theme_id=204473499934&relay_auth_return=1",
  });
  await first.client.ready;
  await first.client.openFallbackSignIn();

  const retry = harness({
    sessionStore,
    url: "https://shop.test/?preview_theme_id=204473499934&relay_clerk_sso_return=1&relay_clerk_sso_attempt=1",
  });
  const retryStatus = await retry.client.ready;
  assert.equal(retryStatus.fallbackRequired, false);
  assert.equal(retryStatus.fallbackRetrying, true);
  assert.equal(retryStatus.fallbackAttemptCount, 1);
  assert.equal(retry.scheduled.length, 1);

  await retry.runScheduled();
  assert.equal(retry.clerk.opened, 0);
  assert.equal(retry.redirects.length, 1);
  const retryRedirect = new URL(retry.redirects[0].redirectUrl);
  assert.equal(retryRedirect.searchParams.get("relay_clerk_sso_attempt"), "2");
  assert.ok(
    retry.events.some(({ event }) => event === "clerk_sso_return_without_session"),
  );
  assert.ok(
    retry.events.some(({ event }) => event === "clerk_sso_retry_redirect"),
  );

  const exhausted = harness({
    sessionStore,
    url: "https://shop.test/?preview_theme_id=204473499934&relay_clerk_sso_return=1&relay_clerk_sso_attempt=2",
  });
  const exhaustedStatus = await exhausted.client.ready;
  assert.equal(exhaustedStatus.branch, "shopify_return_fallback");
  assert.equal(exhaustedStatus.source, "clerk_sso_retry_exhausted");
  assert.equal(exhaustedStatus.fallbackRequired, false);
  assert.equal(exhaustedStatus.fallbackRetrying, false);
  assert.equal(exhaustedStatus.fallbackExhausted, true);
  assert.equal(exhaustedStatus.fallbackAttemptCount, 2);
  assert.match(exhaustedStatus.fallbackMessage, /Automatic redirects stopped/);
  assert.equal(exhausted.scheduled.length, 0);
  assert.equal(exhausted.redirects.length, 0);
  assert.ok(
    exhausted.events.some(({ event }) => event === "clerk_sso_retry_exhausted"),
  );
});

test("accepts the Clerk session returned by redirect SSO and clears its loop guard", async () => {
  const sessionStore = storage({
    "relay:clerk-sso-attempt:v1:https://buyer.test": JSON.stringify({
      attempts: 1,
      startedAt: Date.now(),
    }),
  });
  const { client, events } = harness({
    session: true,
    sessionStore,
    url: "https://shop.test/?relay_clerk_sso_return=1&relay_clerk_sso_attempt=1",
  });
  const status = await client.ready;

  assert.deepEqual(
    { branch: status.branch, source: status.source },
    { branch: "clerk_session_active", source: "clerk_sso_return" },
  );
  assert.equal(sessionStore.getItem(client.fallbackAttemptKey()), null);
  assert.equal(events.at(-1).source, "clerk_sso_return");
});

test("recognizes a Clerk session exposed after the Shopify round trip", async () => {
  const { client, events } = harness({
    session: true,
    url: "https://shop.test/?relay_auth_return=1",
  });
  const status = await client.ready;

  assert.deepEqual(
    { branch: status.branch, source: status.source },
    { branch: "clerk_session_active", source: "shopify_return" },
  );
  assert.equal(events.at(-1).source, "shopify_return");
});

test("keeps anonymous search public while blocking storefront payment", () => {
  assert.match(
    storefront,
    /result = this\.identity\?\.walletAddress\s+\? await this\.authClient\.signedJson\("\/chat"/,
  );
  assert.match(
    storefront,
    /: await this\.fetchJson\("\/chat"/,
  );
  assert.match(
    storefront,
    /await this\.requirePurchaseDelegation\(product,\s*\{/,
  );
  assert.match(
    storefront,
    /Search and comparison stay available without an account\./,
  );
  assert.match(
    storefront,
    /showAuthFailureGate\(product, options = \{\}, status = \{\}\)/,
  );
  assert.match(
    storefront,
    /Automatic redirects have stopped\./,
  );
  assert.doesNotMatch(storefront, />Wallet sign-in</);
});
