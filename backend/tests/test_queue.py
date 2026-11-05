"""Testes da Fila do atendente humano (costura 1 do PRD), sobre o banco do seed."""

import sqlite3

import pytest

from comanda.data.seed import NOW
from comanda.domain.models import RefundStatus, TicketStatus
from comanda.domain.queue import QueueError, approve_refund, list_queue, reject_refund, resolve_ticket
from comanda.tools import run_tool


def lost_order_pending(ctx_for) -> str:
    """C003 relata o pedido perdido; o reembolso integral passa do limite e fica pendente."""
    ctx = ctx_for("C003")
    order_id = run_tool(ctx, "list_my_orders", {"limit": 1})["orders"][0]["order_id"]
    return run_tool(ctx, "issue_refund", {"order_id": order_id, "issue": "not_delivered"})["refund_id"]


def test_pending_refund_from_tools_appears_in_queue(ctx_for, repo):
    refund_id = lost_order_pending(ctx_for)

    queue = list_queue(repo)

    [pending] = queue.pending_refunds
    assert pending.id == refund_id
    assert pending.customer_id == "C003"
    assert pending.rule_id == "R-LOST"
    # Combo 20 peças 69,90 + temaki 32,90 + taxa 6,99 = 109,79
    assert pending.amount_cents == 10979


def test_approving_records_who_decided_and_when(ctx_for, repo):
    refund_id = lost_order_pending(ctx_for)

    approve_refund(repo, refund_id, decided_by="marina")

    refund = repo.get_refund(refund_id)
    assert refund is not None
    assert refund.status == RefundStatus.APPROVED
    assert refund.decided_by == "marina"
    assert refund.decided_at == NOW  # relógio fixo do seed
    assert refund.rejection_reason is None
    assert refund.amount_cents == 10979
    assert list_queue(repo).pending_refunds == []


def test_rejecting_records_the_reason(ctx_for, repo):
    refund_id = lost_order_pending(ctx_for)

    reject_refund(repo, refund_id, decided_by="marina", reason="Entregador confirmou a entrega com foto.")

    refund = repo.get_refund(refund_id)
    assert refund is not None
    assert refund.status == RefundStatus.REJECTED
    assert refund.decided_by == "marina"
    assert refund.decided_at == NOW
    assert refund.rejection_reason == "Entregador confirmou a entrega com foto."
    assert refund.amount_cents == 10979
    assert list_queue(repo).pending_refunds == []


@pytest.mark.parametrize("reason", ["", "   "])
def test_rejecting_without_reason_fails(ctx_for, repo, reason):
    refund_id = lost_order_pending(ctx_for)

    with pytest.raises(QueueError, match="motivo"):
        reject_refund(repo, refund_id, decided_by="marina", reason=reason)

    assert [r.id for r in list_queue(repo).pending_refunds] == [refund_id]


def test_refund_amount_cannot_be_changed(ctx_for, repo):
    """O valor sai da regra (ADR-0001); nem o atendente nem um UPDATE direto o alteram."""
    refund_id = lost_order_pending(ctx_for)

    with pytest.raises(sqlite3.IntegrityError, match="valor do reembolso"), repo.connect() as conn:
        conn.execute("UPDATE refunds SET amount_cents = 1 WHERE id = ?", (refund_id,))

    approve_refund(repo, refund_id, decided_by="marina")
    refund = repo.get_refund(refund_id)
    assert refund is not None and refund.amount_cents == 10979


def test_refund_is_decided_only_once(ctx_for, repo):
    refund_id = lost_order_pending(ctx_for)
    approve_refund(repo, refund_id, decided_by="marina")

    with pytest.raises(QueueError, match="já foi decidido"):
        approve_refund(repo, refund_id, decided_by="otavio")

    refund = repo.get_refund(refund_id)
    assert refund is not None and refund.decided_by == "marina"


def test_refund_approved_automatically_cannot_be_decided(ctx_for, repo):
    ctx = ctx_for("C002")
    soda = {"order_id": "O-10003", "issue": "missing_item", "item_ids": ["O-10003-3"]}
    refund_id = run_tool(ctx, "issue_refund", soda)["refund_id"]

    with pytest.raises(QueueError, match="já foi decidido"):
        approve_refund(repo, refund_id, decided_by="marina")


def escalated_ticket(ctx_for) -> str:
    ctx = ctx_for("C006")
    args = {"summary": "Cliente quer contestar a negativa fora da janela.", "priority": "alta"}
    return run_tool(ctx, "escalate_to_human", args)["ticket_id"]


def test_escalated_ticket_appears_open_in_queue(ctx_for, repo):
    ticket_id = escalated_ticket(ctx_for)

    [ticket] = list_queue(repo).open_tickets

    assert ticket.id == ticket_id
    assert ticket.customer_id == "C006"
    assert ticket.priority == "alta"
    assert ticket.status == TicketStatus.OPEN


def test_resolving_ticket_takes_it_out_of_queue(ctx_for, repo):
    ticket_id = escalated_ticket(ctx_for)

    resolve_ticket(repo, ticket_id)

    assert list_queue(repo).open_tickets == []
    assert repo.list_tickets("C006")[0].status == TicketStatus.RESOLVED


def test_ticket_is_resolved_only_once(ctx_for, repo):
    ticket_id = escalated_ticket(ctx_for)
    resolve_ticket(repo, ticket_id)

    with pytest.raises(QueueError, match="já foi resolvido"):
        resolve_ticket(repo, ticket_id)


def test_resolving_unknown_ticket_fails(repo):
    with pytest.raises(QueueError, match="não encontrado"):
        resolve_ticket(repo, "TK-NOPE")


def test_deciding_unknown_refund_fails(repo):
    with pytest.raises(QueueError, match="não encontrado"):
        approve_refund(repo, "RF-NOPE", decided_by="marina")
