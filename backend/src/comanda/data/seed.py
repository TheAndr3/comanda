"""Gera uma base sintética determinística de clientes, restaurantes e pedidos.

Os clientes C001 a C008 são cenários construídos à mão (atraso, item faltando, pedido
perdido etc.), usados na demo e na avaliação. Os demais são gerados com semente fixa.
"""

from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ..domain.db import Repository
from ..domain.models import (
    Customer,
    IssueType,
    MenuItem,
    Order,
    OrderItem,
    OrderStatus,
    Refund,
    RefundStatus,
    Restaurant,
)

BRT = timezone(timedelta(hours=-3))
NOW = datetime(2026, 11, 5, 19, 30, tzinfo=BRT)
CENTER = (-12.2664, -38.9663)  # Feira de Santana, BA

# Tabelas de dados: o layout manual é mais legível que o do formatador.
# fmt: off
RESTAURANTS: list[tuple[str, str, str, str, str, list[tuple[str, int, tuple[str, ...]]]]] = [
    ("R01", "Burger da Praça", "Hambúrguer", "11:00", "23:30", [
        ("X-Burger", 2890, ("glúten", "lactose")),
        ("X-Bacon", 3490, ("glúten", "lactose")),
        ("Batata frita média", 1690, ()),
        ("Refrigerante lata", 690, ()),
        ("Milkshake de chocolate", 1990, ("lactose",)),
    ]),
    ("R02", "Cantina Nonna Bia", "Italiana", "18:00", "23:00", [
        ("Lasanha à bolonhesa", 4290, ("glúten", "lactose")),
        ("Espaguete ao alho e óleo", 3290, ("glúten",)),
        ("Nhoque ao sugo", 3690, ("glúten",)),
        ("Tiramisù", 2190, ("glúten", "lactose", "ovo")),
    ]),
    ("R03", "Sushi Kaito", "Japonesa", "18:00", "23:30", [
        ("Combo 20 peças", 6990, ("peixe", "soja")),
        ("Temaki de salmão", 3290, ("peixe", "soja")),
        ("Hot roll (8 un.)", 2890, ("peixe", "glúten", "soja")),
        ("Missoshiru", 1290, ("soja",)),
    ]),
    ("R04", "Acarajé da Dinha", "Baiana", "15:00", "22:00", [
        ("Acarajé completo", 1800, ("camarão",)),
        ("Abará", 1600, ("camarão",)),
        ("Cocada", 600, ()),
        ("Suco de cajá 500 ml", 900, ()),
    ]),
    ("R05", "Pizzaria Forno Velho", "Pizza", "18:00", "00:00", [
        ("Pizza calabresa grande", 5490, ("glúten", "lactose")),
        ("Pizza marguerita grande", 5290, ("glúten", "lactose")),
        ("Pizza portuguesa grande", 5790, ("glúten", "lactose", "ovo")),
        ("Guaraná 2 L", 1190, ()),
    ]),
    ("R06", "Verde Bowl", "Saudável", "10:00", "21:00", [
        ("Bowl de frango grelhado", 3490, ()),
        ("Bowl vegano de grão-de-bico", 3290, ()),
        ("Suco verde 400 ml", 1290, ()),
    ]),
    ("R07", "Marmitaria Sabor de Casa", "Brasileira", "10:30", "15:30", [
        ("Marmita de frango", 2490, ()),
        ("Marmita de carne de sol", 2990, ("lactose",)),
        ("Feijoada individual", 3290, ()),
    ]),
    ("R08", "Doce Cacau", "Sobremesas", "12:00", "22:00", [
        ("Brownie com sorvete", 2290, ("glúten", "lactose", "ovo")),
        ("Pudim", 1490, ("lactose", "ovo")),
        ("Açaí 500 ml", 2190, ()),
    ]),
]

