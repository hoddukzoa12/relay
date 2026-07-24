import { config as loadEnv } from "dotenv";
import { existsSync } from "node:fs";
import { resolve } from "node:path";
import { fileURLToPath } from "node:url";

const here = fileURLToPath(new URL(".", import.meta.url));
for (const p of [resolve(here, "../../../.env"), resolve(process.cwd(), ".env")]) {
  if (existsSync(p)) loadEnv({ path: p });
}

const token = process.env.SHOPIFY_ADMIN_ACCESS_TOKEN ?? "";

export const config = {
  port: Number(process.env.COMMERCE_PORT ?? 8082),
  // Mock unless explicitly disabled AND a token is present.
  mock: process.env.COMMERCE_MOCK !== "false" || !token,
  shopify: {
    domain: process.env.SHOPIFY_STORE_DOMAIN ?? "",
    token,
    apiVersion: process.env.SHOPIFY_API_VERSION ?? "2025-01",
    currency: process.env.SHOPIFY_CURRENCY ?? "USD",
  },
  cluster: process.env.SOLANA_CLUSTER ?? "devnet",
};
