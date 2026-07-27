import { config as loadEnv } from "dotenv";
import { existsSync } from "node:fs";
import { resolve } from "node:path";
import { fileURLToPath } from "node:url";

const here = fileURLToPath(new URL(".", import.meta.url));
for (const p of [resolve(here, "../../../.env"), resolve(process.cwd(), ".env")]) {
  if (existsSync(p)) loadEnv({ path: p });
}

const adminAccessToken = process.env.SHOPIFY_ADMIN_ACCESS_TOKEN ?? "";
const clientId = process.env.SHOPIFY_CLIENT_ID ?? "";
const clientSecret = process.env.SHOPIFY_CLIENT_SECRET ?? "";
const hasShopifyAuth = Boolean(
  adminAccessToken || (clientId && clientSecret),
);

export const config = {
  port: Number(process.env.COMMERCE_PORT ?? 8082),
  // Mock unless explicitly disabled AND one complete auth path is present.
  mock: process.env.COMMERCE_MOCK !== "false" || !hasShopifyAuth,
  shopify: {
    domain: process.env.SHOPIFY_STORE_DOMAIN ?? "",
    adminAccessToken,
    clientId,
    clientSecret,
    apiVersion: process.env.SHOPIFY_API_VERSION ?? "2025-01",
  },
  cluster: process.env.SOLANA_CLUSTER ?? "devnet",
  tracking: {
    easypostApiKey: process.env.EASYPOST_API_KEY ?? "",
  },
};
