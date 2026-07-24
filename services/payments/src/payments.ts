import {
  Keypair,
  PublicKey,
  Transaction,
  sendAndConfirmTransaction,
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
import {
  explorerTxUrl,
  type PaymentRequest,
  type VerificationResult,
} from "@arb/shared";
import { config } from "./config.js";
import {
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

const CLUSTER = config.cluster;

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
}

export async function pay(input: PayInput): Promise<{ txSignature: string; explorer: string }> {
  const recipient = new PublicKey(input.payTo);
  const referencePk = new PublicKey(input.reference);

  // Ensure both sides have a USDC token account (buyer funds any creation).
  const buyerAta = await ensureAta(buyer, buyer.publicKey);
  const recipientAta = await ensureAta(buyer, recipient);

  const ix = createTransferCheckedInstruction(
    buyerAta.address,
    usdcMint,
    recipientAta.address,
    buyer.publicKey,
    toBaseUnits(input.amount),
    usdcDecimals,
  );
  // The Solana Pay "reference": a non-signer, read-only key that tags the tx so
  // the merchant can locate it on-chain without trusting the client.
  ix.keys.push({ pubkey: referencePk, isSigner: false, isWritable: false });

  const tx = new Transaction().add(ix);
  tx.feePayer = buyer.publicKey;

  const signature = await sendAndConfirmTransaction(connection, tx, [buyer], {
    commitment: "confirmed",
  });

  return { txSignature: signature, explorer: explorerTxUrl(signature, CLUSTER) };
}

// ---------------------------------------------------------------------------
// Step 7/8 — the shopping agent verifies the payment on-chain by reference.
// ---------------------------------------------------------------------------
export async function verify(reference: string): Promise<VerificationResult> {
  const stored = store.get(reference);
  if (!stored) {
    return { status: "invalid", txSignature: null, explorer: null, amount: null };
  }

  const referencePk = new PublicKey(reference);

  let signatureInfo;
  try {
    signatureInfo = await withFailover((c) =>
      findReference(c, referencePk, { finality: "confirmed" }),
    );
  } catch (err) {
    if (err instanceof FindReferenceError) {
      const status = Date.now() > stored.expiresAt ? "expired" : "pending";
      return { status, txSignature: null, explorer: null, amount: null };
    }
    throw err;
  }

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
    );
  } catch (err) {
    if (err instanceof ValidateTransferError) {
      console.warn(`[verify] on-chain tx failed validation for ${reference}:`, err.message);
      return {
        status: "invalid",
        txSignature: signatureInfo.signature,
        explorer: explorerTxUrl(signatureInfo.signature, CLUSTER),
        amount: null,
      };
    }
    throw err;
  }

  return {
    status: "paid",
    txSignature: signatureInfo.signature,
    explorer: explorerTxUrl(signatureInfo.signature, CLUSTER),
    amount: stored.amount,
  };
}
