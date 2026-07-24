import { config as loadEnv } from "dotenv";
import { existsSync, readFileSync } from "node:fs";
import { resolve, isAbsolute } from "node:path";
import { fileURLToPath } from "node:url";

// Load .env from the repo root (two levels up from services/payments) then cwd.
const here = fileURLToPath(new URL(".", import.meta.url));
for (const p of [resolve(here, "../../../.env"), resolve(process.cwd(), ".env")]) {
  if (existsSync(p)) loadEnv({ path: p });
}

function req(name: string): string {
  const v = process.env[name];
  if (!v) throw new Error(`Missing required env var: ${name}`);
  return v;
}

export const config = {
  port: Number(process.env.PAYMENTS_PORT ?? 8081),
  rpcUrl: process.env.SOLANA_RPC_URL ?? "https://api.devnet.solana.com",
  rpcUrlFallback: process.env.SOLANA_RPC_URL_FALLBACK ?? "",
  cluster: process.env.SOLANA_CLUSTER ?? "devnet",
  usdcMint: process.env.USDC_MINT ?? "4zMMC9srt5Ri5X14GAgXhaHii3GnPAEERYPJgZJDncDU",
  usdcDecimals: Number(process.env.USDC_DECIMALS ?? 6),
  paymentTtlMin: Number(process.env.PAYMENT_REQUEST_TTL_MIN ?? 15),
  merchant: {
    keypairPath: process.env.MERCHANT_WALLET_KEYPAIR_PATH,
    secret: process.env.MERCHANT_WALLET_SECRET,
  },
  buyer: {
    keypairPath: process.env.BUYER_WALLET_KEYPAIR_PATH,
    secret: process.env.BUYER_WALLET_SECRET,
  },
};

/** Resolve a keypair-path relative to the repo root if it isn't absolute. */
export function resolveFromRoot(p: string): string {
  return isAbsolute(p) ? p : resolve(here, "../../../", p);
}

export function readKeypairFile(path: string): number[] {
  return JSON.parse(readFileSync(resolveFromRoot(path), "utf8")) as number[];
}

export { req };
