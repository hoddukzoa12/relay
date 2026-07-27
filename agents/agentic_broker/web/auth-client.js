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
