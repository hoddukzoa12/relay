import {
  Keypair,
  PublicKey,
  Transaction,
} from "@solana/web3.js";
import { createTransferCheckedInstruction } from "@solana/spl-token";
import {
  encodeURL,
  findReference,
  validateTransfer,
  FindReferenceError,
  ValidateTransferError,
} from "@solana/pay";
import BigNumber from "bignumber.js";
import bs58 from "bs58";
import {
  explorerTxUrl,
  type PaymentRequest,
  type VerificationFailureReasonT,
  type VerificationResult,
} from "@arb/shared";
import { config } from "./config.js";
import {
  assertUsdcDecimals,
  buyer,
  connection,
  ensureAta,
  merchant,
  toBaseUnits,
  usdcDecimals,
  usdcMint,
  withFailover,
} from "./solana.js";
import { store, type StoredRequest } from "./store.js";
import { delegatedSourceAccount } from "./delegation.js";

const CLUSTER = config.cluster;
const VERIFY_TIMEOUT_MS = 20_000;
const VERIFY_POLL_MS = 1_000;

const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

export interface PayResult {
  status: "pending" | "paid";
  txSignature: string;
  explorer: string;
  reason: "broadcast_uncertain" | "confirmation_uncertain" | null;
}

type ReconciliationResult = "failed" | "paid" | "pending";
const inFlightPayments = new Map<string, Promise<PayResult>>();
const inFlightRefunds = new Map<string, Promise<RefundTransferResult>>();

export class RefundRefusedError extends Error {}

function paidResult(signature: string): PayResult {
  return {
    status: "paid",
    txSignature: signature,
    explorer: explorerTxUrl(signature, CLUSTER),
    reason: null,
  };
}

function pendingResult(
  signature: string,
  reason: Exclude<PayResult["reason"], null>,
): PayResult {
  return {
    status: "pending",
    txSignature: signature,
    explorer: explorerTxUrl(signature, CLUSTER),
    reason,
  };
}

/**
 * Confirmation errors are ambiguous: the RPC can time out after the transfer
 * lands. Reconcile both the deterministic transaction signature and the
 * Solana Pay reference before returning, and never reopen the reference here.
 */
async function reconcileSubmittedPayment(
  stored: StoredRequest,
  signature: string,
): Promise<ReconciliationResult> {
  const referencePk = new PublicKey(stored.reference);
  const [statusResult, referenceResult] = await Promise.allSettled([
    withFailover((c) =>
      c.getSignatureStatuses([signature], { searchTransactionHistory: true }),
    ),
    withFailover(
      (c) => findReference(c, referencePk, { finality: "confirmed" }),
      (err) => !(err instanceof FindReferenceError),
    ).catch((err: unknown) => {
      if (err instanceof FindReferenceError) return null;
      throw err;
    }),
  ]);

  const signatureStatus =
    statusResult.status === "fulfilled" ? statusResult.value.value[0] : null;
  const referenceInfo =
    referenceResult.status === "fulfilled" ? referenceResult.value : null;

  if (statusResult.status === "rejected") {
    console.warn(
      `[pay] signature-status reconciliation failed for ${signature}:`,
      String(statusResult.reason),
    );
  }
  if (referenceResult.status === "rejected") {
    console.warn(
      `[pay] reference reconciliation failed for ${stored.reference}:`,
      String(referenceResult.reason),
    );
  }

  if (
    signatureStatus?.err ||
    (referenceInfo?.signature === signature && referenceInfo.err)
  ) {
    return "failed";
  }
  if (
    signatureStatus?.confirmationStatus === "confirmed" ||
    signatureStatus?.confirmationStatus === "finalized" ||
    referenceInfo?.signature === signature
  ) {
    store.markPaid(stored.reference, signature);
    return "paid";
  }
  return "pending";
}

// ---------------------------------------------------------------------------
// Step 3 — the shopping agent issues an agent-native payment request.
// ---------------------------------------------------------------------------
export interface CreateRequestInput {
  productId: string;
  title: string;
  amount: string; // decimal USDC string, e.g. "25.00"
  orderRef: string;
  payTo?: string; // defaults to the merchant wallet
}

