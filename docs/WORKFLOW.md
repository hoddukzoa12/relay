# Development workflow — planner/orchestrator + codex workers

This repo is built with **supervised multi-agent orchestration** via [Orca](https://github.com/).

- **Claude Code = coordinator / planner.** Decomposes work into contract-first
  issue specs, dispatches to workers, reviews branches, and integrates. Guards
  the on-chain payment correctness.
- **codex sessions = workers.** Each runs in its own git worktree and does the
  main implementation for one issue.

## Principles

1. **Contract-first.** `packages/shared` (PRD §6) is the only cross-module
   coupling. A worker never renegotiates an interface at runtime — it reads the
   schema. A change to a contract is a *blocking* task the coordinator owns first.
2. **Spine before fan-out.** The scored path is Leg 1 (buyer → payments →
   verify), issue **#1**. It must round-trip on devnet before parallel work that
   depends on it is dispatched.
3. **Review the money path for real.** The coordinator merges an issue only after
   reviewing its branch — and for anything touching the on-chain path, after an
   actual devnet round-trip, not just a typecheck.

## Issue map & DAG

| # | Issue | Priority | Depends on |
|---|-------|----------|-----------|
| **#1** | 🎯 Spine: Leg 1 devnet USDC 왕복 | p0 | — |
| #8 | Commerce 실연동 (Shopify) | p1 | — (parallel with #1) |
| #3 | 데모 웹 UI 다듬기 | p1 | soft: #1 |
| **#9** | A2A 정합 + AP2 mandate (②③ 승부수) | p1 | soft: #1 |
| #2 | Cloud Run 배포 | p1 | #1 |
| #4 | Eval 하네스 (③) | stretch | #1 |
| #6 | x402 애드온 (③④, AP2 crypto ext) | stretch | #1 |
| #5 | Gemini Enterprise publish (⑤) | stretch | #2 |
| #7 | 데모 영상 + 자체심사 | p1 | #1,#2,#3,#8 |

**Waves**
- **Wave 0 (now):** #1 (spine). Optionally #8 and #3 in parallel — their
  contracts are fixed, so they can't diverge from #1.
- **Wave 1 (after #1 merges):** #9 (protocol alignment — highest ③/② ROI), #2, #4.
- **Wave 2:** #6 (x402 addon), #5, then #7 (final).

## Protocol positioning (③ / ②)

Shopify's own Solana Pay is a **human web-checkout** (scan QR, approve in wallet)
— exactly what we avoid. We keep Shopify as an order ledger only and settle
autonomously. For the "차세대 결제 프로토콜" criterion we lead with **Google's own
stack**: A2A (already used) + **AP2 mandate** framing (#9), with **x402** as an
additive on-chain execution surface (#6, AP2's crypto extension, live on Solana).
Solana Pay stays the **guaranteed** live-tx rail; x402 never gates the demo.
One-line pitch: *"A2A로 협상 · AP2 mandate로 인가 · Solana Pay(+x402)로 USDC 온체인 정산."*

## Branch / worktree convention

- One worktree per issue, top-level (independent), based on `origin/main`:
  ```bash
  orca worktree create --name relay-<n>-<slug> --agent codex --no-parent --json
  ```
  e.g. `relay-1-spine`, `relay-8-commerce`. Branch: `feat/<n>-<slug>`.
- Small PR per issue → coordinator review → squash-merge to `main`.
- Dependent worktrees rebase on `main` after their dependency merges.

## Secrets in worktrees (important)

`.env` and `wallets/*.json` are git-ignored, so a fresh worktree won't have them.
For issues whose acceptance needs a live devnet tx (**#1**, #2, #7) or real
Shopify/GCP creds (#8 real-mode, #2, #5), the coordinator/user must place those
files into the worker's worktree (copy or symlink) before the worker can satisfy
the "live" acceptance criteria. Pure-code hardening can proceed without them.

## Coordinator loop (Orca)

```bash
orca status --json                      # runtime up?
orca skills get orchestration           # load the version-matched guide
# per worker:
orca worktree create --name relay-1-spine --agent codex --no-parent --json
orca terminal wait --terminal <handle> --for tui-idle --timeout-ms 60000 --json
orca orchestration task-create --spec "<issue #1 brief>" --json
orca orchestration dispatch --task <id> --to <handle> --inject --json
orca orchestration check --wait --types worker_done,escalation,decision_gate --timeout-ms 900000 --json
```

Issue specs are the dispatch briefs — self-contained (files · interface ·
acceptance · test). See each GitHub issue body.
