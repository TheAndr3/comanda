"""Ferramentas dos agentes, independentes de framework.

Cada ferramenta é uma função pura sobre `ToolContext`. Os adaptadores (Claude Agent SDK,
servidor MCP standalone, testes) reutilizam exatamente estas funções, então a regra de
negócio vive num único lugar.

Princípios:
- O cliente da sessão é fixo no contexto: nenhuma ferramenta aceita `customer_id`, e todo
  pedido consultado passa por checagem de dono. Um prompt malicioso não acessa dados alheios.
- `issue_refund` não recebe valor. O valor sai de `evaluate_refund`, nunca do modelo.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from ..config import Settings
from ..domain.db import Repository
from ..domain.models import IssueType, Order, OrderStatus, Refund, RefundStatus, Ticket
from ..domain.policies import FREQUENT_REFUND_WINDOW, evaluate_refund, format_brl
from ..knowledge import search_policies


class ToolError(Exception):
    """Erro esperado, devolvido ao agente como resultado de ferramenta com `is_error`."""


@dataclass
class ToolContext:
    repo: Repository
    settings: Settings
    customer_id: str
    # Estado da sessão usado pelos guardrails, por exemplo checagens de elegibilidade feitas.
    state: dict[str, Any] = field(default_factory=dict)

    @property
    def checked_eligibility(self) -> set[tuple[str, str]]:
        return self.state.setdefault("checked_eligibility", set())


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    input_schema: dict[str, Any]
    handler: Callable[[ToolContext, dict[str, Any]], dict[str, Any]]
    mutates: bool = False


# --- auxiliares ----------------------------------------------------------------

STATUS_PT = {
    OrderStatus.PREPARING: "em preparo",
    OrderStatus.OUT_FOR_DELIVERY: "saiu para entrega",
    OrderStatus.DELIVERED: "entregue",
    OrderStatus.CANCELLED: "cancelado",
}


def _own_order(ctx: ToolContext, order_id: str) -> Order:
    order = ctx.repo.get_order(order_id.strip())
    # Mesma mensagem para "não existe" e "é de outro cliente": não vaza a existência do pedido.
    if order is None or order.customer_id != ctx.customer_id:
        raise ToolError(f"Pedido {order_id} não encontrado entre os pedidos deste cliente.")
    return order


def _issue(value: str) -> IssueType:
    try:
        return IssueType(value)
    except ValueError as exc:
        options = ", ".join(i.value for i in IssueType)
        raise ToolError(f"Tipo de problema inválido: {value}. Use um de: {options}.") from exc


def _order_summary(order: Order) -> dict[str, Any]:
    return {
        "order_id": order.id,
        "restaurant_id": order.restaurant_id,
        "status": order.status.value,
        "status_pt": STATUS_PT[order.status],
        "placed_at": order.placed_at.isoformat(timespec="minutes"),
        "total": format_brl(order.total_cents),
    }


def _decide(ctx: ToolContext, order: Order, issue: IssueType, item_ids: list[str]):
    now = ctx.repo.now()
    return evaluate_refund(
        order=order,
        issue=issue,
        item_ids=item_ids,
        now=now,
        order_refunds=ctx.repo.refunds_for_order(order.id),
        customer_recent_refunds=ctx.repo.refunds_for_customer(
            ctx.customer_id, now - FREQUENT_REFUND_WINDOW
        ),
        auto_limit_cents=ctx.settings.auto_refund_limit_cents,
    )


# --- ferramentas ---------------------------------------------------------------

def list_my_orders(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    limit = max(1, min(int(args.get("limit", 5)), 10))
    orders = ctx.repo.list_orders(ctx.customer_id, limit)
    return {"orders": [_order_summary(o) for o in orders]}


def get_order_details(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    order = _own_order(ctx, args["order_id"])
    refunds = ctx.repo.refunds_for_order(order.id)
    return {
        **_order_summary(order),
        "eta": order.eta.isoformat(timespec="minutes"),
        "delivered_at": order.delivered_at.isoformat(timespec="minutes") if order.delivered_at else None,
        "items": [
            {
                "item_id": i.id,
                "name": i.name,
                "quantity": i.quantity,
                "line_total": format_brl(i.total_cents),
            }
            for i in order.items
        ],
        "subtotal": format_brl(order.subtotal_cents),
        "delivery_fee": format_brl(order.delivery_fee_cents),
        "refunds": [
            {"refund_id": r.id, "amount": format_brl(r.amount_cents), "status": r.status.value}
            for r in refunds
        ],
    }


def track_delivery(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    order = _own_order(ctx, args["order_id"])
    now = ctx.repo.now()
    late = order.minutes_late(now)
    result: dict[str, Any] = {
        "order_id": order.id,
        "status": order.status.value,
        "status_pt": STATUS_PT[order.status],
        "eta": order.eta.isoformat(timespec="minutes"),
        "minutes_late": late,
    }
    if order.status == OrderStatus.OUT_FOR_DELIVERY:
        result["courier_position"] = {"lat": order.courier_lat, "lng": order.courier_lng}
    if order.status == OrderStatus.DELIVERED and order.delivered_at:
        result["delivered_at"] = order.delivered_at.isoformat(timespec="minutes")
    return result


def get_restaurant_info(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    found = ctx.repo.get_restaurant(args["restaurant_id"].strip())
    if found is None:
        raise ToolError(f"Restaurante {args['restaurant_id']} não encontrado.")
    restaurant, menu = found
    return {
        "restaurant_id": restaurant.id,
        "name": restaurant.name,
        "cuisine": restaurant.cuisine,
        "hours": f"{restaurant.opens_at}–{restaurant.closes_at}",
        "menu": [
            {"name": m.name, "price": format_brl(m.price_cents), "allergens": list(m.allergens)}
            for m in menu
        ],
        "allergen_note": "Lista informada pelo restaurante; em alergia grave, confirme com ele.",
    }


def search_policies_tool(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    return {"results": search_policies(args["query"], k=2)}


def check_refund_eligibility(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    order = _own_order(ctx, args["order_id"])
    issue = _issue(args["issue"])
    item_ids = list(args.get("item_ids") or [])
    decision = _decide(ctx, order, issue, item_ids)
    ctx.checked_eligibility.add((order.id, issue.value))
    return {"order_id": order.id, "issue": issue.value, **decision.to_dict()}


def issue_refund(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    order = _own_order(ctx, args["order_id"])
    issue = _issue(args["issue"])
    item_ids = list(args.get("item_ids") or [])
    decision = _decide(ctx, order, issue, item_ids)  # recalculado: nunca confia em checagem antiga
    if not decision.eligible:
        raise ToolError("Reembolso não permitido: " + " ".join(decision.reasons))
    status = RefundStatus.PENDING_APPROVAL if decision.requires_approval else RefundStatus.APPROVED
    refund = Refund(
        id=f"RF-{uuid.uuid4().hex[:8].upper()}",
        order_id=order.id,
        customer_id=ctx.customer_id,
        amount_cents=decision.amount_cents,
        issue=issue,
        status=status,
        rule_id=decision.rule_id,
        item_ids=tuple(sorted(set(item_ids))),
        created_at=ctx.repo.now(),
    )
    ctx.repo.insert_refund(refund)
    message = (
        "Reembolso enviado para aprovação humana; o cliente recebe a resposta em até 24 horas."
        if status == RefundStatus.PENDING_APPROVAL
        else "Reembolso aprovado; o valor volta ao meio de pagamento do pedido."
    )
    return {
        "refund_id": refund.id,
        "status": status.value,
        "amount": format_brl(refund.amount_cents),
        "rule_id": decision.rule_id,
        "message": message,
    }


def escalate_to_human(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    order_id = args.get("order_id")
    if order_id:
        _own_order(ctx, order_id)
    priority = args.get("priority", "normal")
    if priority not in {"normal", "alta"}:
        raise ToolError("Prioridade deve ser 'normal' ou 'alta'.")
    ticket = Ticket(
        id=f"TK-{uuid.uuid4().hex[:8].upper()}",
        customer_id=ctx.customer_id,
        order_id=order_id,
        priority=priority,
        summary=args["summary"].strip()[:1000],
        created_at=ctx.repo.now(),
    )
    ctx.repo.insert_ticket(ticket)
    return {
        "ticket_id": ticket.id,
        "priority": priority,
        "message": "Caso encaminhado a um atendente humano.",
    }


# --- registro ------------------------------------------------------------------

_ORDER_ID = {"type": "string", "description": "ID do pedido, por exemplo O-10003."}
_ISSUE = {
    "type": "string",
    "enum": [i.value for i in IssueType],
    "description": (
        "missing_item (item faltando), wrong_item (item errado), late_delivery (atraso), "
        "not_delivered (não chegou ou foi cancelado), quality (qualidade ruim)."
    ),
}
_ITEM_IDS = {
    "type": "array",
    "items": {"type": "string"},
    "description": "IDs dos itens do pedido afetados (obrigatório para problemas com itens).",
}

TOOLS: dict[str, ToolSpec] = {
    spec.name: spec
    for spec in [
        ToolSpec(
            "list_my_orders",
            "Lista os pedidos mais recentes do cliente da sessão.",
            {
                "type": "object",
                "properties": {"limit": {"type": "integer", "minimum": 1, "maximum": 10}},
            },
            list_my_orders,
        ),
        ToolSpec(
            "get_order_details",
            "Mostra itens, valores, horários e reembolsos de um pedido do cliente.",
            {"type": "object", "properties": {"order_id": _ORDER_ID}, "required": ["order_id"]},
            get_order_details,
        ),
        ToolSpec(
            "track_delivery",
            "Status em tempo real do pedido: etapa, ETA, minutos de atraso e posição do entregador.",
            {"type": "object", "properties": {"order_id": _ORDER_ID}, "required": ["order_id"]},
            track_delivery,
        ),
        ToolSpec(
            "get_restaurant_info",
            "Horário de funcionamento, cardápio e alérgenos de um restaurante.",
            {
                "type": "object",
                "properties": {"restaurant_id": {"type": "string", "description": "Ex.: R01."}},
                "required": ["restaurant_id"],
            },
            get_restaurant_info,
        ),
        ToolSpec(
            "search_policies",
            "Busca nas políticas oficiais de atendimento (prazos, reembolso, privacidade).",
            {
                "type": "object",
                "properties": {"query": {"type": "string", "description": "Pergunta ou palavras-chave."}},
                "required": ["query"],
            },
            search_policies_tool,
        ),
        ToolSpec(
            "check_refund_eligibility",
            (
                "Calcula, sem executar nada, se o pedido tem direito a reembolso, o valor exato e "
                "se precisa de aprovação humana. Use sempre antes de issue_refund."
            ),
            {
                "type": "object",
                "properties": {"order_id": _ORDER_ID, "issue": _ISSUE, "item_ids": _ITEM_IDS},
                "required": ["order_id", "issue"],
            },
            check_refund_eligibility,
        ),
        ToolSpec(
            "issue_refund",
            (
                "Executa o reembolso calculado pela política. O valor não é escolhido pelo agente. "
                "Só use depois que o cliente concordar com o valor informado."
            ),
            {
                "type": "object",
                "properties": {"order_id": _ORDER_ID, "issue": _ISSUE, "item_ids": _ITEM_IDS},
                "required": ["order_id", "issue"],
            },
            issue_refund,
            mutates=True,
        ),
        ToolSpec(
            "escalate_to_human",
            "Abre um ticket para atendimento humano com um resumo do caso.",
            {
                "type": "object",
                "properties": {
                    "summary": {"type": "string", "description": "Resumo objetivo do caso."},
                    "priority": {"type": "string", "enum": ["normal", "alta"]},
                    "order_id": _ORDER_ID,
                },
                "required": ["summary"],
            },
            escalate_to_human,
            mutates=True,
        ),
    ]
}


def run_tool(ctx: ToolContext, name: str, args: dict[str, Any]) -> dict[str, Any]:
    """Executa uma ferramenta pelo nome. Erros esperados viram `{"error": ...}`."""
    spec = TOOLS.get(name)
    if spec is None:
        return {"error": f"Ferramenta desconhecida: {name}"}
    try:
        return spec.handler(ctx, args)
    except ToolError as exc:
        return {"error": str(exc)}
    except KeyError as exc:
        return {"error": f"Parâmetro obrigatório ausente: {exc.args[0]}"}
