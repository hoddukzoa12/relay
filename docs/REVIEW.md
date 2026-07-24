# Review plan

Every change lands via **PR → review → squash-merge to `main`**. Linear history,
one PR per issue. The coordinator (planner agent) reviews; **the user is the
final approver / merger**.

## PR lifecycle
1. Worker (codex) implements on `feat/<n>-<slug>`, opens a PR, fills the template,
   links the issue (`Closes #n`).
2. **CI must be green** (`typescript` + `python` jobs).
3. Coordinator review against the checklist below. **Money-path PRs also require an
   adversarial pass + a real devnet round-trip** before approval.
4. Squash-merge, delete branch, ensure `CHANGELOG.md` updated.

## Review checklist
- **Correctness** — meets the issue's acceptance criteria.
- **Contract-first** — `packages/shared` ↔ `contracts.py` ↔ `schemas/*.json` in
  sync; any contract change is called out and mirrored in both languages.
- **Non-breaking** — the Solana Pay spine still round-trips; existing endpoints are
  unchanged or extended, not altered.
- **Security** — no secrets committed; inputs validated (zod / pydantic); no new
  external trust introduced on the scored demo path.
- **Money path** (`services/payments/*`, pay/verify) — **real devnet tx proof
  (explorer link in the PR)**; correct amount / recipient / `reference`; ATA,
  decimals, blockhash/feePayer handled; verify polls through confirmation lag.
  Do an adversarial read (try to break it) before approving.
- **Evidence** — verification output / screenshots attached.
- **Docs** — touched docs + a `CHANGELOG.md` line updated in the same PR.

## Tooling
- `/code-review` on the working branch for an automated pass before human review.
- `/code-review ultra <PR#>` (ultrareview) for the spine (#1) and every payment PR
  — multi-agent cloud review. User-triggered.
- `CODEOWNERS` routes review requests to the owner.

## Branch protection (enable after the first CI run registers the check)
- Require a PR before merging to `main`.
- Require the `ci` status checks to pass.
- Require linear history; disallow force-push to `main`.
- Solo-friendly: **do not** require a second approver (would block a solo dev);
  rely on CI + coordinator review + the money-path rule.

## The one hard rule
No payment-path PR merges without a demonstrated **live devnet USDC round-trip**
(explorer link in the PR). Typecheck is necessary, not sufficient.
