"""Multi-turn ADK runner and graceful deterministic chat fallback."""
from __future__ import annotations

from dataclasses import dataclass, field
import logging
import re
from threading import Lock
from typing import Any

from google.adk.events import Event
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types

from ..common.config import settings
from ..common.contracts import (
    StructuredShippingAddress,
    format_shipping_address,
)
from . import flow
from . import tools as buyer_tools
from .agent import (
    CHAT_APPROVAL_SIGNATURE_STATE,
    CHAT_IDENTITY_EMAIL_STATE,
    CHAT_IDENTITY_WALLET_STATE,
    CHAT_REQUEST_STATE,
    payment_gate_response,
    root_agent,
)

_LOG = logging.getLogger(__name__)
_APP_NAME = "relay_buyer_chat"
_PAYMENT_TOOL_NAMES = (
    "request_quote",
    "authorize_payment",
    "confirm_settlement",
)


@dataclass
class TurnTrace:
    """Tool and model output collected from one ADK turn."""

    reply: str = ""
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    tool_results: list[dict[str, Any]] = field(default_factory=list)

    def add_event(self, event: Event) -> None:
        for function_call in event.get_function_calls():
            self.tool_calls.append(
                {
                    "name": function_call.name or "",
                    "args": dict(function_call.args or {}),
                }
            )
        for function_response in event.get_function_responses():
            self.tool_results.append(
                {
                    "name": function_response.name or "",
                    "result": dict(function_response.response or {}),
                }
            )
        if event.is_final_response() and event.content and event.content.parts:
            text = "".join(
                part.text or "" for part in event.content.parts if part.text
            ).strip()
            if text:
                self.reply = text

    def last_result(self, name: str) -> dict[str, Any] | None:
        for entry in reversed(self.tool_results):
            if entry["name"] == name and isinstance(entry["result"], dict):
                return entry["result"]
        return None


class AgentTurnError(RuntimeError):
    """Preserve completed tool calls when Gemini fails mid-turn."""

    def __init__(self, cause: Exception, trace: TurnTrace):
        super().__init__(str(cause))
        self.cause = cause
        self.trace = trace


@dataclass
class _FallbackState:
    query: str = ""
    budget: float = settings.default_budget_usdc
    products: list[dict[str, Any]] = field(default_factory=list)
    shipping_address: StructuredShippingAddress | None = None
    address_fields: dict[str, str] = field(default_factory=dict)
    pending_product: dict[str, Any] | None = None


