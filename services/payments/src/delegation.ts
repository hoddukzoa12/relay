import {
  PublicKey,
  Transaction,
  type Commitment,
} from "@solana/web3.js";
import {
  TokenAccountNotFoundError,
  createApproveCheckedInstruction,
  createRevokeInstruction,
  getAccount,
  getAssociatedTokenAddress,
  type Account,
} from "@solana/spl-token";
import type {
  DelegationStatus,
  DelegationTransaction,
} from "@arb/shared";
import {
  DelegationRefusedError,
  approvalInstructionMatches,
  assertDelegationAllowance,
} from "./delegation-policy.js";
import {
  assertUsdcDecimals,
  buyer,
  connection,
  toBaseUnits,
  usdcDecimals,
  usdcMint,
  withFailover,
} from "./solana.js";
import { config } from "./config.js";

const COMMITMENT: Commitment = "confirmed";

function decimalAmount(amount: bigint): string {
  const divisor = 10n ** BigInt(usdcDecimals);
  const whole = amount / divisor;
  const fraction = (amount % divisor)
    .toString()
    .padStart(usdcDecimals, "0")
    .replace(/0+$/, "");
  return fraction ? `${whole}.${fraction}` : whole.toString();
}

function network(): DelegationStatus["network"] {
  return config.cluster === "mainnet" ? "solana-mainnet" : "solana-devnet";
}

async function readAccount(
  sourceTokenAccount: PublicKey,
): Promise<Account | null> {
  try {
    return await withFailover((rpc) =>
      getAccount(rpc, sourceTokenAccount, COMMITMENT),
    );
  } catch (error) {
    if (error instanceof TokenAccountNotFoundError) return null;
    throw error;
  }
}

export async function delegationStatus(
  delegatorAddress: string,
): Promise<DelegationStatus> {
  const delegator = new PublicKey(delegatorAddress);
  const sourceTokenAccount = await getAssociatedTokenAddress(usdcMint, delegator);
  const account = await readAccount(sourceTokenAccount);
  const active = Boolean(
    account?.delegate?.equals(buyer.publicKey) &&
      account.delegatedAmount > 0n,
  );
  return {
    active,
    delegator: delegator.toBase58(),
    delegateAuthority: buyer.publicKey.toBase58(),
    allowanceRemaining: {
      amount: decimalAmount(active ? account?.delegatedAmount ?? 0n : 0n),
      currency: "USDC",
    },
    balance: {
      amount: decimalAmount(account?.amount ?? 0n),
      currency: "USDC",
    },
    sourceTokenAccount: sourceTokenAccount.toBase58(),
    usdcMint: usdcMint.toBase58(),
    network: network(),
  };
}

export async function delegatedSourceAccount(
  delegatorAddress: string,
  requiredAmount: bigint,
): Promise<PublicKey> {
  const delegator = new PublicKey(delegatorAddress);
  const sourceTokenAccount = await getAssociatedTokenAddress(usdcMint, delegator);
  const account = await readAccount(sourceTokenAccount);
  if (!account) {
    throw new DelegationRefusedError(
      "The signed-in wallet has no USDC token account; fund it before approval.",
    );
  }
  assertDelegationAllowance(
    account,
    delegator,
    buyer.publicKey,
    requiredAmount,
  );
  return sourceTokenAccount;
}

export async function verifyDelegationApproval(
  delegatorAddress: string,
  approvalTxSignature: string,
): Promise<DelegationStatus> {
  const status = await delegationStatus(delegatorAddress);
  if (!status.active) {
    throw new DelegationRefusedError(
      "SPL delegation is missing or revoked; approve the broker again.",
    );
  }
  const transaction = await withFailover((rpc) =>
    rpc.getParsedTransaction(approvalTxSignature, {
      commitment: "confirmed",
      maxSupportedTransactionVersion: 0,
    }),
  );
  if (
    !transaction ||
    transaction.meta?.err ||
    !transaction.transaction.message.instructions.some((instruction) =>
      approvalInstructionMatches(instruction, {
        delegator: status.delegator,
        delegateAuthority: status.delegateAuthority,
        sourceTokenAccount: status.sourceTokenAccount,
        mint: status.usdcMint,
      }),
    )
  ) {
    throw new DelegationRefusedError(
      "The approval transaction does not authorize this Clerk wallet; approve again.",
    );
  }
  return status;
}

export async function prepareDelegationTransaction(input: {
  action: "approve" | "revoke";
  delegator: string;
  amount?: string;
}): Promise<DelegationTransaction> {
  await assertUsdcDecimals();
  const delegator = new PublicKey(input.delegator);
  const sourceTokenAccount = await getAssociatedTokenAddress(usdcMint, delegator);
  const account = await readAccount(sourceTokenAccount);
  if (!account) {
    throw new DelegationRefusedError(
      "The signed-in wallet has no USDC token account; fund it before approval.",
    );
  }
  if (!account.owner.equals(delegator)) {
    throw new DelegationRefusedError(
      "The USDC token account is not owned by the signed-in wallet.",
    );
  }

  let allowance = 0n;
  const transaction = new Transaction();
  if (input.action === "approve") {
    if (!input.amount) {
      throw new DelegationRefusedError("An approval amount is required.");
    }
    allowance = toBaseUnits(input.amount);
    transaction.add(
      createApproveCheckedInstruction(
        sourceTokenAccount,
        usdcMint,
        buyer.publicKey,
        delegator,
        allowance,
        usdcDecimals,
      ),
    );
  } else {
    transaction.add(createRevokeInstruction(sourceTokenAccount, delegator));
  }

  const latest = await connection.getLatestBlockhash(COMMITMENT);
  transaction.feePayer = buyer.publicKey;
  transaction.recentBlockhash = latest.blockhash;
  transaction.partialSign(buyer);
  return {
    action: input.action,
    delegator: delegator.toBase58(),
    delegateAuthority: buyer.publicKey.toBase58(),
    allowanceRemaining: {
      amount: decimalAmount(allowance),
      currency: "USDC",
    },
    transaction: transaction
      .serialize({ requireAllSignatures: false, verifySignatures: false })
      .toString("base64"),
    blockhash: latest.blockhash,
    lastValidBlockHeight: latest.lastValidBlockHeight,
  };
}

export { DelegationRefusedError } from "./delegation-policy.js";
