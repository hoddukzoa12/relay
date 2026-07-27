import assert from "node:assert/strict";
import test from "node:test";
import { Keypair } from "@solana/web3.js";
import {
  DelegationRefusedError,
  approvalInstructionMatches,
  assertDelegationAllowance,
  delegationPaymentMode,
} from "./delegation-policy.js";

const delegator = Keypair.generate().publicKey;
const delegateAuthority = Keypair.generate().publicKey;

test("an omitted delegator preserves the autonomous agent-wallet path", () => {
  assert.equal(delegationPaymentMode(), "agent-wallet");
  assert.equal(delegationPaymentMode(delegator.toBase58()), "spl-delegate");
});

test("the exact on-chain delegate and allowance authorize a transfer", () => {
  assert.doesNotThrow(() =>
    assertDelegationAllowance(
      {
        owner: delegator,
        amount: 20_000_000n,
        delegate: delegateAuthority,
        delegatedAmount: 10_000_000n,
      },
      delegator,
      delegateAuthority,
      5_000_000n,
    ),
  );
});

test("an over-limit delegated transfer is refused before signing", () => {
  assert.throws(
    () =>
      assertDelegationAllowance(
        {
          owner: delegator,
          amount: 20_000_000n,
          delegate: delegateAuthority,
          delegatedAmount: 4_999_999n,
        },
        delegator,
        delegateAuthority,
        5_000_000n,
      ),
    (error: unknown) =>
      error instanceof DelegationRefusedError &&
      /allowance.*below/i.test(error.message),
  );
});

test("a revoked or wrong delegate is refused before signing", () => {
  for (const delegate of [null, Keypair.generate().publicKey]) {
    assert.throws(
      () =>
        assertDelegationAllowance(
          {
            owner: delegator,
            amount: 20_000_000n,
            delegate,
            delegatedAmount: 10_000_000n,
          },
          delegator,
          delegateAuthority,
          5_000_000n,
        ),
      (error: unknown) =>
        error instanceof DelegationRefusedError &&
        /missing or revoked/i.test(error.message),
    );
  }
});

test("approval proof must bind owner, delegate, source ATA, and mint", () => {
  const expected = {
    delegator: delegator.toBase58(),
    delegateAuthority: delegateAuthority.toBase58(),
    sourceTokenAccount: Keypair.generate().publicKey.toBase58(),
    mint: Keypair.generate().publicKey.toBase58(),
  };
  const instruction = {
    program: "spl-token",
    parsed: {
      type: "approveChecked",
      info: {
        owner: expected.delegator,
        delegate: expected.delegateAuthority,
        source: expected.sourceTokenAccount,
        mint: expected.mint,
      },
    },
  };
  assert.equal(approvalInstructionMatches(instruction, expected), true);
  assert.equal(
    approvalInstructionMatches(
      {
        ...instruction,
        parsed: {
          ...instruction.parsed,
          info: {
            ...instruction.parsed.info,
            delegate: Keypair.generate().publicKey.toBase58(),
          },
        },
      },
      expected,
    ),
    false,
  );
});
