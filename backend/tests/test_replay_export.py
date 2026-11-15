"""`comanda export-replay`: copia Rastros escolhidos para o replay do painel (ADR-0012). Sem modelo."""

import json
from dataclasses import replace

import pytest

from comanda.cli import main
from comanda.runtime import Delegate, Return, Say, ScriptedRuntime, ToolCall, start_session

MISSING_SODA = {"order_id": "O-10003", "issue": "missing_item", "item_ids": ["O-10003-3"]}


@pytest.fixture
def replays_dir(tmp_path, repo, settings, monkeypatch):
    monkeypatch.setenv("COMANDA_DB", str(repo.db_path))
    monkeypatch.setenv("COMANDA_RUNS", str(settings.runs_dir))
    monkeypatch.setenv("COMANDA_REPLAYS", str(tmp_path / "replays"))
    return tmp_path / "replays"


async def missing_soda_session(repo, settings):
    """C002 relata que faltou o refrigerante e é reembolsado sem aprovação humana."""
    runtime = ScriptedRuntime(
        [
            [
                Delegate("orders", "Encontrar o pedido e o item refrigerante."),
                ToolCall("orders", "get_order_details", {"order_id": "O-10003"}),
                Return("orders", "O refrigerante é o item O-10003-3."),
                Delegate("refunds", "Reembolsar o refrigerante do O-10003."),
                ToolCall("refunds", "check_refund_eligibility", MISSING_SODA),
                ToolCall("refunds", "issue_refund", MISSING_SODA),
                Return("refunds", "Reembolso aprovado."),
                Say("Pronto! Os R$ 6,90 voltam para o meio de pagamento do pedido."),
            ]
        ]
    )
    session = start_session(repo, settings, customer_id="C002", runtime=runtime)
    await session.send("Meu pedido veio sem o refrigerante.")
    session.close()
    return session


def read_index(replays_dir):
    return json.loads((replays_dir / "index.json").read_text(encoding="utf-8"))


async def test_export_copies_the_trace_and_lists_it_in_the_index(repo, settings, replays_dir):
    session = await missing_soda_session(repo, settings)

    assert main(["export-replay", session.id]) == 0

    assert (replays_dir / f"{session.id}.jsonl").read_bytes() == session.trace_path.read_bytes()
    assert read_index(replays_dir) == {
        "v": 1,
        "replays": [
            {
                "session_id": session.id,
                "title": "Meu pedido veio sem o refrigerante.",
                "customer_id": "C002",
                "shows": [
                    "Recepção delega a Pedidos e Reembolsos",
                    "Reembolso automático de R$ 6,90 (regra R-ITEM)",
                ],
            }
        ],
    }


async def test_finds_traces_of_an_evaluation_run_and_keeps_earlier_replays(repo, settings, replays_dir):
    first = await missing_soda_session(repo, settings)
    eval_settings = replace(settings, runs_dir=settings.runs_dir / "eval" / "20261115T120000Z")
    second = await missing_soda_session(repo, eval_settings)

    assert main(["export-replay", first.id]) == 0
    assert main(["export-replay", second.id, first.id]) == 0

    assert (replays_dir / f"{second.id}.jsonl").read_bytes() == second.trace_path.read_bytes()
    assert [r["session_id"] for r in read_index(replays_dir)["replays"]] == [first.id, second.id]


async def test_refuses_a_trace_with_personal_data_of_any_customer(repo, settings, replays_dir, capsys):
    # A gravação só mascara o cliente do atendimento (C026); o dono do pedido O-10005 é o C004.
    other = repo.get_customer("C004")
    leak = f"O pedido O-10005 é do {other.name}, telefone {other.phone}."
    session = start_session(repo, settings, "C026", ScriptedRuntime([[Say(leak)]]))
    await session.send("De quem é o pedido O-10005?")
    clean = await missing_soda_session(repo, settings)

    assert main(["export-replay", clean.id, session.id]) == 1

    assert f"{session.id} (evento 2)" in capsys.readouterr().err
    assert not replays_dir.exists() or not any(replays_dir.iterdir())


async def test_index_says_what_the_replay_shows(repo, settings, replays_dir):
    lost = {"order_id": "O-10004", "issue": "not_delivered"}
    runtime = ScriptedRuntime(
        [
            [
                Delegate("refunds", "Reembolsar o pedido O-10004, que não chegou."),
                ToolCall("refunds", "issue_refund", lost),
                ToolCall("refunds", "check_refund_eligibility", lost),
                ToolCall("refunds", "issue_refund", lost),
                ToolCall("refunds", "escalate_to_human", {"summary": "Pedido perdido.", "priority": "alta"}),
                Return("refunds", "Reembolso pendente e ticket aberto."),
                Say("Um atendente vai confirmar o reembolso de R$ 109,79."),
            ]
        ]
    )
    session = start_session(repo, settings, "C003", runtime)
    await session.send("Meu sushi nunca chegou.")

    assert main(["export-replay", session.id]) == 0

    assert read_index(replays_dir)["replays"][0]["shows"] == [
        "Recepção delega a Reembolsos",
        "Guardrail eligibility_check bloqueia issue_refund",
        "Reembolso de R$ 109,79 pendente de aprovação humana (regra R-LOST)",
        "Escalonamento a um atendente humano, prioridade alta",
    ]


@pytest.mark.parametrize("session_id", ["S-000000000000", "../index"])
def test_an_unknown_session_is_an_error(session_id, replays_dir, capsys):
    assert main(["export-replay", session_id]) == 1

    assert session_id in capsys.readouterr().err


async def test_title_given_by_hand_replaces_the_customer_message(repo, settings, replays_dir, capsys):
    one, two = await missing_soda_session(repo, settings), await missing_soda_session(repo, settings)

    assert main(["export-replay", one.id, "--title", "Refrigerante faltando"]) == 0
    assert read_index(replays_dir)["replays"][0]["title"] == "Refrigerante faltando"

    assert main(["export-replay", one.id, two.id, "--title", "Dois de uma vez"]) == 1
    assert "--title" in capsys.readouterr().err
