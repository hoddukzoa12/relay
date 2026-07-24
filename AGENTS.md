# AGENTS.md — Relay operating guide (for AI coding agents)

You are working in **Relay**: agent-to-agent commerce settled **autonomously in
on-chain USDC on Solana devnet**. Read this before touching code.
Full spec: [`PRD.md`](./PRD.md) · workflow: [`docs/WORKFLOW.md`](./docs/WORKFLOW.md)
· protocols: [`docs/PROTOCOLS.md`](./docs/PROTOCOLS.md).

## What matters most — the scored spine
- The judged path is **Leg 1**: buyer agent → `payments` issues an agent-native
  Solana Pay request → buyer wallet **signs autonomously (no human click)** →
  `shopping` verifies on-chain by `reference` → explorer tx. **It must round-trip
  live on devnet.**
- **Solana Pay is the guaranteed rail — never break it.** A2A / AP2 / x402 are
  additive layers on top, not replacements.
- **Never route payment through Shopify web checkout** (human approval =
  disqualifying). Shopify is an order ledger only.

## Architecture
- `agents/` — Python (Google ADK + Gemini). One package `agentic_broker`:
  `common/` (config · contracts · service clients · llm), `buyer/` (:8090, serves
  the demo UI), `shopping/` (:8091, the broker).
- `services/` — TypeScript, run via **tsx (no build step)**. `payments/` (:8081,
  `@solana/web3.js` + `@solana/pay`), `commerce/` (:8082, Shopify, mock-able).
- `packages/shared/` — **canonical contracts** (PRD §6). Source of truth.
- Python ↔ TS communicate over HTTP REST.

## Golden rules
1. **Contract-first.** `packages/shared/src/index.ts` (TS) and
   `agents/agentic_broker/common/contracts.py` (Python) MUST mirror each other,
   plus the JSON schemas in `packages/shared/schemas/`. If your task needs a
   contract change, call it out explicitly — don't silently diverge.
2. **Additive, non-breaking.** Prefer adding endpoints/fields over changing
   existing ones, especially on the payment path.
3. **Verify the money path for real.** Typecheck is necessary, not sufficient.
   Prove Leg 1 with an **actual devnet tx (explorer link)** before claiming done.
4. **Match the surrounding code.** TS: ESM, `zod` validation, `express`. Python:
   FastAPI sync handlers, `pydantic`, `httpx` (sync).

## Run / verify
```bash
make setup            # pnpm install + python venv (agents/.venv)
make check-wallets    # SOL (fees) + USDC balances for both wallets
./scripts/dev.sh      # all four services; buyer UI at http://localhost:8090
./scripts/demo.sh "wireless earbuds" 5
pnpm -r typecheck     # TS typecheck (must stay green)
```

## Secrets & wallets (git-ignored — NOT present in a fresh worktree)
- `.env` (copy from `.env.example`) and `wallets/{merchant,buyer}.json` do not
  exist in a fresh checkout. Create them.
- **Demo wallets: generate FRESH throwaway devnet keypairs** (project decision —
  do not ask for existing keys). The `solana` CLI may be absent; generate with
  Node via the already-installed `@solana/web3.js` (`Keypair.generate()`, write
  the 64-byte secret as a JSON array to `wallets/*.json`) and airdrop SOL with
  `connection.requestAirdrop(...)`. Keep a `scripts/gen-wallets.mjs` for this.
- **Devnet USDC** for the buyer wallet comes from the **Circle faucet**
  (https://faucet.circle.com → Solana Devnet) — this is a **manual human step**.
  Print the buyer pubkey and ask the human to fund it; do not block silently.
  Confirm `USDC_MINT` in `.env` matches the faucet token (`4zMMC9…`).
- **Never commit secrets.** `.gitignore` already excludes `.env` and
  `wallets/*.json`.

## Branch / PR
- One worktree per issue, branch `feat/<n>-<slug>`, based on `origin/main`.
  Small PR → coordinator review → merge to `main`.

## If you were dispatched via Orca orchestration
- Your prompt carries a live task preamble. Do the task; use `ask` for blocking
  questions; send `worker_done` **exactly once** when finished (or blocked), from
  your own terminal, with the required payload.

## Pointers
- Per-issue specs: GitHub issues → https://github.com/hoddukzoa12/relay/issues
- [`docs/WORKFLOW.md`](./docs/WORKFLOW.md) (roles/waves) ·
  [`docs/PROTOCOLS.md`](./docs/PROTOCOLS.md) (A2A/AP2/x402) · [`PRD.md`](./PRD.md)
