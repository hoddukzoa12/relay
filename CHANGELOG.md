# Changelog

All notable changes to Relay are recorded here.
Format: [Keep a Changelog](https://keepachangelog.com/); versioning is SemVer-ish
(pre-1.0, date-driven by the hackathon — see [docs/RELEASE.md](docs/RELEASE.md)).

## [Unreleased]

### Added
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
