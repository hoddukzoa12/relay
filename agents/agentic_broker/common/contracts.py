"""Pydantic mirror of packages/shared (PRD §6). Keep in sync with the TS types."""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field

Network = Literal["solana-devnet", "solana-mainnet"]
PaymentStatus = Literal["pending", "paid", "expired", "invalid"]


class Money(BaseModel):
    amount: str = Field(pattern=r"^\d+(\.\d{1,6})?$")
    currency: Literal["USDC"] = "USDC"


class PurchaseIntent(BaseModel):
    """A2A — buyer -> shopping (Step 1)."""

    query: str
    budget: Money
    shipTo: str


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
