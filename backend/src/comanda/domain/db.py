"""Persistência em SQLite. Toda leitura e escrita de dados passa por `Repository`."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from .models import (
    Customer,
    IssueType,
    MenuItem,
    Order,
    OrderItem,
    OrderStatus,
    Refund,
    RefundStatus,
    Restaurant,
    Ticket,
    TicketStatus,
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS customers (
    id         TEXT PRIMARY KEY,
    name       TEXT NOT NULL,
    email      TEXT NOT NULL,
    phone      TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS restaurants (
    id        TEXT PRIMARY KEY,
    name      TEXT NOT NULL,
    cuisine   TEXT NOT NULL,
    opens_at  TEXT NOT NULL,
    closes_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS menu_items (
    id            TEXT PRIMARY KEY,
    restaurant_id TEXT NOT NULL REFERENCES restaurants(id),
    name          TEXT NOT NULL,
    price_cents   INTEGER NOT NULL CHECK (price_cents > 0),
    allergens     TEXT NOT NULL DEFAULT '[]'
);
CREATE TABLE IF NOT EXISTS orders (
    id                 TEXT PRIMARY KEY,
    customer_id        TEXT NOT NULL REFERENCES customers(id),
    restaurant_id      TEXT NOT NULL REFERENCES restaurants(id),
    status             TEXT NOT NULL,
    placed_at          TEXT NOT NULL,
    eta                TEXT NOT NULL,
    delivered_at       TEXT,
    delivery_fee_cents INTEGER NOT NULL CHECK (delivery_fee_cents >= 0),
    courier_lat        REAL,
    courier_lng        REAL
);
CREATE TABLE IF NOT EXISTS order_items (
    id               TEXT PRIMARY KEY,
    order_id         TEXT NOT NULL REFERENCES orders(id),
    menu_item_id     TEXT NOT NULL REFERENCES menu_items(id),
    name             TEXT NOT NULL,
    quantity         INTEGER NOT NULL CHECK (quantity > 0),
    unit_price_cents INTEGER NOT NULL CHECK (unit_price_cents > 0)
);
CREATE TABLE IF NOT EXISTS refunds (
    id               TEXT PRIMARY KEY,
    order_id         TEXT NOT NULL REFERENCES orders(id),
    customer_id      TEXT NOT NULL REFERENCES customers(id),
    amount_cents     INTEGER NOT NULL CHECK (amount_cents > 0),
    issue            TEXT NOT NULL,
    status           TEXT NOT NULL,
    rule_id          TEXT NOT NULL,
    item_ids         TEXT NOT NULL DEFAULT '[]',
    created_at       TEXT NOT NULL,
    decided_by       TEXT,
    decided_at       TEXT,
    rejection_reason TEXT
);
CREATE TABLE IF NOT EXISTS tickets (
    id          TEXT PRIMARY KEY,
    customer_id TEXT NOT NULL REFERENCES customers(id),
    order_id    TEXT,
    priority    TEXT NOT NULL,
    summary     TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    status      TEXT NOT NULL
);
-- O valor sai da regra (ADR-0001) e ninguém o altera depois, nem o atendente humano (ADR-0007).
CREATE TRIGGER IF NOT EXISTS refunds_amount_immutable
BEFORE UPDATE OF amount_cents ON refunds
WHEN NEW.amount_cents IS NOT OLD.amount_cents
BEGIN
    SELECT RAISE(ABORT, 'O valor do reembolso não pode ser alterado.');
END;
CREATE INDEX IF NOT EXISTS idx_orders_customer ON orders(customer_id, placed_at);
CREATE INDEX IF NOT EXISTS idx_refunds_customer ON refunds(customer_id, created_at);
CREATE INDEX IF NOT EXISTS idx_refunds_order ON refunds(order_id);
"""


