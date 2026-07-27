/* Relay no-build Clerk + Solana delegation client. Tokens are never logged. */
(() => {
  if (window.RelayAuth) return;

  const loadedScripts = new Map();
  const AUTH_RETURN_PARAM = "relay_auth_return";
  const CLERK_SSO_RETURN_PARAM = "relay_clerk_sso_return";
  const CLERK_SSO_ATTEMPT_PARAM = "relay_clerk_sso_attempt";
  const CLERK_SSO_MAX_REDIRECTS = 2;
  const AUTH_RETURN_TTL_MS = 30 * 60 * 1000;
  const AUTH_TELEMETRY_LIMIT = 40;

  function loadScript(src, attributes = {}) {
    if (loadedScripts.has(src)) return loadedScripts.get(src);
    const operation = new Promise((resolve, reject) => {
      const existing = document.querySelector(`script[src="${CSS.escape(src)}"]`);
      if (existing) {
        if (existing.dataset.relayLoaded === "true") resolve();
        else {
          existing.addEventListener("load", resolve, { once: true });
          existing.addEventListener("error", reject, { once: true });
        }
        return;
      }
      const script = document.createElement("script");
      script.src = src;
      script.defer = true;
      script.crossOrigin = "anonymous";
      Object.entries(attributes).forEach(([key, value]) => {
        script.setAttribute(key, value);
      });
      script.addEventListener("load", () => {
        script.dataset.relayLoaded = "true";
        resolve();
      }, { once: true });
      script.addEventListener("error", () => {
        reject(new Error(`Unable to load ${src}`));
      }, { once: true });
      document.head.append(script);
    });
    loadedScripts.set(src, operation);
    return operation;
  }

  function frontendDomain(publishableKey) {
    try {
      const encoded = publishableKey.split("_")[2] || "";
      const normalized = encoded.replace(/-/g, "+").replace(/_/g, "/");
      const decoded = atob(normalized + "=".repeat((4 - normalized.length % 4) % 4));
      return decoded.endsWith("$") ? decoded.slice(0, -1) : decoded;
    } catch {
      throw new Error("The Clerk publishable key is invalid.");
    }
  }

  function currentRelativeUrl({ markAuthReturn = false } = {}) {
    const current = new URL(window.location.href);
    if (markAuthReturn) current.searchParams.set(AUTH_RETURN_PARAM, "1");
    return `${current.pathname}${current.search}${current.hash}`;
  }

  function shopifySignInUrl(
    configuredUrl = "/customer_authentication/login",
    returnTo = currentRelativeUrl({ markAuthReturn: true }),
  ) {
    const login = new URL(configuredUrl, window.location.origin);
    if (login.origin !== window.location.origin) {
      throw new Error("Shopify sign-in must stay on this storefront.");
    }
    login.searchParams.set("return_to", returnTo);
    return `${login.pathname}${login.search}`;
  }

  function canonicalize(value) {
    if (Array.isArray(value)) return value.map(canonicalize);
    if (value && typeof value === "object") {
      return Object.fromEntries(
        Object.entries(value)
          .sort(([left], [right]) => left < right ? -1 : left > right ? 1 : 0)
          .map(([key, child]) => [key, canonicalize(child)]),
      );
    }
    return value;
  }

  function canonicalMandateJson(mandate) {
    const { signature: _signature, ...unsigned } = mandate;
    return JSON.stringify(canonicalize(unsigned));
  }

  function base64url(bytes) {
    let binary = "";
    bytes.forEach((byte) => { binary += String.fromCharCode(byte); });
    return btoa(binary).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
  }

  function decodeBase64(value) {
    const binary = atob(value);
    return Uint8Array.from(binary, (character) => character.charCodeAt(0));
  }

  const wait = (milliseconds) =>
    new Promise((resolve) => window.setTimeout(resolve, milliseconds));

  class SolanaWalletAdapter {
    constructor(expectedAddress) {
      this.expectedAddress = expectedAddress;
      this.provider = [
        window.phantom?.solana,
        window.solflare,
        window.backpack,
        window.solana,
      ].find((candidate) => candidate?.connect);
      if (!this.provider) {
        throw new Error(
          "Open the same Solana wallet used for Clerk sign-in (Phantom, Solflare, or Backpack).",
        );
      }
    }

    async connect() {
      const result = await this.provider.connect();
      const publicKey = result?.publicKey || this.provider.publicKey;
      const address = publicKey?.toBase58?.() || publicKey?.toString?.() || "";
      if (!address) throw new Error("The Solana wallet did not return an address.");
      if (address !== this.expectedAddress) {
        throw new Error(
          `Connected wallet ${address} does not match signed-in wallet ${this.expectedAddress}.`,
        );
      }
      return address;
    }

    async signMessage(message) {
      await this.connect();
      const result = await this.provider.signMessage(message, "utf8");
      const signature = result?.signature || result;
      const bytes = signature instanceof Uint8Array
        ? signature
        : new Uint8Array(signature);
      if (bytes.length !== 64) {
        throw new Error("The Solana wallet returned an invalid signature.");
      }
      return bytes;
    }

    async signAndSendTransaction(transaction) {
      await this.connect();
      if (!this.provider.signAndSendTransaction) {
        throw new Error(
          "This wallet cannot submit the SPL approval transaction. Open Phantom and try again.",
        );
      }
      const result = await this.provider.signAndSendTransaction(transaction, {
        preflightCommitment: "confirmed",
        maxRetries: 5,
      });
      const signature = result?.signature || result;
      if (!signature || typeof signature !== "string") {
        throw new Error("The Solana wallet returned no transaction signature.");
      }
      return signature;
    }
  }

  class RelayAuthClient {
    constructor(baseUrl = "") {
      this.baseUrl = baseUrl.replace(/\/+$/, "");
      this.clerk = null;
      this.identity = null;
      this.sessionActive = false;
      this.authBranch = "initializing";
      this.authBranchSource = "initial";
      this.fallbackActive = false;
      this.fallbackAttempts = 0;
      this.fallbackRetrying = false;
      this.fallbackExhausted = false;
      this.fallbackMessage = "";
      this.listeners = new Set();
      this.ready = this.initialize();
    }

    url(path) {
      return `${this.baseUrl}${path}`;
    }

    async initialize() {
      const response = await fetch(this.url("/auth/config"), { mode: "cors" });
      const config = await response.json();
      if (!response.ok || !config.configured || !config.publishableKey) {
        this.setAuthBranch("clerk_unavailable");
        return this.status(false);
      }
      const returnedFromShopify = this.consumeShopifyReturn();
      const fallbackReturn = this.consumeFallbackReturn();
      const domain = frontendDomain(config.publishableKey);
      await loadScript(`https://${domain}/npm/@clerk/ui@1/dist/ui.browser.js`);
      await loadScript(
        `https://${domain}/npm/@clerk/clerk-js@6/dist/clerk.browser.js`,
        { "data-clerk-publishable-key": config.publishableKey },
      );
      this.clerk = window.Clerk;
      await this.clerk.load({
        ui: { ClerkUI: window.__internal_ClerkUICtor },
      });
      this.clerk.addListener(() => {
        const previousSessionActive = this.sessionActive;
        this.refreshIdentity({ notify: false })
          .then(() => {
            if (this.sessionActive && !previousSessionActive && this.fallbackActive) {
              this.clearFallbackAttempts();
              this.resetFallbackState();
              this.setAuthBranch("clerk_session_active", "clerk_sso_listener");
            } else if (!this.sessionActive && previousSessionActive) {
              this.setAuthBranch("shopify_login_required", "session_ended");
            } else {
              this.emit();
            }
          })
          .catch(() => {
            this.identity = null;
            this.emit();
          });
      });
      await this.refreshIdentity({ notify: false });
      if (this.sessionActive) {
        this.clearFallbackAttempts();
        this.resetFallbackState();
        this.setAuthBranch(
          "clerk_session_active",
          fallbackReturn.returned
            ? "clerk_sso_return"
            : returnedFromShopify
              ? "shopify_return"
              : "initial",
        );
      } else if (returnedFromShopify) {
        this.clearFallbackAttempts();
        this.resetFallbackState();
        this.fallbackActive = true;
        this.setAuthBranch("shopify_return_fallback", "shopify_return");
      } else if (fallbackReturn.returned && fallbackReturn.attempts < CLERK_SSO_MAX_REDIRECTS) {
        this.fallbackActive = true;
        this.fallbackAttempts = fallbackReturn.attempts;
        this.fallbackRetrying = true;
        this.recordAuthEvent("clerk_sso_return_without_session", "clerk_sso_return");
        this.setAuthBranch("shopify_return_fallback", "clerk_sso_retry");
        window.setTimeout(() => {
          this.openFallbackSignIn({ retry: true }).catch((error) => {
            if (!this.fallbackExhausted) {
              this.stopFallback(
                `Clerk SSO could not retry: ${error?.message || "unknown error"}`,
                "clerk_sso_retry_error",
                "clerk_sso_redirect_error",
              );
            }
          });
        }, 0);
      } else if (
        fallbackReturn.returned ||
        fallbackReturn.attempts >= CLERK_SSO_MAX_REDIRECTS
      ) {
        this.fallbackAttempts = fallbackReturn.attempts;
        this.stopFallback(
          "Clerk SSO returned twice without exposing a storefront session. Automatic redirects stopped.",
          "clerk_sso_retry_exhausted",
          "clerk_sso_retry_exhausted",
        );
      } else {
        this.setAuthBranch("shopify_login_required", "initial");
      }
      return this.status(true);
    }

    status(configured = Boolean(this.clerk)) {
      return {
        configured,
        branch: this.authBranch,
        source: this.authBranchSource,
        fallbackRequired:
          this.authBranch === "shopify_return_fallback" &&
          !this.fallbackRetrying &&
          !this.fallbackExhausted,
        fallbackRetrying: this.fallbackRetrying,
        fallbackExhausted: this.fallbackExhausted,
        fallbackAttemptCount: this.fallbackAttempts,
        fallbackMessage: this.fallbackMessage,
        sessionActive: this.sessionActive,
        walletVerified: Boolean(this.identity?.walletAddress),
      };
    }

    returnMarkerKey() {
      return `relay:shopify-auth-return:v1:${this.baseUrl}`;
    }

    fallbackAttemptKey() {
      return `relay:clerk-sso-attempt:v1:${this.baseUrl}`;
    }

    telemetryKey() {
      return `relay:auth-telemetry:v1:${this.baseUrl}`;
    }

    readFallbackAttempts() {
      try {
        const raw = window.sessionStorage.getItem(this.fallbackAttemptKey());
        const marker = raw ? JSON.parse(raw) : null;
        if (
          Number.isInteger(marker?.attempts) &&
          marker.attempts >= 0 &&
          Number.isFinite(marker?.startedAt) &&
          Date.now() - marker.startedAt <= AUTH_RETURN_TTL_MS
        ) {
          return marker.attempts;
        }
        window.sessionStorage.removeItem(this.fallbackAttemptKey());
      } catch {}
      return 0;
    }

    saveFallbackAttempts(attempts) {
      this.fallbackAttempts = attempts;
      try {
        window.sessionStorage.setItem(
          this.fallbackAttemptKey(),
          JSON.stringify({ attempts, startedAt: Date.now() }),
        );
      } catch {}
    }

    clearFallbackAttempts() {
      this.fallbackAttempts = 0;
      try {
        window.sessionStorage.removeItem(this.fallbackAttemptKey());
      } catch {}
    }

    resetFallbackState() {
      this.fallbackActive = false;
      this.fallbackRetrying = false;
      this.fallbackExhausted = false;
      this.fallbackMessage = "";
    }

    stopFallback(message, source, event) {
      this.fallbackActive = true;
      this.fallbackRetrying = false;
      this.fallbackExhausted = true;
      this.fallbackMessage = message;
      this.recordAuthEvent(event, source);
      this.setAuthBranch("shopify_return_fallback", source);
    }

    fallbackReturnUrl(attempts) {
      const current = new URL(window.location.href);
      current.searchParams.delete(AUTH_RETURN_PARAM);
      current.searchParams.set(CLERK_SSO_RETURN_PARAM, "1");
      current.searchParams.set(CLERK_SSO_ATTEMPT_PARAM, String(attempts));
      return current.href;
    }

    consumeFallbackReturn() {
      const current = new URL(window.location.href);
      const returned = current.searchParams.get(CLERK_SSO_RETURN_PARAM) === "1";
      const queryAttempts = Number.parseInt(
        current.searchParams.get(CLERK_SSO_ATTEMPT_PARAM) || "0",
        10,
      );
      const attempts = Math.max(
        this.readFallbackAttempts(),
        Number.isInteger(queryAttempts) && queryAttempts >= 0 ? queryAttempts : 0,
      );
      this.fallbackAttempts = attempts;
      if (returned) {
        try {
          current.searchParams.delete(CLERK_SSO_RETURN_PARAM);
          current.searchParams.delete(CLERK_SSO_ATTEMPT_PARAM);
          window.history.replaceState(
            window.history.state,
            "",
            `${current.pathname}${current.search}${current.hash}`,
          );
        } catch {}
      }
      return { returned, attempts };
    }

    saveShopifyReturnMarker() {
      try {
        window.sessionStorage.setItem(
          this.returnMarkerKey(),
          JSON.stringify({ startedAt: Date.now() }),
        );
      } catch {}
    }

    consumeShopifyReturn() {
      const current = new URL(window.location.href);
      const returnParamPresent = current.searchParams.get(AUTH_RETURN_PARAM) === "1";
      let markerPresent = false;
      try {
        const raw = window.sessionStorage.getItem(this.returnMarkerKey());
        window.sessionStorage.removeItem(this.returnMarkerKey());
        const marker = raw ? JSON.parse(raw) : null;
        markerPresent = Number.isFinite(marker?.startedAt) &&
          Date.now() - marker.startedAt <= AUTH_RETURN_TTL_MS;
      } catch {}
      if (returnParamPresent) {
        try {
          current.searchParams.delete(AUTH_RETURN_PARAM);
          window.history.replaceState(
            window.history.state,
            "",
            `${current.pathname}${current.search}${current.hash}`,
          );
        } catch {}
      }
      return returnParamPresent || markerPresent;
    }

    telemetry() {
      try {
        const entries = JSON.parse(
          window.localStorage.getItem(this.telemetryKey()) || "[]",
        );
        return Array.isArray(entries) ? entries : [];
      } catch {
        return [];
      }
    }

    recordAuthEvent(event, source = this.authBranchSource) {
      const record = {
        event,
        source,
        at: new Date().toISOString(),
        path: window.location.pathname,
      };
      try {
        const entries = [...this.telemetry(), record].slice(-AUTH_TELEMETRY_LIMIT);
        window.localStorage.setItem(this.telemetryKey(), JSON.stringify(entries));
      } catch {}
      try {
        console.info("[Relay auth]", record);
        window.dispatchEvent(new CustomEvent("relay:auth-branch", {
          detail: record,
        }));
      } catch {}
      return record;
    }

    setAuthBranch(branch, source = this.authBranchSource) {
      this.authBranch = branch;
      this.authBranchSource = source;
      this.recordAuthEvent(branch, source);
      this.emit();
    }

    subscribe(listener) {
      this.listeners.add(listener);
      listener(this.identity);
      return () => this.listeners.delete(listener);
    }

    emit() {
      this.listeners.forEach((listener) => listener(this.identity));
    }

    async token() {
      if (!this.clerk) await this.ready;
      return this.clerk?.session?.getToken?.() || null;
    }

    async refreshIdentity({ notify = true } = {}) {
      const token = await this.token();
      if (!token) {
        this.sessionActive = false;
        this.identity = null;
        if (notify) this.emit();
        return null;
      }
      this.sessionActive = true;
      const response = await fetch(this.url("/auth/me"), {
        mode: "cors",
        headers: { Authorization: `Bearer ${token}` },
      });
      if (!response.ok) {
        this.identity = {
          userId: this.clerk?.user?.id || "",
          walletAddress: "",
          displayName: this.displayName(),
        };
        if (notify) this.emit();
        return this.identity;
      }
      this.identity = {
        ...await response.json(),
        displayName: this.displayName(),
      };
      if (notify) this.emit();
      return this.identity;
    }

    displayName() {
      const user = this.clerk?.user;
      const fullName = [user?.firstName, user?.lastName].filter(Boolean).join(" ");
      const email = user?.primaryEmailAddress?.emailAddress || "";
      return fullName || user?.username || email.split("@")[0] || "Shopify 고객";
    }

    async signIn() {
      const status = await this.ready;
      if (!status.configured) throw new Error("Clerk is not configured.");
      this.clerk.openSignIn();
    }

    async signInViaShopify(configuredUrl = "/customer_authentication/login") {
      const status = await this.ready;
      if (!status.configured) throw new Error("Clerk is not configured.");
      if (!this.sessionActive) await this.refreshIdentity();
      if (this.sessionActive) {
        this.clearFallbackAttempts();
        this.resetFallbackState();
        this.setAuthBranch("clerk_session_active", "pre_redirect_recheck");
        return false;
      }
      this.clearFallbackAttempts();
      this.resetFallbackState();
      this.saveShopifyReturnMarker();
      this.recordAuthEvent("shopify_login_redirect", "shopify");
      window.location.assign(shopifySignInUrl(configuredUrl));
      return true;
    }

    async openFallbackSignIn({ retry = false } = {}) {
      const status = await this.ready;
      if (!status.configured) throw new Error("Clerk is not configured.");
      if (!this.sessionActive) await this.refreshIdentity();
      if (this.sessionActive) {
        this.clearFallbackAttempts();
        this.resetFallbackState();
        this.setAuthBranch("clerk_session_active", "pre_sso_redirect_recheck");
        return this.identity;
      }
      const previousAttempts = Math.max(
        this.fallbackAttempts,
        this.readFallbackAttempts(),
      );
      if (previousAttempts >= CLERK_SSO_MAX_REDIRECTS) {
        const message =
          "Clerk SSO returned twice without exposing a storefront session. Automatic redirects stopped.";
        this.stopFallback(
          message,
          "clerk_sso_retry_exhausted",
          "clerk_sso_retry_exhausted",
        );
        throw new Error(message);
      }
      if (typeof this.clerk?.redirectToSignIn !== "function") {
        const message = "Clerk redirect sign-in is unavailable in this browser.";
        this.stopFallback(
          message,
          "clerk_sso_redirect_unavailable",
          "clerk_sso_redirect_error",
        );
        throw new Error(message);
      }
      const nextAttempt = previousAttempts + 1;
      this.fallbackActive = true;
      this.fallbackRetrying = true;
      this.fallbackExhausted = false;
      this.fallbackMessage = "";
      this.saveFallbackAttempts(nextAttempt);
      const source = retry ? "clerk_sso_retry" : "shopify_return";
      this.recordAuthEvent(
        retry ? "clerk_sso_retry_redirect" : "clerk_sso_redirect",
        source,
      );
      this.emit();
      try {
        await this.clerk.redirectToSignIn({
          redirectUrl: this.fallbackReturnUrl(nextAttempt),
        });
      } catch (error) {
        if (previousAttempts > 0) this.saveFallbackAttempts(previousAttempts);
        else this.clearFallbackAttempts();
        this.stopFallback(
          `Clerk SSO redirect failed: ${error?.message || "unknown error"}`,
          "clerk_sso_redirect_error",
          "clerk_sso_redirect_error",
        );
        throw error;
      }
      return null;
    }

    async signOut() {
      await this.ready;
      await this.clerk?.signOut?.();
      this.sessionActive = false;
      this.identity = null;
      this.emit();
    }

    async signedRequest(path, options = {}) {
      const token = await this.token();
      if (!token) throw new Error("Sign in with your Solana wallet first.");
      const headers = new Headers(options.headers || {});
      headers.set("Authorization", `Bearer ${token}`);
      return fetch(this.url(path), {
        mode: "cors",
        ...options,
        headers,
      });
    }

    approvalStorageKey(walletAddress) {
      return `relay:spl-approval:${this.baseUrl}:${walletAddress}`;
    }

    approvalSignature(walletAddress) {
      try {
        return localStorage.getItem(this.approvalStorageKey(walletAddress)) || "";
      } catch {
        return "";
      }
    }

    saveApprovalSignature(walletAddress, signature) {
      try {
        if (signature) {
          localStorage.setItem(this.approvalStorageKey(walletAddress), signature);
        } else {
          localStorage.removeItem(this.approvalStorageKey(walletAddress));
        }
      } catch {}
    }

    async signedJson(path, options = {}) {
      const response = await this.signedRequest(path, options);
      const payload = await response.json();
      if (!response.ok) {
        const detail = payload?.detail || payload?.error || `HTTP ${response.status}`;
        throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
      }
      return payload;
    }

    async delegation() {
      const identity = this.identity || await this.refreshIdentity();
      if (!identity?.walletAddress) {
        throw new Error("Sign in with your Solana wallet first.");
      }
      const status = await this.signedJson("/delegation");
      const approvalTxSignature = status.active
        ? this.approvalSignature(identity.walletAddress)
        : "";
      if (!status.active) this.saveApprovalSignature(identity.walletAddress, "");
      return { ...status, approvalTxSignature };
    }

    async submitDelegation(action, amount) {
      const identity = this.identity || await this.refreshIdentity();
      if (!identity?.walletAddress) {
        throw new Error("Sign in with your Solana wallet first.");
      }
      const body = { action };
      if (action === "approve") body.amount = Number(amount).toFixed(6).replace(/0+$/, "").replace(/\.$/, "");
      const prepared = await this.signedJson("/delegation/transaction", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      await loadScript(
        "https://cdn.jsdelivr.net/npm/@solana/web3.js@1.98.4/lib/index.iife.min.js",
      );
      if (!window.solanaWeb3?.Transaction) {
        throw new Error("Unable to load the Solana transaction client.");
      }
      const transaction = window.solanaWeb3.Transaction.from(
        decodeBase64(prepared.transaction),
      );
      const adapter = new SolanaWalletAdapter(identity.walletAddress);
      const signature = await adapter.signAndSendTransaction(transaction);
      if (action === "approve") {
        this.saveApprovalSignature(identity.walletAddress, signature);
      }

      for (let attempt = 0; attempt < 24; attempt += 1) {
        await wait(1_000);
        const status = await this.delegation();
        if (action === "approve" && status.active) {
          return { ...status, approvalTxSignature: signature };
        }
        if (action === "revoke" && !status.active) {
          this.saveApprovalSignature(identity.walletAddress, "");
          return { ...status, approvalTxSignature: signature };
        }
      }
      throw new Error(
        `${action === "approve" ? "Approval" : "Revoke"} transaction ${signature} is still confirming.`,
      );
    }

    async approveDelegation(amount = 50) {
      const limit = Number(amount);
      if (!Number.isFinite(limit) || limit <= 0) {
        throw new Error("Enter a positive USDC delegation limit.");
      }
      return this.submitDelegation("approve", limit);
    }

    async revokeDelegation() {
      return this.submitDelegation("revoke");
    }

    async signIntent({ query, budget, shipTo, shippingAddress = null }) {
      const identity = this.identity || await this.refreshIdentity();
      if (!identity?.walletAddress) {
        throw new Error("Sign in with your Solana wallet first.");
      }
      const unsigned = {
        user_cart_confirmation_required: false,
        natural_language_description: query,
        requires_refundability: false,
        price_ceiling: {
          amount: Number(budget).toFixed(2),
          currency: "USDC",
        },
        ship_to: shipTo,
        ...(shippingAddress ? { shipping_address: shippingAddress } : {}),
        intent_expiry: new Date(Date.now() + 15 * 60 * 1000).toISOString(),
        signer_wallet: identity.walletAddress,
      };
      const adapter = new SolanaWalletAdapter(identity.walletAddress);
      const signature = await adapter.signMessage(
        new TextEncoder().encode(canonicalMandateJson(unsigned)),
      );
      return { ...unsigned, signature: base64url(signature) };
    }
  }

  const clients = new Map();
  window.RelayAuth = {
    client(baseUrl = "") {
      const key = baseUrl.replace(/\/+$/, "");
      if (!clients.has(key)) clients.set(key, new RelayAuthClient(key));
      return clients.get(key);
    },
    canonicalMandateJson,
    shopifySignInUrl,
  };
})();
