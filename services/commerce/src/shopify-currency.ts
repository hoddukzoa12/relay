const STORE_CURRENCY_QUERY = /* GraphQL */ `
  query RelayStoreCurrency {
    shop {
      currencyCode
    }
  }
`;

const USDC_PARITY_CURRENCY = "USD";
const DEFAULT_CACHE_TTL_MS = 60_000;

interface StoreCurrencyData {
  shop: {
    currencyCode: string;
  };
}

type GraphQLExecutor = <T>(
  query: string,
  variables: Record<string, unknown>,
) => Promise<T>;

export interface StoreCurrencyOptions {
  forceRefresh?: boolean;
}

export interface ShopifyStoreCurrencyDependencies {
  now?: () => number;
  cacheTtlMs?: number;
}

export class ShopifyCurrencyMismatchError extends Error {}

/**
 * USDC prices may only be copied 1:1 from a USD-denominated Shopify store.
 * Supporting any other store currency requires an explicit FX conversion layer.
 */
export function requireUsdcParityCurrency(currencyCode: string): string {
  const normalized = currencyCode.trim().toUpperCase();
  if (normalized !== USDC_PARITY_CURRENCY) {
    throw new ShopifyCurrencyMismatchError(
      `Shopify store currency is ${normalized || "missing"}, not USD; ` +
        "refusing to interpret catalog prices or create an order in USDC",
    );
  }
  return normalized;
}

/**
 * Reads Shopify's store currency as the source of truth.
 *
 * A fresh value is cached briefly, but an expired read is never silently
 * replaced with stale data: if Shopify cannot confirm the currency, callers
 * fail closed.
 */
export class ShopifyStoreCurrency {
  private readonly now: () => number;
  private readonly cacheTtlMs: number;
  private cached: { currency: string; expiresAtMs: number } | undefined;
  private inFlight: Promise<string> | undefined;

  constructor(
    private readonly graphql: GraphQLExecutor,
    dependencies: ShopifyStoreCurrencyDependencies = {},
  ) {
    this.now = dependencies.now ?? Date.now;
    this.cacheTtlMs = dependencies.cacheTtlMs ?? DEFAULT_CACHE_TTL_MS;
  }

  async get(options: StoreCurrencyOptions = {}): Promise<string> {
    if (
      !options.forceRefresh &&
      this.cached &&
      this.now() < this.cached.expiresAtMs
    ) {
      return this.cached.currency;
    }
    if (this.inFlight) return this.inFlight;

    const request = this.fetch();
    this.inFlight = request;
    try {
      return await request;
    } finally {
      if (this.inFlight === request) {
        this.inFlight = undefined;
      }
    }
  }

  async requireUsdcParity(
    options: StoreCurrencyOptions = {},
  ): Promise<string> {
    return requireUsdcParityCurrency(await this.get(options));
  }

  private async fetch(): Promise<string> {
    const data = await this.graphql<StoreCurrencyData>(
      STORE_CURRENCY_QUERY,
      {},
    );
    const currency = data.shop?.currencyCode?.trim().toUpperCase();
    if (!currency) {
      throw new Error(
        "Shopify Admin API returned no shop.currencyCode; refusing to price in USDC",
      );
    }
    this.cached = {
      currency,
      expiresAtMs: this.now() + this.cacheTtlMs,
    };
    return currency;
  }
}