export function createPaymentRequest(input: CreateRequestInput): PaymentRequest {
  const reference = Keypair.generate().publicKey; // unique per-order on-chain tag
  const recipient = input.payTo ? new PublicKey(input.payTo) : merchant.publicKey;
  const now = Date.now();
  const expiresAt = now + config.paymentTtlMin * 60_000;

  const stored: StoredRequest = {
    reference: reference.toBase58(),
    amount: input.amount,
    recipient: recipient.toBase58(),
    orderRef: input.orderRef,
    productId: input.productId,
    title: input.title,
    createdAt: now,
    expiresAt,
    status: "pending",
    submittedTxSignature: null,
    paidTxSignature: null,
    refundReference: Keypair.generate().publicKey.toBase58(),
    refundStatus: "not_refunded",
    refundSubmittedTxSignature: null,
    refundTxSignature: null,
  };
  store.put(stored);

  const solanaPayUrl = encodeURL({
    recipient,
    amount: new BigNumber(input.amount),
    splToken: usdcMint,
    reference,
    label: "Agentic Resell Broker",
    message: `Order ${input.orderRef} — ${input.title}`,
  }).toString();

  return {
    productId: input.productId,
    title: input.title,
    price: { amount: input.amount, currency: "USDC" },
    payTo: recipient.toBase58(),
    reference: reference.toBase58(),
    orderRef: input.orderRef,
    network: CLUSTER === "mainnet" ? "solana-mainnet" : "solana-devnet",
    expiresAt: new Date(expiresAt).toISOString(),
    solanaPayUrl,
  };
}

// ---------------------------------------------------------------------------
// Step 4/5 — the buyer wallet autonomously signs & sends the USDC transfer.
// No human approval: this endpoint IS the "자율 서명".
// ---------------------------------------------------------------------------
export interface PayInput {
  payTo: string;
  amount: string;
  reference: string;
  /** Server-verified human wallet. Omitted only when no human principal exists. */
  delegator?: string;
}

async function submitPayment(
  stored: StoredRequest,
  input: PayInput,
): Promise<PayResult> {
  const recipient = new PublicKey(input.payTo);
  const referencePk = new PublicKey(input.reference);
  let tx: Transaction;
  let latestBlockhash: Awaited<ReturnType<typeof connection.getLatestBlockhash>>;
  try {
    await assertUsdcDecimals();

    // Any authenticated human payment spends from that user's ATA with the
    // buyer agent as SPL delegate, independent of web/MCP/A2A transport.
    // Service-principal and CLI calls omit `delegator` and retain the original
    // buyer-wallet source with zero human clicks.
    const amount = toBaseUnits(input.amount);
    const sourceTokenAccount = input.delegator
      ? await delegatedSourceAccount(input.delegator, amount)
      : (await ensureAta(buyer, buyer.publicKey)).address;

    // The agent remains fee payer and funds recipient ATA creation.
    const recipientAta = await ensureAta(buyer, recipient);

    const ix = createTransferCheckedInstruction(
      sourceTokenAccount,
      usdcMint,
      recipientAta.address,
      buyer.publicKey,
      amount,
      usdcDecimals,
    );
    // The Solana Pay "reference": a non-signer, read-only key that tags the tx so
    // the merchant can locate it on-chain without trusting the client.
    ix.keys.push({ pubkey: referencePk, isSigner: false, isWritable: false });

    tx = new Transaction().add(ix);
    tx.feePayer = buyer.publicKey;
    latestBlockhash = await connection.getLatestBlockhash("confirmed");
    tx.recentBlockhash = latestBlockhash.blockhash;
    tx.sign(buyer);
  } catch (err) {
    // No transfer has been signed, so this reference is safe to try again.
    store.resetUnsubmitted(input.reference);
    throw err;
  }

  if (!tx.signature) {
    store.resetUnsubmitted(input.reference);
    throw new Error("Signed transaction did not contain a signature");
  }
  const signature = bs58.encode(tx.signature);
  store.recordSubmitted(input.reference, signature);

  try {
    const submittedSignature = await connection.sendRawTransaction(tx.serialize(), {
      preflightCommitment: "confirmed",
      maxRetries: 5,
    });
    if (submittedSignature !== signature) {
      console.warn(
        `[pay] RPC returned signature ${submittedSignature}, expected ${signature}`,
      );
    }
  } catch (err) {
    const reconciled = await reconcileSubmittedPayment(stored, signature);
    if (reconciled === "paid") return paidResult(signature);
    if (reconciled === "failed") {
      throw new Error(
        `USDC transfer ${signature} failed; issue a new payment reference`,
      );
    }
    console.warn(
      `[pay] broadcast result is uncertain for ${signature}; reference remains locked:`,
      String(err),
    );
    return pendingResult(signature, "broadcast_uncertain");
  }

  let confirmation;
  try {
    confirmation = await connection.confirmTransaction(
      { signature, ...latestBlockhash },
      "confirmed",
    );
  } catch (err) {
    const reconciled = await reconcileSubmittedPayment(stored, signature);
    if (reconciled === "paid") return paidResult(signature);
    if (reconciled === "failed") {
      throw new Error(
        `USDC transfer ${signature} failed; issue a new payment reference`,
      );
    }
    console.warn(
      `[pay] confirmation is uncertain for ${signature}; reference remains locked:`,
      String(err),
    );
    return pendingResult(signature, "confirmation_uncertain");
  }
  if (confirmation.value.err) {
    throw new Error(
      `USDC transfer ${signature} failed: ${JSON.stringify(confirmation.value.err)}; ` +
        "issue a new payment reference",
    );
  }

  store.markPaid(input.reference, signature);
  return paidResult(signature);
}

