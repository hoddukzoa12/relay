import { config } from "./config.js";
import { ShopifyAdminClient } from "./shopify-client.js";

const shopifyAdmin = new ShopifyAdminClient(config.shopify);

export async function shopifyGraphQL<T>(
  query: string,
  variables: Record<string, unknown>,
): Promise<T> {
  return shopifyAdmin.graphql<T>(query, variables);
}
