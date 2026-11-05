"""Modelos de domínio. Valores monetários são sempre inteiros em centavos."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum


class OrderStatus(StrEnum):
    PREPARING = "preparing"
    OUT_FOR_DELIVERY = "out_for_delivery"
    DELIVERED = "delivered"
    CANCELLED = "cancelled"


class IssueType(StrEnum):
    MISSING_ITEM = "missing_item"
    WRONG_ITEM = "wrong_item"
    LATE_DELIVERY = "late_delivery"
    NOT_DELIVERED = "not_delivered"
    QUALITY = "quality"


class RefundStatus(StrEnum):
    APPROVED = "approved"
    PENDING_APPROVAL = "pending_approval"
    REJECTED = "rejected"


@dataclass(frozen=True)
class Customer:
    id: str
    name: str
    email: str
    phone: str
    created_at: datetime


@dataclass(frozen=True)
class MenuItem:
    id: str
    restaurant_id: str
    name: str
    price_cents: int
    allergens: tuple[str, ...] = ()


@dataclass(frozen=True)
class Restaurant:
    id: str
    name: str
    cuisine: str
    opens_at: str
    closes_at: str


@dataclass(frozen=True)
class OrderItem:
    id: str
    order_id: str
    menu_item_id: str
    name: str
    quantity: int
    unit_price_cents: int

    @property
    def total_cents(self) -> int:
        return self.quantity * self.unit_price_cents


@dataclass(frozen=True)
class Order:
    id: str
    customer_id: str
    restaurant_id: str
    status: OrderStatus
    placed_at: datetime
    eta: datetime
    delivered_at: datetime | None
    delivery_fee_cents: int
    items: tuple[OrderItem, ...] = field(default_factory=tuple)
    courier_lat: float | None = None
    courier_lng: float | None = None

    @property
    def subtotal_cents(self) -> int:
        return sum(item.total_cents for item in self.items)

    @property
    def total_cents(self) -> int:
        return self.subtotal_cents + self.delivery_fee_cents

    def minutes_late(self, now: datetime) -> int:
        """Atraso em minutos em relação ao ETA (entregue: até a entrega; em rota: até agora)."""
        reference = self.delivered_at or now
        delta = (reference - self.eta).total_seconds() / 60
        return max(0, int(delta))


@dataclass(frozen=True)
class Refund:
    id: str
    order_id: str
    customer_id: str
    amount_cents: int
    issue: IssueType
    status: RefundStatus
    rule_id: str
    item_ids: tuple[str, ...]
    created_at: datetime
    note: str = ""


@dataclass(frozen=True)
class Ticket:
    id: str
    customer_id: str
    order_id: str | None
    priority: str
    summary: str
    created_at: datetime