def _dt(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


class Repository:
    """Acesso a dados. Uma instância por banco; conexões curtas por operação."""

    def __init__(self, db_path: Path | str) -> None:
        self.db_path = Path(db_path)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def init_schema(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as conn:
            conn.executescript(SCHEMA)

    # --- relógio -------------------------------------------------------------

    def now(self) -> datetime:
        """Relógio do sistema. O seed fixa um "agora" para que os cenários sejam reprodutíveis."""
        with self.connect() as conn:
            row = conn.execute("SELECT value FROM meta WHERE key = 'now'").fetchone()
        if row:
            return datetime.fromisoformat(row["value"])
        return datetime.now(UTC)

    def set_now(self, value: datetime) -> None:
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO meta(key, value) VALUES ('now', ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (value.isoformat(),),
            )

    # --- escrita em lote (seed) ----------------------------------------------

    def insert_customer(self, c: Customer) -> None:
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO customers VALUES (?, ?, ?, ?, ?)",
                (c.id, c.name, c.email, c.phone, c.created_at.isoformat()),
            )

    def insert_restaurant(self, r: Restaurant, menu: list[MenuItem]) -> None:
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO restaurants VALUES (?, ?, ?, ?, ?)",
                (r.id, r.name, r.cuisine, r.opens_at, r.closes_at),
            )
            conn.executemany(
                "INSERT INTO menu_items VALUES (?, ?, ?, ?, ?)",
                [(m.id, m.restaurant_id, m.name, m.price_cents, json.dumps(list(m.allergens))) for m in menu],
            )

    def insert_order(self, o: Order) -> None:
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO orders VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    o.id,
                    o.customer_id,
                    o.restaurant_id,
                    o.status.value,
                    o.placed_at.isoformat(),
                    o.eta.isoformat(),
                    o.delivered_at.isoformat() if o.delivered_at else None,
                    o.delivery_fee_cents,
                    o.courier_lat,
                    o.courier_lng,
                ),
            )
            conn.executemany(
                "INSERT INTO order_items VALUES (?, ?, ?, ?, ?, ?)",
                [(i.id, i.order_id, i.menu_item_id, i.name, i.quantity, i.unit_price_cents) for i in o.items],
            )

    # --- leitura -------------------------------------------------------------

    def get_customer(self, customer_id: str) -> Customer | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM customers WHERE id = ?", (customer_id,)).fetchone()
        if not row:
            return None
        return Customer(
            id=row["id"],
            name=row["name"],
            email=row["email"],
            phone=row["phone"],
            created_at=datetime.fromisoformat(row["created_at"]),
        )

    def list_customer_ids(self) -> list[str]:
        with self.connect() as conn:
            return [r["id"] for r in conn.execute("SELECT id FROM customers ORDER BY id")]

    def get_restaurant(self, restaurant_id: str) -> tuple[Restaurant, list[MenuItem]] | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM restaurants WHERE id = ?", (restaurant_id,)).fetchone()
            if not row:
                return None
            items = conn.execute(
                "SELECT * FROM menu_items WHERE restaurant_id = ? ORDER BY name", (restaurant_id,)
            ).fetchall()
        restaurant = Restaurant(
            id=row["id"],
            name=row["name"],
            cuisine=row["cuisine"],
            opens_at=row["opens_at"],
            closes_at=row["closes_at"],
        )
        menu = [
            MenuItem(
                id=m["id"],
                restaurant_id=m["restaurant_id"],
                name=m["name"],
                price_cents=m["price_cents"],
                allergens=tuple(json.loads(m["allergens"])),
            )
            for m in items
        ]
        return restaurant, menu

    def _order_from_row(self, conn: sqlite3.Connection, row: sqlite3.Row) -> Order:
        items = conn.execute(
            "SELECT * FROM order_items WHERE order_id = ? ORDER BY id", (row["id"],)
        ).fetchall()
        return Order(
            id=row["id"],
            customer_id=row["customer_id"],
            restaurant_id=row["restaurant_id"],
            status=OrderStatus(row["status"]),
            placed_at=datetime.fromisoformat(row["placed_at"]),
            eta=datetime.fromisoformat(row["eta"]),
            delivered_at=_dt(row["delivered_at"]),
            delivery_fee_cents=row["delivery_fee_cents"],
            courier_lat=row["courier_lat"],
            courier_lng=row["courier_lng"],
            items=tuple(
                OrderItem(
                    id=i["id"],
                    order_id=i["order_id"],
                    menu_item_id=i["menu_item_id"],
                    name=i["name"],
                    quantity=i["quantity"],
                    unit_price_cents=i["unit_price_cents"],
                )
                for i in items
            ),
        )

    def get_order(self, order_id: str) -> Order | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()
            return self._order_from_row(conn, row) if row else None

    def list_orders(self, customer_id: str, limit: int = 5) -> list[Order]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM orders WHERE customer_id = ? ORDER BY placed_at DESC LIMIT ?",
                (customer_id, limit),
            ).fetchall()
            return [self._order_from_row(conn, r) for r in rows]

    # --- reembolsos e tickets ------------------------------------------------

    @staticmethod
    def _refund_from_row(row: sqlite3.Row) -> Refund:
        return Refund(
            id=row["id"],
            order_id=row["order_id"],
            customer_id=row["customer_id"],
            amount_cents=row["amount_cents"],
            issue=IssueType(row["issue"]),
            status=RefundStatus(row["status"]),
            rule_id=row["rule_id"],
            item_ids=tuple(json.loads(row["item_ids"])),
            created_at=datetime.fromisoformat(row["created_at"]),
            decided_by=row["decided_by"],
            decided_at=_dt(row["decided_at"]),
            rejection_reason=row["rejection_reason"],
        )

    def insert_refund(self, r: Refund) -> None:
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO refunds (id, order_id, customer_id, amount_cents, issue, status, rule_id, "
                "item_ids, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    r.id,
                    r.order_id,
                    r.customer_id,
                    r.amount_cents,
                    r.issue.value,
                    r.status.value,
                    r.rule_id,
                    json.dumps(list(r.item_ids)),
                    r.created_at.isoformat(),
                ),
            )

    def get_refund(self, refund_id: str) -> Refund | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM refunds WHERE id = ?", (refund_id,)).fetchone()
        return self._refund_from_row(row) if row else None

    def refunds_for_order(self, order_id: str) -> list[Refund]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM refunds WHERE order_id = ? ORDER BY created_at", (order_id,)
            ).fetchall()
        return [self._refund_from_row(r) for r in rows]

    def refunds_for_customer(self, customer_id: str, since: datetime) -> list[Refund]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM refunds WHERE customer_id = ? AND created_at >= ? ORDER BY created_at",
                (customer_id, since.isoformat()),
            ).fetchall()
        return [self._refund_from_row(r) for r in rows]

    def list_refunds(self, status: RefundStatus | None = None) -> list[Refund]:
        query = "SELECT * FROM refunds"
        params: tuple = ()
        if status:
            query += " WHERE status = ?"
            params = (status.value,)
        with self.connect() as conn:
            rows = conn.execute(query + " ORDER BY created_at", params).fetchall()
        return [self._refund_from_row(r) for r in rows]

    def decide_refund(
        self,
        refund_id: str,
        status: RefundStatus,
        decided_by: str,
        decided_at: datetime,
        rejection_reason: str | None = None,
    ) -> bool:
        """Registra a decisão humana. Só um reembolso pendente muda; devolve se mudou."""
        with self.connect() as conn:
            cur = conn.execute(
                "UPDATE refunds SET status = ?, decided_by = ?, decided_at = ?, rejection_reason = ? "
                "WHERE id = ? AND status = ?",
                (
                    status.value,
                    decided_by,
                    decided_at.isoformat(),
                    rejection_reason,
                    refund_id,
                    RefundStatus.PENDING_APPROVAL.value,
                ),
            )
            return cur.rowcount == 1

    def insert_ticket(self, t: Ticket) -> None:
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO tickets (id, customer_id, order_id, priority, summary, created_at, status) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    t.id,
                    t.customer_id,
                    t.order_id,
                    t.priority,
                    t.summary,
                    t.created_at.isoformat(),
                    t.status.value,
                ),
            )

    @staticmethod
    def _ticket_from_row(row: sqlite3.Row) -> Ticket:
        return Ticket(
            id=row["id"],
            customer_id=row["customer_id"],
            order_id=row["order_id"],
            priority=row["priority"],
            summary=row["summary"],
            created_at=datetime.fromisoformat(row["created_at"]),
            status=TicketStatus(row["status"]),
        )

    def get_ticket(self, ticket_id: str) -> Ticket | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM tickets WHERE id = ?", (ticket_id,)).fetchone()
        return self._ticket_from_row(row) if row else None

    def list_tickets(
        self, customer_id: str | None = None, status: TicketStatus | None = None
    ) -> list[Ticket]:
        conditions, params = [], []
        if customer_id:
            conditions.append("customer_id = ?")
            params.append(customer_id)
        if status:
            conditions.append("status = ?")
            params.append(status.value)
        query = "SELECT * FROM tickets"
        if conditions:
            query += " WHERE " + " AND ".join(conditions)
        with self.connect() as conn:
            rows = conn.execute(query + " ORDER BY created_at", params).fetchall()
        return [self._ticket_from_row(r) for r in rows]

    def resolve_ticket(self, ticket_id: str) -> bool:
        """Marca o ticket como resolvido. Só um ticket aberto muda; devolve se mudou."""
        with self.connect() as conn:
            cur = conn.execute(
                "UPDATE tickets SET status = ? WHERE id = ? AND status = ?",
                (TicketStatus.RESOLVED.value, ticket_id, TicketStatus.OPEN.value),
            )
            return cur.rowcount == 1