FIRST_NAMES = [
    "Ana", "Bruno", "Carla", "Diego", "Elisa", "Felipe", "Gabriela", "Heitor", "Isabela",
    "João", "Karina", "Lucas", "Mariana", "Nicolas", "Olívia", "Pedro", "Quésia", "Rafael",
    "Sofia", "Thiago", "Úrsula", "Vinícius", "Wesley", "Yasmin", "Zeca", "Larissa",
]
LAST_NAMES = [
    "Santos", "Oliveira", "Souza", "Lima", "Pereira", "Costa", "Rodrigues", "Almeida",
    "Nascimento", "Carvalho", "Ribeiro", "Barbosa", "Freitas", "Moreira",
]
# fmt: on


class _Builder:
    def __init__(self, repo: Repository, rng: random.Random) -> None:
        self.repo = repo
        self.rng = rng
        self.menus: dict[str, list[MenuItem]] = {}
        self._order_seq = 10000

    def restaurants(self) -> None:
        for rid, name, cuisine, opens, closes, items in RESTAURANTS:
            menu = [
                MenuItem(f"M{rid[1:]}{i:02d}", rid, item_name, price, allergens)
                for i, (item_name, price, allergens) in enumerate(items, start=1)
            ]
            self.menus[rid] = menu
            self.repo.insert_restaurant(Restaurant(rid, name, cuisine, opens, closes), menu)

    def customer(self, cid: str, first: str, last: str) -> None:
        slug = f"{first}.{last}".lower()
        for a, b in (("á", "a"), ("é", "e"), ("í", "i"), ("ó", "o"), ("ú", "u"), ("ã", "a")):
            slug = slug.replace(a, b)
        phone = f"+55 75 9{self.rng.randint(1000, 9999)}-{self.rng.randint(1000, 9999)}"
        created = NOW - timedelta(days=self.rng.randint(60, 900))
        self.repo.insert_customer(Customer(cid, f"{first} {last}", f"{slug}@example.com", phone, created))

    def order(
        self,
        customer_id: str,
        restaurant_id: str,
        picks: list[tuple[int, int]],
        status: OrderStatus,
        placed_ago: timedelta,
        eta_minutes: int = 45,
        delivered_after: int | None = None,
        fee: int = 699,
    ) -> Order:
        """`picks` = (índice no cardápio, quantidade); `delivered_after` em minutos após o pedido."""
        self._order_seq += 1
        oid = f"O-{self._order_seq}"
        placed = NOW - placed_ago
        menu = self.menus[restaurant_id]
        items = tuple(
            OrderItem(f"{oid}-{n}", oid, menu[idx].id, menu[idx].name, qty, menu[idx].price_cents)
            for n, (idx, qty) in enumerate(picks, start=1)
        )
        delivered_at = placed + timedelta(minutes=delivered_after) if delivered_after else None
        lat = lng = None
        if status == OrderStatus.OUT_FOR_DELIVERY:
            lat = CENTER[0] + self.rng.uniform(-0.02, 0.02)
            lng = CENTER[1] + self.rng.uniform(-0.02, 0.02)
        order = Order(
            id=oid,
            customer_id=customer_id,
            restaurant_id=restaurant_id,
            status=status,
            placed_at=placed,
            eta=placed + timedelta(minutes=eta_minutes),
            delivered_at=delivered_at,
            delivery_fee_cents=fee,
            items=items,
            courier_lat=lat,
            courier_lng=lng,
        )
        self.repo.insert_order(order)
        return order

    def past_refund(self, order: Order, days_ago: int, n: int) -> None:
        item = order.items[0]
        self.repo.insert_refund(
            Refund(
                id=f"RF-SEED-{order.id}-{n}",
                order_id=order.id,
                customer_id=order.customer_id,
                amount_cents=item.total_cents,
                issue=IssueType.MISSING_ITEM,
                status=RefundStatus.APPROVED,
                rule_id="R-ITEM",
                item_ids=(item.id,),
                created_at=NOW - timedelta(days=days_ago),
            )
        )


