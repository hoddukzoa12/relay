"""Pydantic mirror of packages/shared (PRD §6). Keep in sync with the TS types."""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator

Network = Literal["solana-devnet", "solana-mainnet"]
PaymentStatus = Literal["pending", "paid", "expired", "invalid"]
VerificationFailureReason = Literal[
    "unknown_reference",
    "transaction_not_found",
    "transaction_not_confirmed",
    "transaction_failed",
    "amount_mismatch",
    "recipient_mismatch",
    "reference_mismatch",
    "transfer_mismatch",
    "malformed_transaction",
    "validation_failed",
]
INTENT_MANDATE_DATA_KEY = "ap2.mandates.IntentMandate"
CART_MANDATE_DATA_KEY = "ap2.mandates.CartMandate"
PAYMENT_MANDATE_DATA_KEY = "ap2.mandates.PaymentMandate"


class Money(BaseModel):
    amount: str = Field(pattern=r"^\d+(\.\d{1,6})?$")
    currency: Literal["USDC"] = "USDC"


class StructuredShippingAddress(BaseModel):
    """Buyer-supplied destination; never synthesize missing supplier fields."""

    name: str = Field(min_length=1)
    address1: str = Field(min_length=1)
    address2: Optional[str] = Field(default=None, min_length=1)
    city: str = Field(min_length=1)
    province: str = Field(min_length=1)
    country: str = Field(pattern=r"^[A-Z]{2}$")
    zip: str = Field(min_length=3)
    phone: Optional[str] = Field(default=None, min_length=5)

    @field_validator(
        "name",
        "address1",
        "address2",
        "city",
        "province",
        "zip",
        "phone",
    )
    @classmethod
    def reject_placeholders(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return value
        if value != value.strip():
            raise ValueError("shipping address values must be trimmed")
        if value.lower() in {
            "n/a",
            "na",
            "none",
            "unknown",
            "test",
            "testing",
            "placeholder",
            "todo",
            "tbd",
            "-",
        }:
            raise ValueError("placeholder shipping address values are forbidden")
        return value


def format_shipping_address(address: StructuredShippingAddress) -> str:
    """Create the retained free-text representation from structured truth."""
    return ", ".join(
        value
        for value in (
            address.name,
            address.address1,
            address.address2,
            address.city,
            address.province,
            address.zip,
            address.country,
        )
        if value
    )


class PurchaseIntent(BaseModel):
    """A2A — buyer -> shopping (Step 1)."""

    query: str
    budget: Money
    shipTo: str
    shippingAddress: Optional[StructuredShippingAddress] = None


class SupplierCostSnapshot(BaseModel):
    """Admin-only, dated DSers supplier-cost evidence for one exact variant."""

    amount: str = Field(pattern=r"^\d+(\.\d{1,6})?$")
    currency: Literal["USD"] = "USD"
    source: Literal["dsers_mcp_snapshot", "relay_demo_catalog"]
    capturedAt: str = Field(min_length=1)
    shipTo: str = Field(min_length=2)
    supplierUrl: Optional[str] = None


class CatalogProduct(BaseModel):
    """Real Shopify variant returned by the commerce catalog endpoint."""

    productId: str
    variantId: str
    sku: str
    title: str
    description: str
    price: str = Field(pattern=r"^\d+(\.\d{1,6})?$")
    inventoryQuantity: int
    status: Literal["ACTIVE"]
    tags: list[str]
    supplierCost: Optional[SupplierCostSnapshot] = None


def _disabled_supplier_order() -> "SupplierOrder":
    return SupplierOrder(
        provider="dsers",
        status="disabled",
        ref=None,
        message=(
            "Supplier fulfillment is disabled; no structured Shopify "
            "shipping address or supplier order was created."
        ),
    )


class SupplierOrder(BaseModel):
    """Supplier state; pending may precede a downstream reference readback."""

    provider: Literal["dsers"] = "dsers"
    status: Literal[
        "disabled",
        "blocked",
        "not_connected",
        "pending",
        "submitted",
        "confirmed",
        "failed",
    ]
    ref: Optional[str] = Field(default=None, min_length=1)
    message: str = Field(min_length=1)


class CustomerAssociation(BaseModel):
    """Shopify customer-link result for an authenticated human purchase."""

    status: Literal["linked", "unlinked"]
    customerId: Optional[str] = Field(default=None, min_length=1)
    message: str = Field(min_length=1)


class CatalogProductsResponse(BaseModel):
    products: list[CatalogProduct]


class WalletOrder(BaseModel):
    shopifyOrderId: str
    name: str
    status: str
    createdAt: str
    orderRef: str
    title: str
    amount: str
    buyerWallet: str
    txSignature: str
    explorer: str
    supplierOrder: SupplierOrder = Field(
        default_factory=_disabled_supplier_order
    )


class WalletOrdersResponse(BaseModel):
    orders: list[WalletOrder]


class OrderLineItem(BaseModel):
    title: str
    sku: str
    quantity: int = Field(gt=0)


class OnChainProof(BaseModel):
    txSignature: str = Field(min_length=1)
    explorer: str


class PaymentProof(OnChainProof):
    reference: Optional[str] = Field(default=None, min_length=1)


class RefundState(BaseModel):
    status: Literal["not_refunded", "refunded"]
    reference: Optional[str] = None
    txSignature: Optional[str] = None
    explorer: Optional[str] = None


class TrackingInfo(BaseModel):
    provider: Literal["shopify", "easypost"]
    carrier: str = Field(min_length=1)
    trackingNumber: str = Field(min_length=1)
    status: str = Field(min_length=1)
    statusDetail: Optional[str] = None
    trackingUrl: Optional[str] = None
    estimatedDeliveryAt: Optional[str] = None
    demo: bool
    message: str = Field(min_length=1)


class OrderStatus(BaseModel):
    shopifyOrderId: str
    orderRef: str
    name: str
    financialStatus: str
    fulfillmentStatus: str
    lineItems: list[OrderLineItem] = Field(min_length=1)
    amount: Money
    payment: PaymentProof
    refund: RefundState
    supplierOrder: SupplierOrder = Field(
        default_factory=_disabled_supplier_order
    )
    tracking: Optional[TrackingInfo] = None


class RefundResult(BaseModel):
    shopifyOrderId: str
    orderRef: str
    name: str
    status: Literal["refunded"]
    financialStatus: Literal["REFUNDED"]
    amount: Money
    payment: PaymentProof
    refund: PaymentProof
    replayed: bool


class FulfillmentResult(BaseModel):
    shopifyOrderId: str
    orderRef: str
    name: str
    fulfillmentStatus: Literal["FULFILLED"]
    tracking: TrackingInfo
    replayed: bool


class PaymentRequest(BaseModel):
    """Step 3 — shopping -> buyer. The agent-native payment request."""

    productId: str
    title: str
    price: Money
    payTo: str
    reference: str
    orderRef: str
    network: Network
    expiresAt: str
    solanaPayUrl: Optional[str] = None


class TokenDelegation(BaseModel):
    """SPL delegation proof embedded in a browser-present AP2 intent."""

    delegator: str
    delegateAuthority: str
    allowanceRemaining: Money
    approvalTxSignature: str = Field(min_length=1)


class DelegationStatus(BaseModel):
    """Current on-chain SPL delegation state; never an application ledger."""

    active: bool
    delegator: str
    delegateAuthority: str
    allowanceRemaining: Money
    balance: Money
    sourceTokenAccount: str
    usdcMint: str
    network: Network


class DelegationTransaction(BaseModel):
    """Agent-fee-paid transaction awaiting the delegator's one signature."""

    action: Literal["approve", "revoke"]
    delegator: str
    delegateAuthority: str
    allowanceRemaining: Money
    transaction: str = Field(min_length=1)
    blockhash: str = Field(min_length=1)
    lastValidBlockHeight: int = Field(gt=0)


class DelegationApprovalRequired(BaseModel):
    """Fail-closed response with the user's one-time approval entry point."""

    status: Literal["approval-required"] = "approval-required"
    reason: str = Field(min_length=1)
    delegator: str
    requiredAmount: Money
    allowanceRemaining: Money
    balance: Money
    approvalUrl: str


class IntentMandate(BaseModel):
    """AP2 buyer authorization for an autonomous purchase intent."""

    user_cart_confirmation_required: bool
    natural_language_description: str
    merchants: Optional[list[str]] = None
    skus: Optional[list[str]] = None
    requires_refundability: Optional[bool] = False
    price_ceiling: Money
    ship_to: str
    shipping_address: Optional[StructuredShippingAddress] = None
    intent_expiry: str
    # Present when a signed-in human delegates; absent on the legacy agent-only
    # path, where the configured payments buyer wallet remains the signer.
    signer_wallet: Optional[str] = None
    delegator: Optional[str] = None
    delegateAuthority: Optional[str] = None
    allowanceRemaining: Optional[Money] = None
    approvalTxSignature: Optional[str] = Field(default=None, min_length=1)
    signature: str = Field(min_length=1)


class CartItem(BaseModel):
    sku: str
    variant_id: Optional[str] = None
    name: str
    price: Money


class CartContents(BaseModel):
    """AP2 cart details signed by the merchant wallet."""

    id: str
    user_cart_confirmation_required: bool
    cart_items: list[CartItem] = Field(min_length=1)
    total: Money
    shipping: Money
    tax: Money
    refund_period: int = Field(ge=0)
    cart_expiry: str
    merchant_name: str
    payment_request: PaymentRequest
    intent_mandate_signature: str = Field(min_length=1)


class CartMandate(BaseModel):
    contents: CartContents
    signature: str = Field(min_length=1)


class PayerInfo(BaseModel):
    wallet_address: str
    ship_to: Optional[str] = None


class PaymentMandateContents(BaseModel):
    """AP2 payment authorization bound to the accepted intent and cart."""

    payment_mandate_id: str
    payment_details_id: str
    payment_method_token: str
    amount: Money
    merchant_name: str
    payer: PayerInfo
    timestamp: str
    cart_mandate_signature: str = Field(min_length=1)
    intent_mandate_signature: str = Field(min_length=1)


class PaymentMandate(BaseModel):
    payment_mandate_contents: PaymentMandateContents
    signature: str = Field(min_length=1)


class SettlementRequest(BaseModel):
    """A2A — buyer -> shopping (Step 7 handoff)."""

    orderRef: str
    reference: str
    txSignature: str


class OrderConfirmation(BaseModel):
    """Step 11 — shopping -> buyer."""

    orderRef: str
    status: PaymentStatus
    txSignature: Optional[str] = None
    explorer: Optional[str] = None
    shopifyOrderId: Optional[str] = None
    supplierOrder: SupplierOrder = Field(
        default_factory=_disabled_supplier_order
    )
    customerAssociation: Optional[CustomerAssociation] = None


class VerificationResult(BaseModel):
    """Payments-service result after validating a reference on-chain."""

    status: PaymentStatus
    txSignature: Optional[str] = None
    explorer: Optional[str] = None
    amount: Optional[str] = None
    reason: Optional[VerificationFailureReason] = None
