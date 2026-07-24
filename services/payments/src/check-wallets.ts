/**
 * `pnpm --filter @arb/payments check-wallets`
 * Prints SOL + USDC balances for the merchant and buyer wallets so you can
 * confirm your devnet airdrops landed before running the demo.
 */
import { getAccount } from "@solana/spl-token";
import { LAMPORTS_PER_SOL } from "@solana/web3.js";
import { buyer, connection, merchant, usdcAta, usdcDecimals, usdcMint } from "./solana.js";

async function report(label: string, owner: import("@solana/web3.js").PublicKey) {
  const sol = (await connection.getBalance(owner)) / LAMPORTS_PER_SOL;
  let usdc = "0 (no token account yet)";
  try {
    const ata = await usdcAta(owner);
    const acct = await getAccount(connection, ata);
    usdc = (Number(acct.amount) / 10 ** usdcDecimals).toString();
  } catch {
    /* no ATA yet */
  }
  console.log(`\n${label}`);
  console.log(`  pubkey : ${owner.toBase58()}`);
  console.log(`  SOL    : ${sol}`);
  console.log(`  USDC   : ${usdc}`);
}

(async () => {
  console.log(`USDC mint: ${usdcMint.toBase58()}`);
  await report("MERCHANT (shopping agent)", merchant.publicKey);
  await report("BUYER (general agent)", buyer.publicKey);
  console.log("");
})();