class ConversationService:
    """Run one in-memory ADK conversation per caller-provided session ID."""

    def __init__(self) -> None:
        self._session_service = InMemorySessionService()
        self._runner = Runner(
            agent=root_agent,
            app_name=_APP_NAME,
            session_service=self._session_service,
            auto_create_session=True,
        )
        self._locks_guard = Lock()
        self._session_locks: dict[str, Lock] = {}
        self._fallback_states: dict[str, _FallbackState] = {}

    def respond(
        self,
        session_id: str,
        message: str,
        *,
        identity_wallet: str | None = None,
        identity_email: str | None = None,
        approval_tx_signature: str | None = None,
    ) -> dict[str, Any]:
        """Return model text plus structured products/payment progress."""
        with (
            self._session_lock(session_id),
            buyer_tools.storefront_context(
                identity_wallet,
                approval_tx_signature,
                identity_email,
            ),
        ):
            if not settings.google_api_key:
                return self._fallback_response(
                    session_id,
                    message,
                    reason="GOOGLE_API_KEY is not configured",
                    identity_wallet=identity_wallet,
                    identity_email=identity_email,
                    approval_tx_signature=approval_tx_signature,
                )
            try:
                trace = self._run_agent(
                    session_id,
                    message,
                    identity_wallet=identity_wallet,
                    identity_email=identity_email,
                    approval_tx_signature=approval_tx_signature,
                )
                if not trace.reply:
                    raise AgentTurnError(
                        RuntimeError("Gemini returned no final response"), trace
                    )
                return self._response_from_trace(
                    session_id=session_id,
                    trace=trace,
                    mode="ai",
                )
            except AgentTurnError as exc:
                _LOG.warning("[chat] Gemini turn failed: %s", exc.cause)
                if exc.trace.tool_results:
                    return self._recover_partial_turn(
                        session_id=session_id,
                        trace=exc.trace,
                        reason=str(exc.cause),
                        identity_wallet=identity_wallet,
                    )
                return self._fallback_response(
                    session_id,
                    message,
                    reason=str(exc.cause),
                    identity_wallet=identity_wallet,
                    identity_email=identity_email,
                    approval_tx_signature=approval_tx_signature,
                )
            except Exception as exc:  # noqa: BLE001
                _LOG.warning("[chat] Gemini unavailable; using fallback: %s", exc)
                return self._fallback_response(
                    session_id,
                    message,
                    reason=str(exc),
                    identity_wallet=identity_wallet,
                    identity_email=identity_email,
                    approval_tx_signature=approval_tx_signature,
                )

    def _session_lock(self, session_id: str) -> Lock:
        with self._locks_guard:
            return self._session_locks.setdefault(session_id, Lock())

    def _run_agent(
        self,
        session_id: str,
        message: str,
        *,
        identity_wallet: str | None,
        identity_email: str | None,
        approval_tx_signature: str | None,
    ) -> TurnTrace:
        trace = TurnTrace()
        user_id = f"storefront:{session_id}"
        content = types.Content(
            role="user",
            parts=[types.Part.from_text(text=message)],
        )
        try:
            for event in self._runner.run(
                user_id=user_id,
                session_id=session_id,
                new_message=content,
                state_delta={
                    # These values come only from the Clerk-verifying FastAPI
                    # boundary. Empty strings explicitly clear authenticated
                    # state when an anonymous caller reuses a session ID.
                    CHAT_REQUEST_STATE: True,
                    CHAT_IDENTITY_WALLET_STATE: identity_wallet or "",
                    CHAT_IDENTITY_EMAIL_STATE: identity_email or "",
                    CHAT_APPROVAL_SIGNATURE_STATE: (
                        approval_tx_signature or ""
                    ),
                },
            ):
                trace.add_event(event)
        except Exception as exc:  # noqa: BLE001
            raise AgentTurnError(exc, trace) from exc
        return trace

    def _response_from_trace(
        self,
        *,
        session_id: str,
        trace: TurnTrace,
        mode: str,
        fallback_reason: str | None = None,
    ) -> dict[str, Any]:
        search = trace.last_result("search_catalog") or {}
        payment_gate = self._payment_gate(trace)
        quote = (
            None if payment_gate else trace.last_result("request_quote")
        )
        payment = (
            None if payment_gate else trace.last_result("authorize_payment")
        )
        confirmation = (
            None if payment_gate else trace.last_result("confirm_settlement")
        )
        order = trace.last_result("get_order_status")
        display_products = (
            search.get("products")
            or search.get("fallbackCatalog")
            or search.get("closestOverBudget", [])
        )
        response: dict[str, Any] = {
            "sessionId": session_id,
            "reply": trace.reply,
            "mode": mode,
            "toolCalls": [
                call["name"] for call in trace.tool_calls if call["name"]
            ],
            "products": display_products,
        }
        if search:
            response["search"] = search
        if quote:
            response["quote"] = quote
        if payment:
            response["payment"] = payment
        if confirmation:
            response["confirmation"] = confirmation
        if order:
            response["order"] = order
        if payment_gate:
            response.update(
                {
                    "paymentBlocked": True,
                    "authRequired": bool(payment_gate.get("authRequired")),
                    "paymentGate": payment_gate,
                }
            )
        if fallback_reason:
            response["fallbackReason"] = fallback_reason[:240]
        return response

    @staticmethod
    def _payment_gate(trace: TurnTrace) -> dict[str, Any] | None:
        for entry in reversed(trace.tool_results):
            if entry["name"] not in _PAYMENT_TOOL_NAMES:
                continue
            result = entry.get("result")
            if (
                isinstance(result, dict)
                and result.get("paymentBlocked") is True
            ):
                return result
        return None

    def _recover_partial_turn(
        self,
        *,
        session_id: str,
        trace: TurnTrace,
        reason: str,
        identity_wallet: str | None,
    ) -> dict[str, Any]:
        """Finish settlement after a sent payment, but never send twice."""
        quote = trace.last_result("request_quote")
        payment = trace.last_result("authorize_payment")
        confirmation = trace.last_result("confirm_settlement")
        if quote and payment and not confirmation and payment.get("txSignature"):
            if not identity_wallet:
                refusal = payment_gate_response(None, None)
                if refusal:
                    trace.tool_results.append(
                        {"name": "confirm_settlement", "result": refusal}
                    )
                trace.reply = self._fallback_reply_for_trace(trace)
                return self._response_from_trace(
                    session_id=session_id,
                    trace=trace,
                    mode="fallback",
                    fallback_reason=reason,
                )
            mandates = {
                **quote.get("ap2Mandates", {}),
                **payment.get("ap2Mandates", {}),
            }
            try:
                confirmation = buyer_tools.confirm_settlement(
                    quote["orderRef"],
                    quote["reference"],
                    payment["txSignature"],
                    mandates,
                )
                trace.tool_calls.append(
                    {"name": "confirm_settlement", "args": {}}
                )
                trace.tool_results.append(
                    {"name": "confirm_settlement", "result": confirmation}
                )
            except Exception as exc:  # noqa: BLE001
                _LOG.error(
                    "[chat] payment sent but settlement recovery failed: %s",
                    exc,
                )
                reason = f"{reason}; settlement recovery failed: {exc}"

        trace.reply = self._fallback_reply_for_trace(trace)
        return self._response_from_trace(
            session_id=session_id,
            trace=trace,
            mode="fallback",
            fallback_reason=reason,
        )

    def _fallback_reply_for_trace(self, trace: TurnTrace) -> str:
        payment_gate = self._payment_gate(trace)
        if payment_gate:
            return str(
                payment_gate.get(
                    "reason",
                    "Sign in with Clerk before purchasing. Catalog search "
                    "and comparison remain available.",
                )
            )
        confirmation = trace.last_result("confirm_settlement") or {}
        quote = trace.last_result("request_quote") or {}
        payment = trace.last_result("authorize_payment") or {}
        search = trace.last_result("search_catalog") or {}
        order = trace.last_result("get_order_status") or {}
        if confirmation.get("status") == "paid":
            order_id = (
                confirmation.get("shopifyOrderId")
                or confirmation.get("orderRef")
                or quote.get("orderRef")
            )
            amount = quote.get("price", {}).get("amount", "")
            supplier = confirmation.get("supplierOrder", {})
            supplier_status = str(
                supplier.get("status", "disabled")
            ).replace("_", " ")
            return (
                f"Payment complete for {quote.get('title', 'the selected item')} "
                f"at {amount} USDC. Shopify order {order_id} was recorded after "
                f"on-chain verification. Supplier order is {supplier_status}; "
                f"{supplier.get('message', 'no supplier tracking is available.')}"
            )
        if payment.get("txSignature"):
            return (
                "The USDC transfer was sent, but settlement could not be "
                "confirmed yet. I kept the transaction proof for a safe retry."
            )
        if quote:
            return (
                f"I prepared a {quote.get('price', {}).get('amount', '')} USDC "
                "quote, but the model became unavailable before any payment was sent."
            )
        if order:
            supplier = order.get("supplierOrder", {})
            supplier_status = str(
                supplier.get("status", "disabled")
            ).replace("_", " ")
            return (
                f"Order {order.get('name') or order.get('orderRef')} is "
                f"{order.get('financialStatus', 'available')} with fulfillment "
                f"status {order.get('fulfillmentStatus', 'unknown')}; supplier "
                f"order is {supplier_status}. "
                f"{supplier.get('message', 'No supplier tracking is available.')}"
            )
        products = search.get("products", [])
        if products:
            return (
                f"I found {len(products)} in-stock catalog "
                f"{'match' if len(products) == 1 else 'matches'} within budget."
            )
        sourcing = search.get("externalSourcing", {})
        if sourcing.get("status") == "unavailable":
            return (
                "I could not source a new matching product right now. "
                "The existing catalog and autonomous USDC checkout remain available."
            )
        return "The AI model is temporarily unavailable. No funds were sent."

    def _fallback_response(
        self,
        session_id: str,
        message: str,
        *,
        reason: str,
        identity_wallet: str | None,
        identity_email: str | None,
        approval_tx_signature: str | None,
    ) -> dict[str, Any]:
        state = self._fallback_states.setdefault(session_id, _FallbackState())
        normalized = message.lower()
        state.address_fields.update(self._structured_shipping_fields(message))
        parsed_address = self._complete_shipping_address(state.address_fields)
        if parsed_address:
            state.shipping_address = parsed_address
        elif state.pending_product is not None and not self._is_purchase_request(
            normalized
        ):
            return {
                "sessionId": session_id,
                "reply": self._structured_address_prompt(state.address_fields),
                "mode": "fallback",
                "fallbackReason": reason[:240],
                "toolCalls": [],
                "products": state.products,
                "shippingAddressRequired": True,
            }

        resume_pending = state.pending_product is not None and parsed_address is not None
        if state.products and (
            self._is_purchase_request(normalized) or resume_pending
        ):
            product = state.pending_product or self._fallback_selection(
                message, state.products
            )
            if state.shipping_address is None:
                state.pending_product = product
                return {
                    "sessionId": session_id,
                    "reply": self._structured_address_prompt(
                        state.address_fields
                    ),
                    "mode": "fallback",
                    "fallbackReason": reason[:240],
                    "toolCalls": [],
                    "products": state.products,
                    "shippingAddressRequired": True,
                }
            state.pending_product = None
            ship_to = format_shipping_address(state.shipping_address)
            payment_gate = payment_gate_response(
                identity_wallet, approval_tx_signature
            )
            if payment_gate:
                return {
                    "sessionId": session_id,
                    "reply": payment_gate["reason"],
                    "mode": "fallback",
                    "fallbackReason": reason[:240],
                    "toolCalls": [],
                    "products": state.products,
                    "paymentBlocked": True,
                    "authRequired": payment_gate["authRequired"],
                    "paymentGate": payment_gate,
                }
            try:
                result = flow.buy(
                    query=str(product["title"]),
                    budget=state.budget,
                    ship_to=ship_to,
                    identity_wallet=identity_wallet,
                    identity_email=identity_email,
                    human_customer=identity_wallet is not None,
                    shipping_address=state.shipping_address,
                )
            except PermissionError as exc:
                return {
                    "sessionId": session_id,
                    "reply": (
                        f"{exc} Catalog search and comparison remain available "
                        "without payment authorization."
                    ),
                    "mode": "fallback",
                    "fallbackReason": reason[:240],
                    "toolCalls": [],
                    "products": state.products,
                    "paymentBlocked": True,
                }
            quote = result.get("quote", {})
            confirmation = result.get("confirmation", {})
            if result.get("ok"):
                supplier = confirmation.get("supplierOrder", {})
                reply = (
                    f"Payment complete for {quote.get('title', product['title'])} "
                    f"at {quote.get('price', {}).get('amount', '')} USDC. "
                    f"Shopify order {confirmation.get('shopifyOrderId') or confirmation.get('orderRef')} "
                    "was recorded after on-chain verification. "
                    f"{supplier.get('message', 'Supplier status is unavailable.')}"
                )
            else:
                reply = (
                    f"I could not complete the purchase: "
                    f"{result.get('reason', 'settlement did not reach paid')}. "
                    "No Shopify checkout was opened."
                )
            return {
                "sessionId": session_id,
                "reply": reply,
                "mode": "fallback",
                "fallbackReason": reason[:240],
                "toolCalls": ["deterministic_buy"],
                "products": [],
                **result,
            }

        if state.products and re.search(r"\bstock|inventory|left\b", normalized):
            stock = ", ".join(
                f"{product['title']}: {product['inventoryQuantity']} left"
                for product in state.products[:3]
            )
            return {
                "sessionId": session_id,
                "reply": f"Current Shopify inventory: {stock}.",
                "mode": "fallback",
                "fallbackReason": reason[:240],
                "toolCalls": [],
                "products": state.products,
            }

        budget = self._budget(message) or state.budget
        search = buyer_tools.search_catalog(message, budget)
        products = search.get("products", [])
        display_products = (
            products
            or search.get("fallbackCatalog")
            or search.get("closestOverBudget", [])
        )
        state.query = message
        state.budget = budget
        state.products = products
        state.pending_product = None
        if products:
            reply = (
                f"I found {len(products)} in-stock catalog "
                f"{'match' if len(products) == 1 else 'matches'} within your "
                f"{budget:.2f} USDC cap. Choose one or delegate the choice."
            )
        elif search.get("externalSourcing", {}).get("status") == "unavailable":
            reply = (
                "I could not source a new matching product from DSers right "
                "now. The existing catalog shown below and autonomous USDC "
                "checkout remain available; no funds were sent."
            )
        else:
            reply = (
                f"Nothing in stock fits the {budget:.2f} USDC cap. "
                "No funds were sent."
            )
        return {
            "sessionId": session_id,
            "reply": reply,
            "mode": "fallback",
            "fallbackReason": reason[:240],
            "toolCalls": ["search_catalog"],
            "products": display_products,
            "search": search,
        }

    @staticmethod
    def _budget(message: str) -> float | None:
        patterns = (
            r"(?:under|below|less\s+than|up\s+to|max(?:imum)?|budget(?:\s+of)?)\s*\$?\s*(\d+(?:\.\d+)?)",
            r"\$\s*(\d+(?:\.\d+)?)",
            r"(\d+(?:\.\d+)?)\s*(?:dollars?|usd|usdc)\b",
        )
        for pattern in patterns:
            match = re.search(pattern, message, flags=re.IGNORECASE)
            if match:
                value = float(match.group(1))
                if value > 0:
                    return value
        return None

    @staticmethod
    def _is_purchase_request(normalized: str) -> bool:
        return bool(
            re.search(
                r"\b(?:buy|choose|pay|delegate|go\s+ahead|pick\s+(?:it|one|for me))\b",
                normalized,
            )
        )

    @staticmethod
    def _fallback_selection(
        message: str, products: list[dict[str, Any]]
    ) -> dict[str, Any]:
        normalized = message.lower()
        for product in products:
            if str(product["title"]).lower() in normalized:
                return product
        if re.search(r"\b(?:second|2nd|number\s*2)\b", normalized) and len(products) > 1:
            return products[1]
        if re.search(r"\b(?:third|3rd|number\s*3)\b", normalized) and len(products) > 2:
            return products[2]
        if "cheap" in normalized:
            return min(products, key=lambda product: float(product["price"]))
        return products[0]

    @staticmethod
    def _structured_address_prompt(fields: dict[str, str]) -> str:
        required = ("name", "address1", "city", "province", "country", "zip")
        missing = [field for field in required if not fields.get(field)]
        missing_text = (
            ", ".join(missing)
            if missing
            else "one or more invalid values (country must be ISO-2)"
        )
        return (
            "Before payment, provide the real shipping fields exactly as "
            "'Name: ...; Address1: ...; City: ...; Province: ...; "
            "Country: KR; ZIP: ...'. Address2 and Phone are optional. "
            f"Missing or invalid: {missing_text}. "
            "I will not invent missing address values."
        )

    @staticmethod
    def _structured_shipping_fields(message: str) -> dict[str, str]:
        aliases = {
            "name": "name",
            "recipient": "name",
            "address1": "address1",
            "address 1": "address1",
            "address2": "address2",
            "address 2": "address2",
            "city": "city",
            "province": "province",
            "state": "province",
            "country": "country",
            "zip": "zip",
            "postal": "zip",
            "postal code": "zip",
            "phone": "phone",
        }
        field_names = "|".join(
            sorted((re.escape(name) for name in aliases), key=len, reverse=True)
        )
        values: dict[str, str] = {}
        for match in re.finditer(
            rf"(?:^|[;\n])\s*({field_names})\s*[:=]\s*([^;\n]+)",
            message,
            flags=re.IGNORECASE,
        ):
            key = aliases[match.group(1).lower()]
            values[key] = match.group(2).strip()
        return values

    @staticmethod
    def _complete_shipping_address(
        values: dict[str, str],
    ) -> StructuredShippingAddress | None:
        required = {"name", "address1", "city", "province", "country", "zip"}
        if not required.issubset(values):
            return None
        normalized = {**values, "country": values["country"].upper()}
        try:
            return StructuredShippingAddress(**normalized)
        except ValueError:
            return None


_SERVICE = ConversationService()


def respond(
    session_id: str,
    message: str,
    *,
    identity_wallet: str | None = None,
    identity_email: str | None = None,
    approval_tx_signature: str | None = None,
) -> dict[str, Any]:
    """Process one chat turn through the process-wide conversation service."""
    return _SERVICE.respond(
        session_id,
        message,
        identity_wallet=identity_wallet,
        identity_email=identity_email,
        approval_tx_signature=approval_tx_signature,
    )
