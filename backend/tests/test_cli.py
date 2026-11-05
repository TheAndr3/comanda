"""Testes de fumaça da CLI da Fila: adaptador fino sobre as operações de domínio."""

import pytest

from comanda.cli import main
from comanda.tools import run_tool


@pytest.fixture(autouse=True)
def seeded_db(repo, monkeypatch):
    monkeypatch.setenv("COMANDA_DB", str(repo.db_path))


def pending_lost_order(ctx_for) -> str:
    return run_tool(ctx_for("C003"), "issue_refund", {"order_id": "O-10004", "issue": "not_delivered"})[
        "refund_id"
    ]


def test_queue_list_shows_pending_refunds_and_open_tickets(ctx_for, capsys):
    refund_id = pending_lost_order(ctx_for)
    ticket_id = run_tool(ctx_for("C006"), "escalate_to_human", {"summary": "Quer falar com um humano."})[
        "ticket_id"
    ]

    assert main(["queue", "list"]) == 0

    out = capsys.readouterr().out
    refund_line = next(line for line in out.splitlines() if refund_id in line)
    for expected in ("C003", "Carla Souza", "O-10004", "R-LOST", "R$ 109,79"):
        assert expected in refund_line
    ticket_line = next(line for line in out.splitlines() if ticket_id in line)
    for expected in ("C006", "Felipe Costa", "normal", "Quer falar com um humano."):
        assert expected in ticket_line


def test_approve_through_cli_and_deciding_again_fails(ctx_for, repo, capsys):
    approved = pending_lost_order(ctx_for)
    assert main(["queue", "approve", approved, "--by", "marina"]) == 0
    assert repo.get_refund(approved).decided_by == "marina"  # type: ignore[union-attr]

    assert main(["queue", "approve", approved, "--by", "marina"]) == 1
    assert "já foi decidido" in capsys.readouterr().err


def test_reject_requires_reason(ctx_for, repo):
    refund_id = pending_lost_order(ctx_for)

    with pytest.raises(SystemExit):
        main(["queue", "reject", refund_id, "--by", "marina"])

    assert main(["queue", "reject", refund_id, "--by", "marina", "--reason", "Entrega confirmada."]) == 0
    assert repo.get_refund(refund_id).rejection_reason == "Entrega confirmada."  # type: ignore[union-attr]


def test_resolve_ticket_through_cli(ctx_for, capsys):
    ticket_id = run_tool(ctx_for("C006"), "escalate_to_human", {"summary": "Quer falar com um humano."})[
        "ticket_id"
    ]

    assert main(["queue", "resolve", ticket_id]) == 0
    capsys.readouterr()

    assert main(["queue", "list"]) == 0
    assert ticket_id not in capsys.readouterr().out
