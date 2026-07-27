# Issue #48 — authenticated user wallet on MCP and A2A

Verified locally against Solana devnet and the live Shopify catalog/order API
on 2026-07-27. No service or Shopify theme was deployed or modified.

## Payer invariant

Relay now selects the USDC source from the verified principal rather than the
transport:

- Clerk OAuth/session or signed human A2A identity: that user's ATA, with the
  buyer agent acting as SPL delegate and fee payer.
- MCP API-key service principal, CLI, or rehearsal without a human: configured
  buyer-agent ATA.
- Authenticated user without enough live delegation: fail closed with an
  `approval-required` response and storefront approval URL.

The OAuth wallet comes only from the authentication middleware context. MCP
tool arguments expose no wallet selector, and a signed human A2A mandate must
have `delegator == signer_wallet`.

## Delegation setup

A fresh throwaway devnet user received 3 USDC and approved a 2.5 USDC limit.
The user held zero SOL; the agent paid the approval fee.

- User: `6TsGmY4mjSCeLBhCpHkbsyG6HD6DxBXcRufR9UJnRM1s`
- Delegate / agent: `7dwPd8gByoFPwfu8swgVFddzaSLyRARbmVuwposmxrr`
- User ATA: `MD3EyXT6THtCcUBerWHXFPtKotMb6LLQ3wF6VXAmXxD`
- Funding transaction:
  `24wppJMuzHFVhn94iRk9f3Q9sP37zsb8V8KGhHzka8hTDKTvknkufZKQsz4ZtR4u2GXViUF5uLefLVuKHKxG1pCP`
- Approval transaction:
  `5ZrY4LWiZe8paL6X5fvN4My34DVMtrrnfd6hsdqY1N9LV7LhR1Jfdu5yAoHQVko6X4GdVopZX4XDhiJu76tgutEs`
- Approval explorer:
  <https://explorer.solana.com/tx/5ZrY4LWiZe8paL6X5fvN4My34DVMtrrnfd6hsdqY1N9LV7LhR1Jfdu5yAoHQVko6X4GdVopZX4XDhiJu76tgutEs?cluster=devnet>

## OAuth MCP delegated purchase

The local MCP Streamable HTTP transport ran with a test-only replacement for
Clerk token verification. The verified wallet still entered only through
`AuthenticationMiddleware`; quote, payment, A2A settlement, on-chain
verification, and Shopify order creation used the real services.

- Product amount: `2.13 USDC`
- Order ref: `ord_c04e5be14f26435f8b0782561d0cba5e`
- Shopify order: `gid://shopify/Order/8711442759966`
- Payment transaction:
  `afvNVbwSPk8LXewhg7yptBc5DELGLQXFsJQyqz7jpKmBefvQjR5RDoo5JQmE2Hv4qTFTVopCEigkWPsEwR73UoY`
- Explorer:
  <https://explorer.solana.com/tx/afvNVbwSPk8LXewhg7yptBc5DELGLQXFsJQyqz7jpKmBefvQjR5RDoo5JQmE2Hv4qTFTVopCEigkWPsEwR73UoY?cluster=devnet>

| On-chain field | Before | After | Delta |
|---|---:|---:|---:|
| Authenticated user USDC | 3.00 | 0.87 | **-2.13** |
| Buyer-agent USDC | 17.79 | 17.79 | **0.00** |

The transaction's sole signer was the buyer agent
`7dwPd8g…osmxrr`; the authenticated user supplied **zero purchase
signatures**. The reference-bound Solana Pay transfer and Shopify order both
settled as `paid`.

## Missing-delegation refusal

The user revoked the delegate on-chain:

- Revoke transaction:
  `5XFr4YFr7DMjpWxvR5d4zsmudathQap762BizHPiD5w1pKfg1pBVkHjZmsqq5kFPUP28YgpvmTT6ME9HcHpsFCR`
- Explorer:
  <https://explorer.solana.com/tx/5XFr4YFr7DMjpWxvR5d4zsmudathQap762BizHPiD5w1pKfg1pBVkHjZmsqq5kFPUP28YgpvmTT6ME9HcHpsFCR?cluster=devnet>
- Readback: `active=false`, `allowanceRemaining=0`

The next OAuth MCP purchase reached quote but stopped before `/pay` and
returned:

```json
{
  "status": "approval-required",
  "reason": "SPL delegation is missing or revoked; approve the broker once.",
  "delegator": "6TsGmY4mjSCeLBhCpHkbsyG6HD6DxBXcRufR9UJnRM1s",
  "requiredAmount": { "amount": "2.13", "currency": "USDC" },
  "allowanceRemaining": { "amount": "0", "currency": "USDC" },
  "balance": { "amount": "0.87", "currency": "USDC" },
  "approvalUrl": "https://solanagcp.myshopify.com/?relayAction=approve&relayAmount=2.13"
}
```

Both balances were read from devnet before and after the refusal:

| Wallet | Before | After | Delta |
|---|---:|---:|---:|
| Authenticated user | 0.87 | 0.87 | **0.00 USDC** |
| Buyer agent / prior fallback source | 17.79 | 17.79 | **0.00 USDC** |

No transaction signature or settlement was produced.

## Principal-free regressions

An MCP server started with a local API-key service principal completed the same
2.13 USDC catalog purchase from the configured buyer wallet:

- Transaction:
  `45Sbk1giqetx4sS1k44nsK3FhiXB6E6JQsTMYvhbPHRq2gRBWS9YxAL2pp1dehAaJmDXhFhAf9ER4h8aWFJVfPCX`
- Explorer:
  <https://explorer.solana.com/tx/45Sbk1giqetx4sS1k44nsK3FhiXB6E6JQsTMYvhbPHRq2gRBWS9YxAL2pp1dehAaJmDXhFhAf9ER4h8aWFJVfPCX?cluster=devnet>
- Shopify order: `gid://shopify/Order/8711445479710`

The unchanged CLI flow also completed from the buyer-agent wallet:

- Transaction:
  `R2AExqFsQGSg7MdbkRAmPu9Bh7CeMgx2obzPmUoCxXj34SQi3FkBQFbVfyYCyfpxZoF9p5UjdQ2mnXJL9CHZiqB`
- Explorer:
  <https://explorer.solana.com/tx/R2AExqFsQGSg7MdbkRAmPu9Bh7CeMgx2obzPmUoCxXj34SQi3FkBQFbVfyYCyfpxZoF9p5UjdQ2mnXJL9CHZiqB?cluster=devnet>
- Shopify order: `gid://shopify/Order/8711446364446`

## Automated verification

- `pnpm -r typecheck` — pass
- Python agents — 57 passed
- Payments — 8 passed
- Commerce — 17 passed
- Shopify seed policy — 2 passed
- All JSON contract schemas parsed successfully
- Contract addition mirrored in Zod, Pydantic, and
  `delegation-approval-required.schema.json`
