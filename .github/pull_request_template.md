<!-- Title: <type>: <summary>  (e.g. "feat: harden devnet USDC payment spine") -->

## What & why
Closes #<!-- issue -->

<!-- one or two lines -->

## Type
- [ ] Spine / payments (money path)
- [ ] Agent (buyer / shopping)
- [ ] Commerce (Shopify)
- [ ] Infra / deploy
- [ ] Protocol (A2A / AP2 / x402)
- [ ] Docs / chore

## Checklist
- [ ] `pnpm -r typecheck` green
- [ ] Contracts in sync: `packages/shared` (TS) ↔ `agents/agentic_broker/common/contracts.py` (Python) ↔ `packages/shared/schemas/*.json`
- [ ] Additive / non-breaking — the Solana Pay spine still round-trips
- [ ] No secrets committed (`.env`, `wallets/*.json`)
- [ ] Docs updated (README / AGENTS / relevant `docs/*`) + `CHANGELOG.md` entry
- [ ] **Money path only:** real devnet round-trip verified — explorer tx link below

## Evidence
<!-- explorer tx link, demo output, screenshots -->
