"""Fila do atendente humano: reembolsos pendentes e tickets abertos.

São operações de domínio usadas pela CLI e pela API, não ferramentas dos agentes (ADR-0007).
"""

from __future__ import annotations

from dataclasses import dataclass

from .db import Repository
from .models import Refund, RefundStatus, Ticket, TicketStatus


class QueueError(Exception):
    """Operação da Fila não permitida; a mensagem é para o atendente humano."""


class QueueItemNotFound(QueueError):
    """O reembolso ou ticket não existe."""


@dataclass(frozen=True)
class Queue:
    pending_refunds: list[Refund]
    open_tickets: list[Ticket]


def list_queue(repo: Repository) -> Queue:
    return Queue(
        pending_refunds=repo.list_refunds(RefundStatus.PENDING_APPROVAL),
        open_tickets=repo.list_tickets(status=TicketStatus.OPEN),
    )


def approve_refund(repo: Repository, refund_id: str, decided_by: str) -> None:
    _decide(repo, refund_id, RefundStatus.APPROVED, decided_by)


def reject_refund(repo: Repository, refund_id: str, decided_by: str, reason: str) -> None:
    reason = reason.strip()
    if not reason:
        raise QueueError("Informe o motivo da recusa.")
    _decide(repo, refund_id, RefundStatus.REJECTED, decided_by, reason)


def _decide(
    repo: Repository,
    refund_id: str,
    status: RefundStatus,
    decided_by: str,
    rejection_reason: str | None = None,
) -> None:
    # O UPDATE só muda reembolso pendente, então duas decisões simultâneas não passam juntas.
    if repo.decide_refund(refund_id, status, decided_by, repo.now(), rejection_reason):
        return
    if repo.get_refund(refund_id) is None:
        raise QueueItemNotFound(f"Reembolso {refund_id} não encontrado.")
    raise QueueError(f"Reembolso {refund_id} já foi decidido.")


def resolve_ticket(repo: Repository, ticket_id: str) -> None:
    if repo.resolve_ticket(ticket_id):
        return
    if repo.get_ticket(ticket_id) is None:
        raise QueueItemNotFound(f"Ticket {ticket_id} não encontrado.")
    raise QueueError(f"Ticket {ticket_id} já foi resolvido.")
