"""ADK-native interface for the buyer agent.

Run interactively with:  `adk run agentic_broker/buyer`  (or `adk web`).
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any

from google.adk.agents import LlmAgent
from google.adk.tools import ToolContext

from ..common.config import settings
from ..common.contracts import (
    StructuredShippingAddress,
    format_shipping_address,
)
from . import tools as buyer_tools

CHAT_REQUEST_STATE = "relay:chat_request"
CHAT_IDENTITY_WALLET_STATE = "relay:chat_identity_wallet"
CHAT_IDENTITY_EMAIL_STATE = "relay:chat_identity_email"
CHAT_APPROVAL_SIGNATURE_STATE = "relay:chat_approval_tx_signature"

INSTRUCTION = """\
You are Relay, a conversational BUYER agent acting for a human. You have
multi-turn memory and must use tools for every catalog, payment, and order fact.
Never invent products, stock, prices, order IDs, or transaction proofs.

Conversation policy:
1. A usable shopping request needs a positive maximum budget in USDC and a
   complete buyer-supplied structured shipping address: recipient name,
   address1, city, Shopify-compatible province/state code, ISO-2 country code,
   and postal/ZIP code.
   Address2 and phone are optional. Ask short follow-up questions for every
   missing required field. Never infer, default, or invent address values.
2. Once both are known, call `search_catalog`. Compare up to three suitable
   candidates in your own concise words, including meaningful price and stock
   differences. Use the exact tool results. For follow-ups such as "anything
   cheaper?" or "how much stock is left?", use the remembered results or call
   `search_catalog` again with a refined query/budget.
   When nothing in the catalog matches, `search_catalog` sources the product
   from the supplier pool and adds it to the live catalog by itself. So call
   the tool — never tell the human that a product cannot be added, that you
   are unable to change the catalog, or that they must pick something else.
   Only report a limit when the tool actually reports external sourcing
   unavailable, and then distinguish fallbackCatalog items from real matches.
3. Searching and comparing is non-binding. Do not spend funds until the human
   explicitly selects an item or delegates the choice with language such as
   "buy it", "choose the cheapest", or "go ahead". A fully explicit initial
   command beginning with "buy" also counts as delegation.
4. After explicit delegation, call `request_quote` with the exact selected
   product title as the query, the user's maximum budget, the legacy one-line
   destination, and every structured address field. If the quote exceeds the
   budget, stop and explain.
5. Otherwise call `authorize_payment` with the quote's payTo, price.amount, and
   reference. This signs and sends USDC autonomously; never ask for a wallet
   click or send the human to Shopify checkout.
6. Immediately call `confirm_settlement` with the quote's orderRef/reference
   and the returned txSignature.
7. Only say a purchase completed when settlement status is exactly "paid".
   Include the product, amount, Shopify order ID, and Solana explorer link.
8. For order questions, call `get_order_status` with an orderRef or Shopify
   order name. State `supplierOrder.status` and its message exactly. Never imply
   that Shopify fulfillment or a demo value represents a real supplier order or
   parcel, and never promise future actions you cannot perform.

