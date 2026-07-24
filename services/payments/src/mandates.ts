import { Keypair, PublicKey } from "@solana/web3.js";
import nacl from "tweetnacl";

export type Mandate = Record<string, unknown>;

/**
 * Return the JSON value covered by an AP2 detached signature. The signature
 * property itself is excluded so the same object can be passed to sign/verify.
 */
function unsignedMandate(mandate: Mandate): Mandate {
  const { signature: _signature, ...unsigned } = mandate;
  return unsigned;
}

function canonicalize(value: unknown): unknown {
  if (Array.isArray(value)) {
    return value.map(canonicalize);
  }
  if (value !== null && typeof value === "object") {
    return Object.fromEntries(
      Object.entries(value as Record<string, unknown>)
        .sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0))
        .map(([key, child]) => [key, canonicalize(child)]),
    );
  }
  return value;
}

/** Stable canonical JSON for Relay's JSON-only mandate values. */
export function canonicalMandateJson(mandate: Mandate): string {
  return JSON.stringify(canonicalize(unsignedMandate(mandate)));
}

/** Sign a mandate with the same ed25519 keypair used for Solana settlement. */
export function signMandate(mandate: Mandate, signer: Keypair): string {
  const message = Buffer.from(canonicalMandateJson(mandate), "utf8");
  const signature = nacl.sign.detached(message, signer.secretKey);
  return Buffer.from(signature).toString("base64url");
}

/** Verify a detached AP2 mandate signature against a Solana public key. */
export function verifyMandate(
  mandate: Mandate,
  signature: string,
  signer: PublicKey,
): boolean {
  try {
    const message = Buffer.from(canonicalMandateJson(mandate), "utf8");
    const signatureBytes = Buffer.from(signature, "base64url");
    return nacl.sign.detached.verify(message, signatureBytes, signer.toBytes());
  } catch {
    return false;
  }
}