def _scenarios(b: _Builder) -> None:
    """Clientes C001–C008: um cenário de atendimento cada."""
    D, M = timedelta(days=1), timedelta(minutes=1)
    S = OrderStatus

    # C001 Ana: pedido entregue com 75 min de atraso (taxa + 25%).
    b.customer("C001", "Ana", "Santos")
    b.order("C001", "R02", [(0, 1), (3, 1)], S.DELIVERED, 12 * D, delivered_after=40)
    b.order("C001", "R02", [(0, 1), (1, 1)], S.DELIVERED, 140 * M, delivered_after=120)

    # C002 Bruno: entregue há pouco, faltou o refrigerante.
    b.customer("C002", "Bruno", "Oliveira")
    b.order("C002", "R01", [(1, 2), (2, 1), (3, 1)], S.DELIVERED, 80 * M, delivered_after=42)

    # C003 Carla: pedido em rota 100 min além do ETA (perdido, acima do limite automático).
    b.customer("C003", "Carla", "Souza")
    b.order("C003", "R03", [(0, 1), (1, 1)], S.OUT_FOR_DELIVERY, 145 * M, eta_minutes=45)

    # C004 Diego: restaurante cancelou o pedido.
    b.customer("C004", "Diego", "Lima")
    b.order("C004", "R05", [(0, 1), (3, 1)], S.CANCELLED, 50 * M)

    # C005 Elisa: três reembolsos nos últimos 30 dias (sinal de fraude) e um item faltando hoje.
    b.customer("C005", "Elisa", "Pereira")
    for days, n in ((25, 1), (14, 2), (6, 3)):
        old = b.order("C005", "R08", [(1, 1)], S.DELIVERED, days * D, delivered_after=35)
        b.past_refund(old, days, n)
    b.order("C005", "R08", [(0, 1), (2, 1)], S.DELIVERED, 70 * M, delivered_after=38)

    # C006 Felipe: reclama de pedido entregue há 3 dias (fora da janela de 48 h).
    b.customer("C006", "Felipe", "Costa")
    b.order("C006", "R04", [(0, 2), (3, 2)], S.DELIVERED, 3 * D, delivered_after=50)

    # C007 Gabriela: pedido ainda em preparo, dentro do prazo.
    b.customer("C007", "Gabriela", "Rodrigues")
    b.order("C007", "R06", [(0, 1), (2, 1)], S.PREPARING, 15 * M, eta_minutes=40)

    # C008 Heitor: pedido grande com itens faltando (valor acima do limite automático).
    b.customer("C008", "Heitor", "Almeida")
    b.order("C008", "R05", [(0, 1), (1, 1), (2, 1), (3, 2)], S.DELIVERED, 95 * M, delivered_after=50)


def _random_population(b: _Builder, count: int) -> None:
    rng = b.rng
    for n in range(9, 9 + count):
        cid = f"C{n:03d}"
        b.customer(cid, rng.choice(FIRST_NAMES), rng.choice(LAST_NAMES))
        for _ in range(rng.randint(1, 4)):
            rid = rng.choice(list(b.menus))
            menu_size = len(b.menus[rid])
            picks = [(rng.randrange(menu_size), rng.randint(1, 2)) for _ in range(rng.randint(1, 3))]
            picks = list({idx: (idx, qty) for idx, qty in picks}.values())  # sem repetir item
            days_ago = rng.randint(1, 60)
            b.order(
                cid,
                rid,
                picks,
                OrderStatus.DELIVERED,
                timedelta(days=days_ago, minutes=rng.randint(0, 600)),
                delivered_after=rng.randint(25, 70),
                fee=rng.choice([0, 499, 699, 899]),
            )


def seed_database(db_path: Path | str, random_customers: int = 22, seed: int = 42) -> Repository:
    """Recria o banco do zero com dados determinísticos."""
    path = Path(db_path)
    if path.exists():
        path.unlink()
    repo = Repository(path)
    repo.init_schema()
    repo.set_now(NOW)
    builder = _Builder(repo, random.Random(seed))
    builder.restaurants()
    _scenarios(builder)
    _random_population(builder, random_customers)
    return repo