export async function pay(input: PayInput): Promise<PayResult> {
  const recipient = new PublicKey(input.payTo);
  const stored = store.get(input.reference);
  if (!stored) {
    throw new Error("Unknown payment reference");
  }
  if (
    stored.recipient !== recipient.toBase58() ||
    !new BigNumber(stored.amount).eq(input.amount)
  ) {
    throw new Error("Payment does not match the issued request");
  }

  if (stored.status === "paid") {
    if (!stored.paidTxSignature) {
      throw new Error(`Paid payment reference ${input.reference} has no stored signature`);
    }
    return paidResult(stored.paidTxSignature);
  }
  if (stored.status === "pending" && Date.now() > stored.expiresAt) {
    throw new Error("Payment request has expired");
  }

  const transition = store.beginPayment(input.reference);
  if (transition.state === "paid") {
    if (!transition.request.paidTxSignature) {
      throw new Error(`Paid payment reference ${input.reference} has no stored signature`);
    }
    return paidResult(transition.request.paidTxSignature);
  }
  if (transition.state === "paying") {
    const inFlight = inFlightPayments.get(input.reference);
    if (inFlight) return inFlight;

    const submitted = transition.request.submittedTxSignature;
    if (!submitted) {
      throw new Error(`Payment reference ${input.reference} is already being prepared`);
    }
    const reconciled = await reconcileSubmittedPayment(transition.request, submitted);
    if (reconciled === "paid") return paidResult(submitted);
    if (reconciled === "failed") {
      throw new Error(
        `USDC transfer ${submitted} failed; issue a new payment reference`,
      );
    }
    return pendingResult(submitted, "confirmation_uncertain");
  }

  const operation = submitPayment(transition.request, input);
  inFlightPayments.set(input.reference, operation);
  try {
    return await operation;
  } finally {
    if (inFlightPayments.get(input.reference) === operation) {
      inFlightPayments.delete(input.reference);
    }
  }
}

// ---------------------------------------------------------------------------
// Post-purchase refund — the merchant wallet returns the original full USDC
// amount to the configured buyer wallet. The original Solana Pay transfer is
// re-verified on-chain before the refund CAS can be acquired.
// ---------------------------------------------------------------------------
export interface RefundInput {
  orderRef: string;
  reference: string;
}

export interface RefundTransferResult {
  orderRef: string;
  status: "pending" | "refunded";
  amount: string;
  recipient: string;
  paymentTxSignature: string;
  paymentExplorer: string;
  refundReference: string;
  refundTxSignature: string;
  refundExplorer: string;
  reason: "broadcast_uncertain" | "confirmation_uncertain" | null;
  replayed: boolean;
}

