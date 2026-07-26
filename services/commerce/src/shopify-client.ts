import { z } from "zod";

const TokenResponseSchema = z.object({
  access_token: z.string().min(1),
  scope: z.string().optional(),
  expires_in: z.coerce.number().positive(),
});

export interface ShopifyAuthConfig {
  domain: string;
  adminAccessToken?: string;
  clientId?: string;
  clientSecret?: string;
}

export interface ShopifyClientConfig extends ShopifyAuthConfig {
  apiVersion: string;
}

export interface ShopifyClientDependencies {
  fetch?: typeof fetch;
  now?: () => number;
  refreshMarginMs?: number;
}

interface CachedToken {
  value: string;
  expiresAtMs: number;
}

interface AccessTokenOptions {
  forceRefresh?: boolean;
  rejectedToken?: string;
}

interface GraphQLError {
  message: string;
}

const DEFAULT_REFRESH_MARGIN_MS = 60_000;

/**
 * Supplies Shopify Admin API access tokens.
 *
 * A configured static token always wins for legacy stores. Otherwise, client
 * credentials are exchanged for a short-lived token and cached in memory.
 */
export class ShopifyTokenProvider {
  private readonly fetchImpl: typeof fetch;
  private readonly now: () => number;
  private readonly refreshMarginMs: number;
  private cachedToken: CachedToken | undefined;
  private inFlightRequest: Promise<string> | undefined;

  constructor(
    private readonly auth: ShopifyAuthConfig,
    dependencies: ShopifyClientDependencies = {},
  ) {
    this.fetchImpl = dependencies.fetch ?? globalThis.fetch;
    this.now = dependencies.now ?? Date.now;
    this.refreshMarginMs =
      dependencies.refreshMarginMs ?? DEFAULT_REFRESH_MARGIN_MS;
  }

  async getAccessToken(options: AccessTokenOptions = {}): Promise<string> {
    if (this.auth.adminAccessToken) {
      return this.auth.adminAccessToken;
    }

    this.requireClientCredentials();

    if (this.inFlightRequest) {
      return this.inFlightRequest;
    }

    const cachedIsFresh =
      this.cachedToken !== undefined &&
      this.now() < this.cachedToken.expiresAtMs - this.refreshMarginMs;
    if (!options.forceRefresh && cachedIsFresh) {
      return this.cachedToken!.value;
    }

    // A concurrent request may already have refreshed the token that received
    // the 401. Reuse that replacement rather than issuing another grant.
    if (
      options.forceRefresh &&
      options.rejectedToken &&
      cachedIsFresh &&
      this.cachedToken!.value !== options.rejectedToken
    ) {
      return this.cachedToken!.value;
    }

    const request = this.requestClientCredentialsToken();
    this.inFlightRequest = request;
    try {
      return await request;
    } finally {
      if (this.inFlightRequest === request) {
        this.inFlightRequest = undefined;
      }
    }
  }

  private requireClientCredentials(): void {
    if (!this.auth.domain) {
      throw new Error("SHOPIFY_STORE_DOMAIN is required for Shopify Admin API");
    }
    if (!this.auth.clientId || !this.auth.clientSecret) {
      throw new Error(
        "Shopify authentication requires SHOPIFY_ADMIN_ACCESS_TOKEN or both SHOPIFY_CLIENT_ID and SHOPIFY_CLIENT_SECRET",
      );
    }
  }

  private async requestClientCredentialsToken(): Promise<string> {
    const response = await this.fetchImpl(
      `https://${this.auth.domain}/admin/oauth/access_token`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          grant_type: "client_credentials",
          client_id: this.auth.clientId,
          client_secret: this.auth.clientSecret,
        }),
      },
    );

    if (!response.ok) {
      // Deliberately omit the response body: authentication failures must
      // never risk reflecting credentials into service logs.
      throw new Error(`Shopify token request failed with HTTP ${response.status}`);
    }

    let payload: unknown;
    try {
      payload = await response.json();
    } catch {
      throw new Error("Shopify token response was not valid JSON");
    }

    const parsed = TokenResponseSchema.safeParse(payload);
    if (!parsed.success) {
      throw new Error("Shopify token response was missing required fields");
    }

    this.cachedToken = {
      value: parsed.data.access_token,
      expiresAtMs: this.now() + parsed.data.expires_in * 1_000,
    };
    return parsed.data.access_token;
  }
}

export class ShopifyAdminClient {
  private readonly fetchImpl: typeof fetch;
  private readonly endpoint: string;
  private readonly tokenProvider: ShopifyTokenProvider;

  constructor(
    config: ShopifyClientConfig,
    dependencies: ShopifyClientDependencies = {},
  ) {
    this.fetchImpl = dependencies.fetch ?? globalThis.fetch;
    this.endpoint = `https://${config.domain}/admin/api/${config.apiVersion}/graphql.json`;
    this.tokenProvider = new ShopifyTokenProvider(config, dependencies);
  }

  async graphql<T>(
    query: string,
    variables: Record<string, unknown> = {},
  ): Promise<T> {
    let accessToken = await this.tokenProvider.getAccessToken();
    let response = await this.sendGraphQL(query, variables, accessToken);

    if (response.status === 401) {
      accessToken = await this.tokenProvider.getAccessToken({
        forceRefresh: true,
        rejectedToken: accessToken,
      });
      response = await this.sendGraphQL(query, variables, accessToken);
    }

    if (!response.ok) {
      throw new Error(
        `Shopify HTTP ${response.status}: ${await response.text()}`,
      );
    }

    const json = (await response.json()) as {
      data?: T;
      errors?: GraphQLError[];
    };
    if (json.errors?.length) {
      throw new Error(
        `Shopify GraphQL: ${json.errors.map((error) => error.message).join("; ")}`,
      );
    }
    return json.data as T;
  }

  private sendGraphQL(
    query: string,
    variables: Record<string, unknown>,
    accessToken: string,
  ): Promise<Response> {
    return this.fetchImpl(this.endpoint, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-Shopify-Access-Token": accessToken,
      },
      body: JSON.stringify({ query, variables }),
    });
  }
}
