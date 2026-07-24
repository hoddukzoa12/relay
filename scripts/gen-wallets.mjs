#!/usr/bin/env node

import { mkdir, writeFile } from "node:fs/promises";
import { createRequire } from "node:module";

// Resolve the workspace dependency from the payments package. pnpm intentionally
// does not hoist @solana/web3.js to the repository root.
const require = createRequire(
  new URL("../services/payments/package.json", import.meta.url),
);
const { Connection, Keypair, LAMPORTS_PER_SOL } = require("@solana/web3.js");

const RPC_URL =
  process.env.SOLANA_RPC_URL ?? "https://api.devnet.solana.com";
const AIRDROP_SOL = Number(process.env.AIRDROP_SOL ?? "0.1");
const AIRDROP_RETRIES = 5;
const walletsDir = new URL("../wallets/", import.meta.url);
const connection = new Connection(RPC_URL, "confirmed");

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

async function writeKeypair(name, keypair) {
  const path = new URL(`${name}.json`, walletsDir);
  await writeFile(path, `${JSON.stringify([...keypair.secretKey])}\n`, {
    mode: 0o600,
  });
}

async function airdrop(name, publicKey) {
  for (let attempt = 1; attempt <= AIRDROP_RETRIES; attempt += 1) {
    try {
      const signature = await connection.requestAirdrop(
        publicKey,
        AIRDROP_SOL * LAMPORTS_PER_SOL,
      );
      const latestBlockhash = await connection.getLatestBlockhash("confirmed");
      const confirmation = await connection.confirmTransaction(
        { signature, ...latestBlockhash },
        "confirmed",
      );
      if (confirmation.value.err) {
        throw new Error(JSON.stringify(confirmation.value.err));
      }
      console.log(`${name} SOL airdrop: ${signature}`);
      return;
    } catch (error) {
      if (attempt === AIRDROP_RETRIES) throw error;
      console.warn(
        `${name} airdrop attempt ${attempt} failed; retrying: ${String(error)}`,
      );
      await sleep(attempt * 2_000);
    }
  }
}

await mkdir(walletsDir, { recursive: true });

const merchant = Keypair.generate();
const buyer = Keypair.generate();
await Promise.all([
  writeKeypair("merchant", merchant),
  writeKeypair("buyer", buyer),
]);

console.log(`RPC:      ${RPC_URL}`);
console.log(`Merchant: ${merchant.publicKey.toBase58()}`);
console.log(`Buyer:    ${buyer.publicKey.toBase58()}`);

// Run sequentially to be polite to the rate-limited public devnet faucet.
await airdrop("Merchant", merchant.publicKey);
await airdrop("Buyer", buyer.publicKey);

console.log("\nFresh devnet wallets are ready.");
console.log(
  `Fund buyer ${buyer.publicKey.toBase58()} with Circle devnet USDC:`,
);
console.log("https://faucet.circle.com (select Solana Devnet)");
