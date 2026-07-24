import {
  Connection,
  Keypair,
  PublicKey,
  type Commitment,
} from "@solana/web3.js";
import {
  getAssociatedTokenAddress,
  getOrCreateAssociatedTokenAccount,
  type Account,
} from "@solana/spl-token";
import bs58 from "bs58";
import { config, readKeypairFile } from "./config.js";

const COMMITMENT: Commitment = "confirmed";

/**
 * A Connection with a single fallback endpoint. `withFailover` retries a
 * read/RPC operation against the fallback if the primary throws — this is the
 * "RPC 다중화" risk-mitigation from PRD §10.
 */
export const connection = new Connection(config.rpcUrl, COMMITMENT);
const fallback = config.rpcUrlFallback
  ? new Connection(config.rpcUrlFallback, COMMITMENT)
  : null;

export async function withFailover<T>(
  fn: (c: Connection) => Promise<T>,
): Promise<T> {
  try {
    return await fn(connection);
  } catch (err) {
    if (!fallback) throw err;
    console.warn("[solana] primary RPC failed, retrying on fallback:", String(err));
    return await fn(fallback);
  }
}

export const usdcMint = new PublicKey(config.usdcMint);
export const usdcDecimals = config.usdcDecimals;

/** Load a keypair from either a base58 secret or a solana-keygen id.json path. */
function loadKeypair(
  which: "merchant" | "buyer",
  src: { keypairPath?: string; secret?: string },
): Keypair {
  if (src.secret) {
    return Keypair.fromSecretKey(bs58.decode(src.secret));
  }
  if (src.keypairPath) {
    return Keypair.fromSecretKey(Uint8Array.from(readKeypairFile(src.keypairPath)));
  }
  throw new Error(
    `No key configured for ${which}. Set ${which.toUpperCase()}_WALLET_KEYPAIR_PATH ` +
      `or ${which.toUpperCase()}_WALLET_SECRET in .env.`,
  );
}

export const merchant = loadKeypair("merchant", config.merchant);
export const buyer = loadKeypair("buyer", config.buyer);

export function usdcAta(owner: PublicKey): Promise<PublicKey> {
  return getAssociatedTokenAddress(usdcMint, owner);
}

/**
 * Ensure `owner` has a USDC associated token account, creating it if missing.
 * `payer` funds the (one-time) rent. Returns the token account.
 */
export function ensureAta(payer: Keypair, owner: PublicKey): Promise<Account> {
  return getOrCreateAssociatedTokenAccount(connection, payer, usdcMint, owner);
}

/** Convert a decimal USDC string ("25.00") to a base-unit bigint. */
export function toBaseUnits(amount: string): bigint {
  const [whole, frac = ""] = amount.split(".");
  const padded = (frac + "0".repeat(usdcDecimals)).slice(0, usdcDecimals);
  return BigInt(whole + padded);
}
