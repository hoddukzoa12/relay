# Changelog

All notable changes to Relay are recorded here.
Format: [Keep a Changelog](https://keepachangelog.com/); versioning is SemVer-ish
(pre-1.0, date-driven by the hackathon — see [docs/RELEASE.md](docs/RELEASE.md)).

## [Unreleased]

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
