"""One-shot buyer purchase from the terminal.

Example:
  python -m agentic_broker.buyer.cli --query "wireless earbuds" --budget 30
  python -m agentic_broker.buyer.cli --text "Buy me some wireless earbuds under 25 USDC"
"""
from __future__ import annotations

import argparse
import json

from ..common.config import settings
from . import flow


def main() -> None:
    ap = argparse.ArgumentParser(description="Delegate a purchase to the buyer agent.")
    ap.add_argument("--text", help="natural-language instruction (parsed by Gemini)")
    ap.add_argument("--query", help="what to buy")
    ap.add_argument("--budget", type=float, default=30.0, help="max USDC to spend")
    ap.add_argument("--ship", default=settings.default_ship_to, help="shipping address")
    args = ap.parse_args()

    if args.text:
        result = flow.buy_from_text(args.text)
    else:
        result = flow.buy(args.query or "wireless earbuds", args.budget, args.ship)

    print(json.dumps(result, indent=2, ensure_ascii=False))

    conf = result.get("confirmation", {})
    if conf.get("status") == "paid":
        print("\n✅ PAID — no human approval used.")
        print(f"   tx:       {conf.get('txSignature')}")
        print(f"   explorer: {conf.get('explorer')}")
        print(f"   shopify:  {conf.get('shopifyOrderId')}")
    else:
        print(f"\n⚠️  status = {conf.get('status') or result.get('reason')}")


if __name__ == "__main__":
    main()
