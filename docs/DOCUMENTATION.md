# Documentation plan

Docs are part of "done" — a PR that changes behavior updates docs **in the same
PR**. Reproducibility is judging criterion ②, so the doc set is a deliverable, not
an afterthought.

## Inventory (exists today)
| Doc | Purpose | Update trigger |
|---|---|---|
| `README.md` | Setup · run · reproduce | any run/setup change |
| `PRD.md` | Product spec (mostly frozen) | scope decisions |
| `AGENTS.md` / `CLAUDE.md` | Agent operating guide | conventions / setup change |
| `docs/WORKFLOW.md` | Roles · waves · worktrees | process change |
| `docs/PROTOCOLS.md` | A2A / AP2 / x402 reference | protocol work (#9, #6) |
| `docs/RELEASE.md` | Release train + checklist | milestone change |
| `docs/REVIEW.md` | PR review process | process change |
| `CHANGELOG.md` | What changed per version | **every PR** |

## Needed (create as features land)
- `docs/ARCHITECTURE.md` — component boundaries + the message-flow sequence as a
  **mermaid** diagram (after #1 and #9 stabilize).
- `docs/DEMO.md` — the 3-minute demo script + shot list (with #7).
- `docs/API.md` (or per-service READMEs) — endpoint contracts for
  `payments` / `commerce` / the agents' A2A surface.
- `services/*/README.md` — brief per-service run/notes as each stabilizes
  (`agents/README.md` already exists).

## Rules
- **Every PR**: update touched docs + add a `CHANGELOG.md` line.
- **Every release tag**: refresh README status + live URL; re-verify the DoD
  checklist in `docs/RELEASE.md`.
- **Diagrams**: keep as mermaid in-repo so they render on GitHub and stay diffable
  (no binary image blobs for architecture).
- **One source of truth**: contracts live in `packages/shared`; docs link to it
  rather than restating field lists that can drift.