Be natural and concise. Use plain text only, without Markdown. The user should
see your own response text alongside structured product cards or payment
progress supplied by the API.
"""


def payment_gate_response(
    identity_wallet: str | None,
    approval_signature: str | None,
    *,
    chat_request: bool = True,
) -> dict[str, Any] | None:
    """Build an executable refusal for an unverified storefront principal."""
    if not chat_request or not identity_wallet:
        return {
            "status": "auth-required",
            "authRequired": True,
            "paymentBlocked": True,
            "reason": (
                "A verified Clerk session is required before storefront "
                "quotes, payments, or settlement. Sign in to buy; catalog "
                "search and comparison remain available."
            ),
            "action": "sign-in",
        }
    if not approval_signature:
        return {
            "status": "approval-required",
            "authRequired": False,
            "paymentBlocked": True,
            "reason": (
                "Approve a USDC spending limit from the verified Clerk wallet "
                "before requesting a payment quote."
            ),
            "action": "approve-delegation",
        }
    return None


def _payment_gate(
    tool_context: ToolContext,
) -> tuple[str, str] | dict[str, Any]:
    """Return the server-verified chat principal or an executable refusal.

    ``Runner.run`` executes tools on a background thread, so a ContextVar set by
    the FastAPI request thread is not a security boundary.  The conversation
    runner copies only server-derived values into ADK session state for each
    turn; every spending-capable tool checks those values before doing any work.
    """
    wallet = str(
        tool_context.state.get(CHAT_IDENTITY_WALLET_STATE, "")
    ).strip()
    approval_signature = str(
        tool_context.state.get(CHAT_APPROVAL_SIGNATURE_STATE, "")
    ).strip()
    refusal = payment_gate_response(
        wallet,
        approval_signature,
        chat_request=bool(tool_context.state.get(CHAT_REQUEST_STATE)),
    )
    if refusal:
        return refusal
    return wallet, approval_signature


def search_catalog(
    query: str,
    budget: float,
    tool_context: ToolContext,
) -> dict[str, Any]:
    """Search the live catalog for in-stock products within ``budget`` USDC."""
    result = buyer_tools.search_catalog(query, budget)
    tool_context.state["relay:last_search"] = result
    tool_context.state["relay:last_budget"] = budget
    return result


def request_quote(
    query: str,
    budget: float,
    ship_to: str,
    tool_context: ToolContext,
    shipping_name: str = "",
    address1: str = "",
    city: str = "",
    province: str = "",
    country: str = "",
    zip_code: str = "",
    address2: str = "",
    phone: str = "",
) -> dict[str, Any]:
    """Request an agent-native quote for an explicitly selected product."""
    principal = _payment_gate(tool_context)
    if isinstance(principal, dict):
        return principal
    identity_wallet, approval_signature = principal

    required = {
        "shipping_name": shipping_name,
        "address1": address1,
        "city": city,
        "province": province,
        "country": country,
        "zip_code": zip_code,
    }
    missing = [name for name, value in required.items() if not value.strip()]
    if missing:
        raise ValueError(
            "Complete buyer-supplied shipping address required; missing "
            + ", ".join(missing)
        )
    shipping_address = StructuredShippingAddress(
        name=shipping_name.strip(),
        address1=address1.strip(),
        address2=address2.strip() or None,
        city=city.strip(),
        province=province.strip(),
        country=country.strip().upper(),
        zip=zip_code.strip(),
        phone=phone.strip() or None,
    )
    ship_to = format_shipping_address(shipping_address)
    # Re-establish the storefront context in the ADK tool thread.  This keeps
    # the delegation proof check local to the call that can issue a quote.
    with buyer_tools.storefront_context(
        identity_wallet,
        approval_signature,
        str(tool_context.state.get(CHAT_IDENTITY_EMAIL_STATE, "")).strip()
        or None,
    ):
        quote = buyer_tools.request_quote(
            query,
            budget,
            ship_to,
            delegator=identity_wallet,
            shipping_address=shipping_address,
        )
    quoted_amount = Decimal(str(quote["price"]["amount"]))
    ceiling = Decimal(str(budget))
    if quoted_amount > ceiling:
        raise ValueError(
            f"quote {quoted_amount} USDC exceeds budget {ceiling} USDC"
        )
    tool_context.state["relay:last_budget"] = budget
    tool_context.state["relay:last_ship_to"] = ship_to
    tool_context.state["relay:last_shipping_address"] = (
        shipping_address.model_dump(exclude_none=True)
    )
    tool_context.state["relay:last_quote"] = quote
    return quote


def authorize_payment(
    pay_to: str,
    amount: str,
    reference: str,
    tool_context: ToolContext,
) -> dict[str, Any]:
    """Autonomously sign the exact in-session quote after budget validation."""
    principal = _payment_gate(tool_context)
    if isinstance(principal, dict):
        return principal
    identity_wallet, _ = principal

    quote = tool_context.state.get("relay:last_quote")
    budget = tool_context.state.get("relay:last_budget")
    if not isinstance(quote, dict) or budget is None:
        raise ValueError("request_quote must succeed before authorize_payment")
    if (
        quote.get("payTo") != pay_to
        or quote.get("reference") != reference
        or str(quote.get("price", {}).get("amount")) != str(amount)
    ):
        raise ValueError("payment arguments do not match the active quote")
    if Decimal(str(amount)) > Decimal(str(budget)):
        raise ValueError(f"payment {amount} USDC exceeds budget {budget} USDC")

    payment = buyer_tools.authorize_payment(
        pay_to,
        amount,
        reference,
        quote.get("ap2Mandates"),
        delegator=identity_wallet,
    )
    tool_context.state["relay:last_payment"] = payment
    return payment


def confirm_settlement(
    order_ref: str,
    reference: str,
    tx_signature: str,
    tool_context: ToolContext,
) -> dict[str, Any]:
    """Verify the exact in-session payment and record the Shopify order."""
    principal = _payment_gate(tool_context)
    if isinstance(principal, dict):
        return principal

    quote = tool_context.state.get("relay:last_quote")
    payment = tool_context.state.get("relay:last_payment")
    if not isinstance(quote, dict) or not isinstance(payment, dict):
        raise ValueError(
            "request_quote and authorize_payment must succeed before settlement"
        )
    if quote.get("orderRef") != order_ref or quote.get("reference") != reference:
        raise ValueError("settlement arguments do not match the active quote")
    if payment.get("txSignature") != tx_signature:
        raise ValueError("settlement signature does not match the active payment")

    mandates = {
        **quote.get("ap2Mandates", {}),
        **payment.get("ap2Mandates", {}),
    }
    confirmation = buyer_tools.confirm_settlement(
        order_ref,
        reference,
        tx_signature,
        mandates,
        human_customer=True,
        customer_email=(
            str(tool_context.state.get(CHAT_IDENTITY_EMAIL_STATE, "")).strip()
            or None
        ),
    )
    tool_context.state["relay:last_confirmation"] = confirmation
    return confirmation


def get_order_status(identifier: str) -> dict[str, Any]:
    """Get a Relay order by orderRef or Shopify order name."""
    return buyer_tools.get_order_status(identifier)


root_agent = LlmAgent(
    model=settings.gemini_model,
    name="buyer",
    description=(
        "Conversational buyer that searches a live catalog and autonomously "
        "settles delegated USDC purchases."
    ),
    instruction=INSTRUCTION,
    tools=[
        search_catalog,
        request_quote,
        authorize_payment,
        confirm_settlement,
        get_order_status,
    ],
)
