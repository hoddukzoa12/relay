#!/usr/bin/env node

import { readFile } from "node:fs/promises";

const ALPHABET =
  "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz";

function encodeBase58(bytes) {
  let value = 0n;
  for (const byte of bytes) {
    value = value * 256n + BigInt(byte);
  }

  let encoded = "";
  while (value > 0n) {
    encoded = ALPHABET[Number(value % 58n)] + encoded;
    value /= 58n;
  }

  for (const byte of bytes) {
    if (byte !== 0) break;
    encoded = `1${encoded}`;
  }
  return encoded || "1";
}

async function readSecret(path) {
  const parsed = JSON.parse(await readFile(path, "utf8"));
  if (
    !Array.isArray(parsed) ||
    parsed.length !== 64 ||
    parsed.some(
      (value) =>
        !Number.isInteger(value) ||
        value < 0 ||
        value > 255,
    )
  ) {
    throw new Error(`${path} is not a 64-byte Solana keypair JSON array`);
  }
  return encodeBase58(parsed);
}

const merchantPath = process.argv[2] ?? "wallets/merchant.json";
const buyerPath = process.argv[3] ?? "wallets/buyer.json";
const bundle = {
  MERCHANT_WALLET_SECRET: await readSecret(merchantPath),
  BUYER_WALLET_SECRET: await readSecret(buyerPath),
};

process.stdout.write(JSON.stringify(bundle));
