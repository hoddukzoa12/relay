import { PublicKey } from "@solana/web3.js";

export class DelegationRefusedError extends Error {
  readonly reapprovalRequired = true;
}

export interface DelegationAccountSnapshot {
  owner: PublicKey;
  amount: bigint;
  delegate: PublicKey | null;
  delegatedAmount: bigint;
}

export function delegationPaymentMode(
  delegator?: string,
): "agent-wallet" | "spl-delegate" {
  return delegator ? "spl-delegate" : "agent-wallet";
}

/**
 * Enforce the on-chain account as the allowance SSOT. Signed intent snapshots
 * and client values are deliberately absent from this check.
 */
export function assertDelegationAllowance(
  account: DelegationAccountSnapshot,
  delegator: PublicKey,
  delegateAuthority: PublicKey,
  requiredAmount: bigint,
): void {
  if (!account.owner.equals(delegator)) {
    throw new DelegationRefusedError(
      "The USDC token account is not owned by the signed-in wallet; approve again.",
    );
  }
  if (!account.delegate?.equals(delegateAuthority)) {
    throw new DelegationRefusedError(
      "SPL delegation is missing or revoked; approve the broker again.",
    );
  }
  if (account.delegatedAmount < requiredAmount) {
    throw new DelegationRefusedError(
      "SPL delegated allowance is below this purchase amount; approve a higher limit.",
    );
  }
  if (account.amount < requiredAmount) {
    throw new DelegationRefusedError(
      "The signed-in wallet has insufficient USDC for this purchase.",
    );
  }
}

export function approvalInstructionMatches(
  instruction: unknown,
  expected: {
    delegator: string;
    delegateAuthority: string;
    sourceTokenAccount: string;
    mint: string;
  },
): boolean {
  if (!instruction || typeof instruction !== "object") return false;
  const candidate = instruction as {
    program?: unknown;
    parsed?: { type?: unknown; info?: Record<string, unknown> };
  };
  if (
    candidate.program !== "spl-token" ||
    !["approve", "approveChecked"].includes(String(candidate.parsed?.type))
  ) {
    return false;
  }
  const info = candidate.parsed?.info;
  return Boolean(
    info &&
      info.owner === expected.delegator &&
      info.delegate === expected.delegateAuthority &&
      info.source === expected.sourceTokenAccount &&
      (info.mint === undefined || info.mint === expected.mint),
  );
}
