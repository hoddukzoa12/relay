"""Pydantic mirror of packages/shared (PRD §6). Keep in sync with the TS types."""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field

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


class PurchaseIntent(BaseModel):
    """A2A — buyer -> shopping (Step 1)."""

    query: str
    budget: Money
    shipTo: str


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


class CatalogProductsResponse(BaseModel):
    products: list[CatalogProduct]


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


class IntentMandate(BaseModel):
    """AP2 buyer authorization for an autonomous purchase intent."""

    user_cart_confirmation_required: bool
    natural_language_description: str
    merchants: Optional[list[str]] = None
    skus: Optional[list[str]] = None
    requires_refundability: Optional[bool] = False
    price_ceiling: Money
    ship_to: str
    intent_expiry: str
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


class VerificationResult(BaseModel):
    """Payments-service result after validating a reference on-chain."""

    status: PaymentStatus
    txSignature: Optional[str] = None
    explorer: Optional[str] = None
    amount: Optional[str] = None
    reason: Optional[VerificationFailureReason] = None
