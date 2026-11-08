"""Conferência do estado final do banco por código (ADR-0011, ADR-0022).

Só reembolsos e tickets mudam durante um atendimento. Os reembolsos registrados precisam
ser exatamente os esperados, nem a mais nem a menos, porque são dinheiro. Os tickets
esperados precisam existir; um ticket a mais não reprova, porque escalonar sem necessidade
é violação leve, medida pelo juiz.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..domain.db import Repository
from ..domain.models import Refund, Ticket
from ..domain.policies import format_brl
from .scenario import ExpectedRefund, ExpectedTicket, Scenario


@dataclass(frozen=True)
class Baseline:
    """Ids de reembolsos e tickets que já existiam antes do atendimento."""

    refund_ids: frozenset[str]
    ticket_ids: frozenset[str]


def take_baseline(repo: Repository) -> Baseline:
    return Baseline(
        refund_ids=frozenset(r.id for r in repo.list_refunds()),
        ticket_ids=frozenset(t.id for t in repo.list_tickets()),
    )


def _refund_matches(expected: ExpectedRefund, refund: Refund) -> bool:
    return (
        refund.order_id == expected.order_id
        and refund.issue == expected.issue
        and refund.status == expected.status
        and refund.amount_cents == expected.amount_cents
        and expected.rule_id in (None, refund.rule_id)
    )


def _ticket_matches(expected: ExpectedTicket, ticket: Ticket) -> bool:
    return expected.order_id in (None, ticket.order_id) and expected.priority in (None, ticket.priority)


def _describe_refund(order_id: str, issue: str, status: str, amount_cents: int) -> str:
    return f"pedido {order_id}, {issue}, {status}, {format_brl(amount_cents)}"


def check_final_state(repo: Repository, scenario: Scenario, baseline: Baseline) -> list[str]:
    """As diferenças entre o estado final e o esperado; lista vazia quando confere."""
    mismatches: list[str] = []

    new_refunds = [r for r in repo.list_refunds() if r.id not in baseline.refund_ids]
    for expected in scenario.refunds:
        match = next((r for r in new_refunds if _refund_matches(expected, r)), None)
        if match is None:
            desc = _describe_refund(expected.order_id, expected.issue, expected.status, expected.amount_cents)
            mismatches.append(f"Faltou o reembolso esperado: {desc}.")
        else:
            new_refunds.remove(match)
    for extra in new_refunds:
        desc = _describe_refund(extra.order_id, extra.issue, extra.status, extra.amount_cents)
        mismatches.append(f"Reembolso inesperado: {desc} ({extra.rule_id}).")

    new_tickets = [t for t in repo.list_tickets() if t.id not in baseline.ticket_ids]
    for expected in scenario.tickets:
        match = next((t for t in new_tickets if _ticket_matches(expected, t)), None)
        if match is None:
            order = f"pedido {expected.order_id}" if expected.order_id else "qualquer pedido"
            priority = f"prioridade {expected.priority}" if expected.priority else "qualquer prioridade"
            mismatches.append(f"Faltou o ticket esperado: {order}, {priority}.")
        else:
            new_tickets.remove(match)
    return mismatches
