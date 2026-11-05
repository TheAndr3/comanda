"""Política de reembolso aplicada em código.

O agente decide *o que* pedir; esta função decide *o que é permitido*. Nenhum texto de
conversa altera o resultado: o valor e a necessidade de aprovação vêm só dos dados.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

from .models import IssueType, Order, OrderStatus, Refund, RefundStatus

CLAIM_WINDOW = timedelta(hours=48)
LATE_FEE_MINUTES = 30
LATE_PARTIAL_MINUTES = 60
LATE_PARTIAL_RATE = 0.25
LOST_ORDER_MINUTES = 90
QUALITY_RATE = 0.5
FREQUENT_REFUND_COUNT = 3
FREQUENT_REFUND_WINDOW = timedelta(days=30)


@dataclass(frozen=True)
class RefundDecision:
    eligible: bool
    amount_cents: int
    requires_approval: bool
    rule_id: str
    reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "eligible": self.eligible,
            "amount_cents": self.amount_cents,
            "amount": format_brl(self.amount_cents),
            "requires_human_approval": self.requires_approval,
            "rule_id": self.rule_id,
            "reasons": self.reasons,
        }


def format_brl(cents: int) -> str:
    reais, centavos = divmod(cents, 100)
    return f"R$ {reais:,}".replace(",", ".") + f",{centavos:02d}"


def _deny(rule_id: str, reason: str) -> RefundDecision:
    return RefundDecision(False, 0, False, rule_id, [reason])


def _active(refunds: list[Refund]) -> list[Refund]:
    return [r for r in refunds if r.status != RefundStatus.REJECTED]


def evaluate_refund(
    order: Order,
    issue: IssueType,
    item_ids: list[str],
    now: datetime,
    order_refunds: list[Refund],
    customer_recent_refunds: list[Refund],
    auto_limit_cents: int,
) -> RefundDecision:
    """Calcula se o pedido tem direito a reembolso, quanto, e se precisa de aprovação humana."""
    reasons: list[str] = []
    item_by_id = {item.id: item for item in order.items}
    already_refunded_items = {iid for r in _active(order_refunds) for iid in r.item_ids}

    # 1) Elegibilidade e valor-base por tipo de problema.
    if issue in (IssueType.MISSING_ITEM, IssueType.WRONG_ITEM, IssueType.QUALITY):
        if order.status != OrderStatus.DELIVERED or order.delivered_at is None:
            return _deny("R-NOT-DELIVERED-YET", "Problemas com itens só valem para pedidos entregues.")
        if now - order.delivered_at > CLAIM_WINDOW:
            return _deny("R-WINDOW", "Reclamações devem ser feitas em até 48 horas após a entrega.")
        if not item_ids:
            return _deny("R-ITEMS-REQUIRED", "Informe quais itens do pedido tiveram problema.")
        unknown = [iid for iid in item_ids if iid not in item_by_id]
        if unknown:
            return _deny("R-ITEMS-UNKNOWN", f"Itens que não pertencem a este pedido: {unknown}.")
        repeated = [iid for iid in item_ids if iid in already_refunded_items]
        if repeated:
            return _deny("R-ITEMS-REPEATED", f"Itens que já tiveram reembolso: {repeated}.")
        items_total = sum(item_by_id[iid].total_cents for iid in set(item_ids))
        if issue == IssueType.QUALITY:
            amount, rule_id = round(items_total * QUALITY_RATE), "R-QUALITY"
            reasons.append("Problema de qualidade: reembolso de 50% do valor dos itens.")
        else:
            amount, rule_id = items_total, "R-ITEM"
            reasons.append("Item faltando ou errado: reembolso integral do valor dos itens.")

    elif issue == IssueType.LATE_DELIVERY:
        if order.status != OrderStatus.DELIVERED or order.delivered_at is None:
            return _deny(
                "R-LATE-NOT-DELIVERED",
                "O pedido ainda não foi entregue; acompanhe o rastreio ou trate como não entregue.",
            )
        if now - order.delivered_at > CLAIM_WINDOW:
            return _deny("R-WINDOW", "Reclamações devem ser feitas em até 48 horas após a entrega.")
        late = order.minutes_late(now)
        if late >= LATE_PARTIAL_MINUTES:
            amount = order.delivery_fee_cents + round(order.subtotal_cents * LATE_PARTIAL_RATE)
            rule_id = "R-LATE-60"
            reasons.append(f"Atraso de {late} min: taxa de entrega + 25% do subtotal.")
        elif late >= LATE_FEE_MINUTES:
            amount, rule_id = order.delivery_fee_cents, "R-LATE-30"
            reasons.append(f"Atraso de {late} min: reembolso da taxa de entrega.")
        else:
            return _deny("R-LATE-UNDER-30", f"Atraso de {late} min; o mínimo para reembolso é 30 min.")

    elif issue == IssueType.NOT_DELIVERED:
        if order.status == OrderStatus.CANCELLED:
            amount, rule_id = order.total_cents, "R-CANCELLED"
            reasons.append("Pedido cancelado: reembolso integral.")
        elif (
            order.status == OrderStatus.OUT_FOR_DELIVERY
            and order.minutes_late(now) >= LOST_ORDER_MINUTES
        ):
            amount, rule_id = order.total_cents, "R-LOST"
            reasons.append("Pedido em rota há mais de 90 min além do previsto: reembolso integral.")
        elif order.status == OrderStatus.DELIVERED:
            return _deny(
                "R-MARKED-DELIVERED",
                "O pedido consta como entregue; o caso precisa de análise humana.",
            )
        else:
            return _deny(
                "R-STILL-IN-PROGRESS",
                "O pedido ainda está dentro do prazo; acompanhe o rastreio.",
            )
    else:  # pragma: no cover - enum exaustivo
        return _deny("R-UNKNOWN-ISSUE", f"Tipo de problema desconhecido: {issue}.")

    # 2) Teto: nunca reembolsar mais que o total do pedido.
    already = sum(r.amount_cents for r in _active(order_refunds))
    remaining = order.total_cents - already
    if remaining <= 0:
        return _deny("R-FULLY-REFUNDED", "Este pedido já foi totalmente reembolsado.")
    if amount > remaining:
        amount = remaining
        reasons.append("Valor limitado ao saldo ainda não reembolsado do pedido.")

    # 3) Aprovação humana: valor alto ou cliente com muitos reembolsos recentes.
    requires_approval = False
    if amount > auto_limit_cents:
        requires_approval = True
        reasons.append(f"Valor acima de {format_brl(auto_limit_cents)}: exige aprovação humana.")
    recent = [
        r for r in _active(customer_recent_refunds) if now - r.created_at <= FREQUENT_REFUND_WINDOW
    ]
    if len(recent) >= FREQUENT_REFUND_COUNT:
        requires_approval = True
        reasons.append(
            f"Cliente com {len(recent)} reembolsos nos últimos 30 dias: exige aprovação humana."
        )

    return RefundDecision(True, amount, requires_approval, rule_id, reasons)
