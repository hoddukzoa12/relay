"""ADK-native interface for the shopping broker.

Run interactively with:  `adk run agentic_broker/shopping`  (or `adk web`).
The FastAPI server (server.py) drives the same tools deterministically for the
scored demo; this module is the conversational, LLM-driven face of the agent.
"""
from __future__ import annotations

from google.adk.agents import LlmAgent

from ..common.config import settings
from .tools import (
    fulfill_order,
    get_order_status,
    issue_payment_request,
    record_order,
    refund_order,
    source_and_price,
    track_order,
    verify_payment,
)

INSTRUCTION = """\
You are the SHOPPING BROKER — a headless merchant agent. A buyer agent asks you
to source a product within a budget. Your job:

1. Call `source_and_price` with the query and budget to pick a product and set a
   resale price from a real, in-stock catalog variant. The tool excludes
   products whose marked-up price exceeds the budget and raises an error when
   nothing fits; report that failure without inventing an alternative.
2. Call `issue_payment_request` with the selected `variantId` as `product_id`,
   the selected title and price, and a stable `order_ref`. Return its `payTo`,
   `amount`, and `reference` to the buyer. NEVER hand the buyer a web-checkout
   link — the buyer's wallet signs the request directly.
3. When the buyer reports payment, call `verify_payment` with the reference.
   Only proceed if status == "paid".
4. Call `record_order` with the same `variantId` as `product_id` and the `sku`
   returned by `source_and_price`, plus the verified payment and fulfillment
   fields. Then confirm with the explorer link.
5. For post-purchase requests, use `get_order_status` and `refund_order`. A
   refund is full-only and must return both payment and refund explorer proofs.
   Supplier fulfillment is default-off because enabling it can trigger a real
   charge through Shopify→DSers automation. Report `supplierOrder` exactly:
   disabled/blocked means no supplier order; pending is unconfirmed and still
   has no supplier ref. `fulfill_order` and `track_order` fail closed until a
   real downstream reference or carrier record exists. Never overclaim them.

Be concise. Never invent a transaction signature or claim payment you did not
verify on-chain.
"""

root_agent = LlmAgent(
    model=settings.gemini_model,
    name="shopping_broker",
    description="Sources products, issues USDC payment requests, verifies on-chain payments, records Shopify orders.",
    instruction=INSTRUCTION,
    tools=[
        source_and_price,
        issue_payment_request,
        verify_payment,
        record_order,
        get_order_status,
        refund_order,
        fulfill_order,
        track_order,
    ],
)