type RefundReconciliationResult = "failed" | "refunded" | "pending";

function refundTransferResult(
  stored: StoredRequest,
  signature: string,
  status: RefundTransferResult["status"],
  reason: RefundTransferResult["reason"],
  replayed: boolean,
): RefundTransferResult {
  if (!stored.paidTxSignature) {
    throw new Error(`Paid order ${stored.orderRef} has no stored payment signature`);
  }
  return {
    orderRef: stored.orderRef,
    status,
    amount: stored.amount,
    recipient: buyer.publicKey.toBase58(),
    paymentTxSignature: stored.paidTxSignature,
    paymentExplorer: explorerTxUrl(stored.paidTxSignature, CLUSTER),
    refundReference: stored.refundReference,
    refundTxSignature: signature,
    refundExplorer: explorerTxUrl(signature, CLUSTER),
    reason,
    replayed,
  };
}

async function reconcileSubmittedRefund(
  stored: StoredRequest,
  signature: string,
): Promise<RefundReconciliationResult> {
  const refundReference = new PublicKey(stored.refundReference);
  const [statusResult, referenceResult] = await Promise.allSettled([
    withFailover((c) =>
      c.getSignatureStatuses([signature], { searchTransactionHistory: true }),
    ),
    withFailover(
      (c) => findReference(c, refundReference, { finality: "confirmed" }),
      (err) => !(err instanceof FindReferenceError),
    ).catch((err: unknown) => {
      if (err instanceof FindReferenceError) return null;
      throw err;
    }),
  ]);

  const signatureStatus =
    statusResult.status === "fulfilled" ? statusResult.value.value[0] : null;
  const referenceInfo =
    referenceResult.status === "fulfilled" ? referenceResult.value : null;
  if (
    signatureStatus?.err ||
    (referenceInfo?.signature === signature && referenceInfo.err)
  ) {
    return "failed";
  }
  if (
    signatureStatus?.confirmationStatus !== "confirmed" &&
    signatureStatus?.confirmationStatus !== "finalized" &&
    referenceInfo?.signature !== signature
  ) {
    return "pending";
  }

  try {
    await withFailover((c) =>
      validateTransfer(
        c,
        signature,
        {
          recipient: buyer.publicKey,
          amount: new BigNumber(stored.amount),
          splToken: usdcMint,
          reference: refundReference,
        },
        { commitment: "confirmed" },
      ),
    );
  } catch (err) {
    if (err instanceof ValidateTransferError && err.message === "not found") {
      return "pending";
    }
    console.error(
      `[refund] submitted transaction ${signature} failed validation:`,
      err instanceof Error ? err.message : String(err),
    );
    return "failed";
  }

  store.markRefunded(stored.reference, signature);
  return "refunded";
}

