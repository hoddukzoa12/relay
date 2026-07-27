/**
 * Devnet-only evidence helper for issue #42.
 *
 * It creates a throwaway human wallet (git-ignored), funds its USDC ATA from
 * the configured buyer wallet, and submits exactly one SPL Approve transaction
 * with the buyer agent as fee payer/delegate. `revoke` signs a matching Revoke.
 * No local allowance is tracked: every printed value is read from the ATA.
 */
import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import {
  Keypair,
  Transaction,
  sendAndConfirmTransaction,
} from "@solana/web3.js";
import {
  createApproveCheckedInstruction,
  createRevokeInstruction,
  createTransferCheckedInstruction,
  getAccount,
} from "@solana/spl-token";
import {
  assertUsdcDecimals,
  buyer,
  connection,
  ensureAta,
  toBaseUnits,
  usdcDecimals,
  usdcMint,
} from "./solana.js";
import { delegationStatus } from "./delegation.js";

const keypairPath = resolve(
  process.cwd(),
  process.env.DELEGATOR_WALLET_KEYPAIR_PATH ?? "../../wallets/delegator.json",
);
const action = process.argv[2] ?? "setup";
const fundingAmount = process.env.DELEGATOR_FUNDING_USDC ?? "3.00";
const approvalAmount = process.env.DELEGATION_LIMIT_USDC ?? "2.50";

function loadOrCreateDelegator(): Keypair {
  if (existsSync(keypairPath)) {
    const secret = JSON.parse(readFileSync(keypairPath, "utf8")) as number[];
    return Keypair.fromSecretKey(Uint8Array.from(secret));
  }
  const generated = Keypair.generate();
  mkdirSync(dirname(keypairPath), { recursive: true });
  writeFileSync(keypairPath, JSON.stringify([...generated.secretKey]) + "\n", {
    mode: 0o600,
  });
  return generated;
}

async function tokenBalance(
  tokenAccount: Awaited<ReturnType<typeof ensureAta>>["address"],
): Promise<string> {
  const account = await getAccount(connection, tokenAccount, "confirmed");
  const divisor = 10n ** BigInt(usdcDecimals);
  const whole = account.amount / divisor;
  const fraction = (account.amount % divisor)
    .toString()
    .padStart(usdcDecimals, "0")
    .replace(/0+$/, "");
  return fraction ? `${whole}.${fraction}` : whole.toString();
}

async function main(): Promise<void> {
  if (process.env.SOLANA_CLUSTER !== "devnet") {
    throw new Error("This evidence helper refuses to run outside devnet");
  }
  await assertUsdcDecimals();
  const delegator = loadOrCreateDelegator();
  const source = await ensureAta(buyer, delegator.publicKey);

  if (action === "setup") {
    const target = toBaseUnits(fundingAmount);
    const current = (await getAccount(connection, source.address, "confirmed")).amount;
    let fundingTxSignature: string | null = null;
    if (current < target) {
      const buyerAta = await ensureAta(buyer, buyer.publicKey);
      const transaction = new Transaction().add(
        createTransferCheckedInstruction(
          buyerAta.address,
          usdcMint,
          source.address,
          buyer.publicKey,
          target - current,
          usdcDecimals,
        ),
      );
      transaction.feePayer = buyer.publicKey;
      fundingTxSignature = await sendAndConfirmTransaction(
        connection,
        transaction,
        [buyer],
        { commitment: "confirmed", preflightCommitment: "confirmed" },
      );
    }

    const approve = new Transaction().add(
      createApproveCheckedInstruction(
        source.address,
        usdcMint,
        buyer.publicKey,
        delegator.publicKey,
        toBaseUnits(approvalAmount),
        usdcDecimals,
      ),
    );
    approve.feePayer = buyer.publicKey;
    const approvalTxSignature = await sendAndConfirmTransaction(
      connection,
      approve,
      [buyer, delegator],
      { commitment: "confirmed", preflightCommitment: "confirmed" },
    );
    console.log(
      JSON.stringify(
        {
          action,
          delegator: delegator.publicKey.toBase58(),
          userSolLamports: await connection.getBalance(delegator.publicKey),
          sourceTokenAccount: source.address.toBase58(),
          balance: await tokenBalance(source.address),
          fundingTxSignature,
          approvalTxSignature,
          approvalExplorer:
            `https://explorer.solana.com/tx/${approvalTxSignature}?cluster=devnet`,
          status: await delegationStatus(delegator.publicKey.toBase58()),
        },
        null,
        2,
      ),
    );
    return;
  }

  if (action === "revoke") {
    const revoke = new Transaction().add(
      createRevokeInstruction(source.address, delegator.publicKey),
    );
    revoke.feePayer = buyer.publicKey;
    const revokeTxSignature = await sendAndConfirmTransaction(
      connection,
      revoke,
      [buyer, delegator],
      { commitment: "confirmed", preflightCommitment: "confirmed" },
    );
    console.log(
      JSON.stringify(
        {
          action,
          delegator: delegator.publicKey.toBase58(),
          balance: await tokenBalance(source.address),
          revokeTxSignature,
          revokeExplorer:
            `https://explorer.solana.com/tx/${revokeTxSignature}?cluster=devnet`,
          status: await delegationStatus(delegator.publicKey.toBase58()),
        },
        null,
        2,
      ),
    );
    return;
  }

  if (action === "status") {
    console.log(
      JSON.stringify(
        {
          action,
          delegator: delegator.publicKey.toBase58(),
          balance: await tokenBalance(source.address),
          status: await delegationStatus(delegator.publicKey.toBase58()),
        },
        null,
        2,
      ),
    );
    return;
  }

  throw new Error(`Unknown action: ${action}`);
}

await main();
