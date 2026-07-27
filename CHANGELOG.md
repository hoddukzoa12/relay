# Changelog

All notable changes to Relay are recorded here.
Format: [Keep a Changelog](https://keepachangelog.com/); versioning is SemVer-ish
(pre-1.0, date-driven by the hackathon — see [docs/RELEASE.md](docs/RELEASE.md)).

## [Unreleased]

### Added
- Added the live four-service Cloud Run deployment in `us-central1`, with a
  public buyer console, IAM-authenticated backend calls, Secret Manager
  provisioning within the free allowance, and scale-to-zero cost controls (#2).
- Added an idempotent Shopify catalog seed for nine demo electronics, live
  catalog search, and published/inventory readback evidence (#25).
- Added Clerk Sign in with Solana to the buyer console and Shopify widget,
  cached-JWKS session verification, one-signature human AP2 delegation, and
  signed-in-wallet Shopify order lookup (#29).
- Added the agent-owned post-purchase lifecycle: single-order status lookup,
  idempotent Shopify fulfillment with explicit demo tracking, and a full
  merchant-to-buyer on-chain USDC refund with bidirectional explorer proof
  (#26).

### Changed
- Grounded sourcing in in-stock, within-budget Shopify variants, carried the
  real SKU/variant through CartMandate, and linked Shopify orders to the real
  variant while preserving the broker resale price (#25).
- Replaced expiring pasted Shopify Admin tokens with a cached client credentials
  provider shared by the commerce service and catalog seeder, while retaining
  static-token compatibility for legacy stores (#28).

### Fixed
- Retried Shopify's transient post-creation `orderMarkAsPaid` availability
  window so the deployed buyer flow returns one clean paid confirmation (#2).
- Made payment references single-use with pending/paying/paid transitions,
  reconciled uncertain Solana confirmations without resending, and made broker
  settlement plus Shopify order creation idempotent by `orderRef` (#20).

## [v0.2.0-commerce-protocol] - 2026-07-24

### Added
- A2A v0.3 AgentCards and JSON-RPC `message/send`, plus buyer/merchant-signed
  AP2 Intent, Cart, and Payment mandates layered over the unchanged Solana Pay
  request, pay, and on-chain verification spine (#9).
- Live Shopify order-ledger integration with custom-priced line items, on-chain
  payment attributes, and explicit paid-status verification (#8).
- A self-contained Shopify product-page "Buy with Agent" widget, configurable
  buyer-agent CORS allowlist, and HTTPS tunnel demo guide (#14).

## [v0.1.0-spine] - 2026-07-24

### Added
- **Payment spine hardened** (#1, #11): issued-request validation on `pay`,
  on-chain USDC mint-decimal check, robust send/confirm with explicit blockhash,
  and `verify` polling (~20s) to absorb confirmation lag. First live devnet USDC
  round-trip — 3.45 USDC buyer→merchant, verified on the explorer.
- Monorepo scaffold: Python agents (Google ADK + Gemini) `buyer` + `shopping`;
  TypeScript services `payments` (@solana/web3.js + @solana/pay) + `commerce`
  (Shopify, mock-able); `packages/shared` canonical contracts (PRD §6).
- Solana Pay flow: create payment request · autonomous buyer pay · on-chain
  verify by `reference`. Demo web UI served by the buyer agent.
- Docs: `PRD.md`, `AGENTS.md` / `CLAUDE.md`, `docs/WORKFLOW.md`,
  `docs/PROTOCOLS.md` (A2A / AP2 / x402).
- Governance: CI (typecheck + py-compile), PR template, CODEOWNERS,
  `docs/RELEASE.md`, `docs/REVIEW.md`, `docs/DOCUMENTATION.md`.

<!--
Add one bullet per PR under the right heading (Added / Changed / Fixed / Removed).
On release, move [Unreleased] items under a new "## [vX.Y.Z] - YYYY-MM-DD" heading.
-->