async function submitRefund(stored: StoredRequest): Promise<RefundTransferResult> {
  let tx: Transaction;
  let latestBlockhash: Awaited<ReturnType<typeof connection.getLatestBlockhash>>;
  try {
    await assertUsdcDecimals();

    const merchantAta = await ensureAta(merchant, merchant.publicKey);
    const buyerAta = await ensureAta(merchant, buyer.publicKey);
    const ix = createTransferCheckedInstruction(
      merchantAta.address,
      usdcMint,
      buyerAta.address,
      merchant.publicKey,
      toBaseUnits(stored.amount),
      usdcDecimals,
    );
    ix.keys.push({
      pubkey: new PublicKey(stored.refundReference),
      isSigner: false,
      isWritable: false,
    });

    tx = new Transaction().add(ix);
    tx.feePayer = merchant.publicKey;
    latestBlockhash = await connection.getLatestBlockhash("confirmed");
    tx.recentBlockhash = latestBlockhash.blockhash;
    tx.sign(merchant);
  } catch (err) {
    store.resetUnsubmittedRefund(stored.reference);
    throw err;
  }

  if (!tx.signature) {
    store.resetUnsubmittedRefund(stored.reference);
    throw new Error("Signed refund transaction did not contain a signature");
  }
  const signature = bs58.encode(tx.signature);
  store.recordRefundSubmitted(stored.reference, signature);

  try {
    const submittedSignature = await connection.sendRawTransaction(tx.serialize(), {
      preflightCommitment: "confirmed",
      maxRetries: 5,
    });
    if (submittedSignature !== signature) {
      console.warn(
        `[refund] RPC returned signature ${submittedSignature}, expected ${signature}`,
      );
    }
  } catch (err) {
    const reconciled = await reconcileSubmittedRefund(stored, signature);
    if (reconciled === "refunded") {
      return refundTransferResult(stored, signature, "refunded", null, false);
    }
    if (reconciled === "failed") {
      throw new Error(
        `USDC refund ${signature} failed; manual review is required before retrying`,
      );
    }
    console.warn(
      `[refund] broadcast result is uncertain for ${signature}; refund remains locked:`,
      String(err),
    );
    return refundTransferResult(
      stored,
      signature,
      "pending",
      "broadcast_uncertain",
      false,
    );
  }

  let confirmation;
  try {
    confirmation = await connection.confirmTransaction(
      { signature, ...latestBlockhash },
      "confirmed",
    );
  } catch (err) {
    const reconciled = await reconcileSubmittedRefund(stored, signature);
    if (reconciled === "refunded") {
      return refundTransferResult(stored, signature, "refunded", null, false);
    }
    if (reconciled === "failed") {
      throw new Error(
        `USDC refund ${signature} failed; manual review is required before retrying`,
      );
    }
    console.warn(
      `[refund] confirmation is uncertain for ${signature}; refund remains locked:`,
      String(err),
    );
    return refundTransferResult(
      stored,
      signature,
      "pending",
      "confirmation_uncertain",
      false,
    );
  }
  if (confirmation.value.err) {
    throw new Error(
      `USDC refund ${signature} failed: ${JSON.stringify(confirmation.value.err)}; ` +
        "manual review is required before retrying",
    );
  }

  store.markRefunded(stored.reference, signature);
  console.log(
    `[refund] returned ${stored.amount} USDC merchant→buyer for ${stored.orderRef} tx=${signature}`,
  );
  return refundTransferResult(stored, signature, "refunded", null, false);
}

export async function refund(input: RefundInput): Promise<RefundTransferResult> {
  const stored = store.get(input.reference);
  if (!stored || stored.orderRef !== input.orderRef) {
    throw new RefundRefusedError(
      `No issued payment matches order ${input.orderRef} and reference ${input.reference}`,
    );
  }
  if (stored.recipient !== merchant.publicKey.toBase58()) {
    throw new RefundRefusedError(
      `Order ${input.orderRef} was not paid to the configured merchant wallet`,
    );
  }

  // Never trust process-local paid state alone for the reverse money movement.
  const originalPayment = await verify(input.reference);
  if (
    originalPayment.status !== "paid" ||
    !originalPayment.txSignature ||
    !originalPayment.explorer
  ) {
    throw new RefundRefusedError(
      `Order ${input.orderRef} cannot be refunded because its original payment ` +
        `is not verified on-chain (status=${originalPayment.status}, ` +
        `reason=${originalPayment.reason ?? "unknown"})`,
    );
  }

  if (stored.refundStatus === "refunded") {
    if (!stored.refundTxSignature) {
      throw new Error(`Refunded order ${input.orderRef} has no stored refund signature`);
    }
    return refundTransferResult(
      stored,
      stored.refundTxSignature,
      "refunded",
      null,
      true,
    );
  }

  const transition = store.beginRefund(input.reference);
  if (transition.state === "refunded") {
    if (!transition.request.refundTxSignature) {
      throw new Error(`Refunded order ${input.orderRef} has no stored refund signature`);
    }
    return refundTransferResult(
      transition.request,
      transition.request.refundTxSignature,
      "refunded",
      null,
      true,
    );
  }
  if (transition.state === "refunding") {
    const inFlight = inFlightRefunds.get(input.reference);
    if (inFlight) {
      const result = await inFlight;
      return { ...result, replayed: true };
    }
    const submitted = transition.request.refundSubmittedTxSignature;
    if (!submitted) {
      throw new Error(`Refund for order ${input.orderRef} is already being prepared`);
    }
    const reconciled = await reconcileSubmittedRefund(transition.request, submitted);
    if (reconciled === "refunded") {
      return refundTransferResult(
        transition.request,
        submitted,
        "refunded",
        null,
        true,
      );
    }
    if (reconciled === "failed") {
      throw new Error(
        `USDC refund ${submitted} failed; manual review is required before retrying`,
      );
    }
    return refundTransferResult(
      transition.request,
      submitted,
      "pending",
      "confirmation_uncertain",
      true,
    );
  }

  const operation = submitRefund(transition.request);
  inFlightRefunds.set(input.reference, operation);
  try {
    return await operation;
  } finally {
    if (inFlightRefunds.get(input.reference) === operation) {
      inFlightRefunds.delete(input.reference);
    }
  }
}

