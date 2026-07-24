# Release plan

Pre-1.0 and date-driven by the hackathon. Tags are SemVer-ish; **each tag is a
demoable cut of `main`**. Releases are cut from `main` after CI is green.

## Release train

| Tag | Theme | Gate — must all be true | Target |
|---|---|---|---|
| `v0.1.0-spine` | Leg 1 devnet USDC round-trip live | #1 merged · real explorer tx · CI green | ~7/28 |
| `v0.2.0-commerce-protocol` | Real Shopify orders + A2A/AP2 alignment | #8, #9 merged | ~7/31 |
| `v0.3.0-cloud` | Cloud Run live URL, services wired | #2 merged · buyer URL loads · e2e on deploy | ~8/1 |
| `v0.9.0-rc` | Feature-complete, demo UI polished | #3 merged (·#4/#6 if time) | ~8/2 |
| `v1.0.0-submission` | **Hackathon submission cut** | PRD §12 DoD all ✓ · demo video · README repro | **8/3 23:59 KST** |
| `v1.1.0-demoday` | Demo Day build | rehearsed · RPC failover tested | 8/21 |

Waves map to issues (see [WORKFLOW.md](WORKFLOW.md)): spine #1 → protocol/commerce
#9/#8 → cloud #2 → polish #3 → stretch #4/#5/#6 → demo #7.

## Cutting a release
```bash
# 1. main is green and the milestone's issues are merged
# 2. move CHANGELOG [Unreleased] → the version, dated
# 3. tag + GitHub release
git tag -a v0.1.0-spine -m "Leg 1 devnet USDC round-trip live"
git push origin v0.1.0-spine
gh release create v0.1.0-spine --title "v0.1.0-spine" --notes-from-tag
```

## Submission gate — PRD §12 Definition of Done (the `v1.0.0` checklist)
- [ ] Real devnet USDC tx verifiable on the explorer
- [ ] Zero human clicks at the moment of payment (autonomy proven)
- [ ] `paid` Shopify order auto-created
- [ ] Live deployed URL reachable
- [ ] End-to-end demo reproducible in ≤3 minutes
- [ ] "Why on-chain?" one-line defense holds
