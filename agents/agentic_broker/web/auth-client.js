/* Relay no-build Clerk + Solana delegation client. Tokens are never logged. */
(() => {
  if (window.RelayAuth) return;

  const loadedScripts = new Map();

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

  class SolanaWalletAdapter {
    constructor(expectedAddress) {
      this.expectedAddress = expectedAddress;
      this.provider = [
        window.phantom?.solana,
        window.solflare,
        window.backpack,
        window.solana,
      ].find((candidate) => candidate?.connect && candidate?.signMessage);
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
  }

  class RelayAuthClient {
    constructor(baseUrl = "") {
      this.baseUrl = baseUrl.replace(/\/+$/, "");
      this.clerk = null;
      this.identity = null;
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
        return { configured: false };
      }
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
        this.refreshIdentity().catch(() => {
          this.identity = null;
          this.emit();
        });
      });
      await this.refreshIdentity();
      return { configured: true };
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

    async refreshIdentity() {
      const token = await this.token();
      if (!token) {
        this.identity = null;
        this.emit();
        return null;
      }
      const response = await fetch(this.url("/auth/me"), {
        mode: "cors",
        headers: { Authorization: `Bearer ${token}` },
      });
      if (!response.ok) {
        this.identity = null;
        this.emit();
        return null;
      }
      this.identity = await response.json();
      this.emit();
      return this.identity;
    }

    async signIn() {
      const status = await this.ready;
      if (!status.configured) throw new Error("Clerk is not configured.");
      this.clerk.openSignIn();
    }

    async signOut() {
      await this.ready;
      await this.clerk?.signOut?.();
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

    async signIntent({ query, budget, shipTo }) {
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
  };
})();