// ---------------------------------------------------------------------------
// Step 7/8 — the shopping agent verifies the payment on-chain by reference.
// ---------------------------------------------------------------------------
function validationFailureReason(message: string): VerificationFailureReasonT {
  if (message === "amount not transferred") return "amount_mismatch";
  if (message === "recipient not found") return "recipient_mismatch";
  if (message.includes("reference")) return "reference_mismatch";
  if (message.includes("transfer")) return "transfer_mismatch";
  if (message.includes("missing")) return "malformed_transaction";
  return "validation_failed";
}

export async function verify(reference: string): Promise<VerificationResult> {
  const stored = store.get(reference);
  if (!stored) {
    return {
      status: "invalid",
      txSignature: null,
      explorer: null,
      amount: null,
      reason: "unknown_reference",
    };
  }

  const referencePk = new PublicKey(reference);
  const deadline = Date.now() + VERIFY_TIMEOUT_MS;

  let signatureInfo;
  while (!signatureInfo) {
    try {
      signatureInfo = await withFailover((c) =>
        findReference(c, referencePk, { finality: "confirmed" }),
        (err) => !(err instanceof FindReferenceError),
      );
    } catch (err) {
      if (!(err instanceof FindReferenceError)) throw err;
      if (Date.now() >= deadline) {
        const status = Date.now() > stored.expiresAt ? "expired" : "pending";
        return {
          status,
          txSignature: null,
          explorer: null,
          amount: null,
          reason: "transaction_not_found",
        };
      }
      await sleep(Math.min(VERIFY_POLL_MS, deadline - Date.now()));
    }
  }

  if (signatureInfo.err) {
    return {
      status: "invalid",
      txSignature: signatureInfo.signature,
      explorer: explorerTxUrl(signatureInfo.signature, CLUSTER),
      amount: null,
      reason: "transaction_failed",
    };
  }

  while (true) {
    try {
      await withFailover((c) =>
        validateTransfer(
          c,
          signatureInfo.signature,
          {
            recipient: new PublicKey(stored.recipient),
            amount: new BigNumber(stored.amount),
            splToken: usdcMint,
            reference: referencePk,
          },
          { commitment: "confirmed" },
        ),
        (err) => !(err instanceof ValidateTransferError),
      );
      break;
    } catch (err) {
      if (
        err instanceof ValidateTransferError &&
        err.message === "not found" &&
        Date.now() < deadline
      ) {
        await sleep(Math.min(VERIFY_POLL_MS, deadline - Date.now()));
        continue;
      }
      if (!(err instanceof ValidateTransferError)) throw err;
      if (err.message === "not found") {
        return {
          // A signature exists, so expiry must not overwrite an in-flight tx.
          status: "pending",
          txSignature: signatureInfo.signature,
          explorer: explorerTxUrl(signatureInfo.signature, CLUSTER),
          amount: null,
          reason: "transaction_not_confirmed",
        };
      }
      console.warn(`[verify] on-chain tx failed validation for ${reference}:`, err.message);
      return {
        status: "invalid",
        txSignature: signatureInfo.signature,
        explorer: explorerTxUrl(signatureInfo.signature, CLUSTER),
        amount: null,
        reason: validationFailureReason(err.message),
      };
    }
  }

  store.markPaid(reference, signatureInfo.signature);
  return {
    status: "paid",
    txSignature: signatureInfo.signature,
    explorer: explorerTxUrl(signatureInfo.signature, CLUSTER),
    amount: stored.amount,
    reason: null,
  };
}
