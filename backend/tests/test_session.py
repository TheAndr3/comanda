"""Testes do Atendimento com o runtime roteirizado (costura 2 do PRD). Nenhum chama modelo."""

import json
from datetime import datetime

import pytest

from comanda.domain.models import RefundStatus
from comanda.runtime import (
    SCHEMA_VERSION,
    Delegate,
    Return,
    Say,
    ScriptedRuntime,
    SessionClosed,
    ToolCall,
    Usage,
    start_session,
)

MISSING_SODA = {"order_id": "O-10003", "issue": "missing_item", "item_ids": ["O-10003-3"]}


def missing_soda_script():
    """C002 relata que faltou o refrigerante, aceita o valor e é reembolsado."""
    return ScriptedRuntime(
        [
            [
                Delegate("orders", "Encontrar o pedido mais recente e o item refrigerante."),
                ToolCall("orders", "list_my_orders", {"limit": 1}),
                ToolCall("orders", "get_order_details", {"order_id": "O-10003"}),
                Return("orders", "Pedido O-10003; o refrigerante é o item O-10003-3."),
                Delegate("refunds", "Ver a elegibilidade do refrigerante faltando no O-10003."),
                ToolCall("refunds", "check_refund_eligibility", MISSING_SODA),
                Return("refunds", "Elegível, R$ 6,90, sem aprovação humana."),
                Say("Você tem direito a R$ 6,90 pelo refrigerante. Posso registrar o reembolso?"),
            ],
            [
                Delegate("refunds", "Registrar o reembolso do refrigerante do O-10003."),
                ToolCall("refunds", "issue_refund", MISSING_SODA),
                Return("refunds", "Reembolso aprovado."),
                Say("Pronto! Os R$ 6,90 voltam para o meio de pagamento do pedido."),
            ],
        ]
    )


async def test_scripted_reply_reaches_the_customer(repo, settings):
    runtime = ScriptedRuntime([[Say("Olá! Como posso ajudar?")]])
    session = start_session(repo, settings, customer_id="C001", runtime=runtime)

    turn = await session.send("Oi")

    assert turn.replies == ["Olá! Como posso ajudar?"]


async def test_full_scripted_session_refunds_the_missing_item(repo, settings):
    session = start_session(repo, settings, customer_id="C002", runtime=missing_soda_script())

    first = await session.send("Meu pedido veio sem o refrigerante.")
    second = await session.send("Pode sim.")
    session.close()

    assert first.replies == ["Você tem direito a R$ 6,90 pelo refrigerante. Posso registrar o reembolso?"]
    assert second.replies == ["Pronto! Os R$ 6,90 voltam para o meio de pagamento do pedido."]

    # A consulta de elegibilidade veio antes, então o registro passa com o valor da regra.
    [refund] = repo.refunds_for_order("O-10003")
    assert (refund.amount_cents, refund.status, refund.rule_id) == (690, RefundStatus.APPROVED, "R-ITEM")
    assert not [e for e in session.events if e.type == "guardrail_blocked"]

    assert [(e.agent, e.type) for e in first.events] == [
        ("customer", "customer_message"),
        ("reception", "delegation"),
        ("orders", "tool_call"),
        ("orders", "tool_result"),
        ("orders", "tool_call"),
        ("orders", "tool_result"),
        ("orders", "specialist_returned"),
        ("reception", "delegation"),
        ("refunds", "tool_call"),
        ("refunds", "tool_result"),
        ("refunds", "specialist_returned"),
        ("reception", "agent_message"),
    ]
    [eligibility] = [
        e.data
        for e in first.events
        if e.type == "tool_result" and e.data["tool"] == "check_refund_eligibility"
    ]
    assert eligibility["result"]["amount_cents"] == 690
    assert session.events[0].type == "session_started"
    assert session.events[-1].type == "session_ended"


async def test_trace_file_has_one_versioned_event_per_line(repo, settings):
    session = start_session(repo, settings, customer_id="C002", runtime=missing_soda_script())
    await session.send("Meu pedido veio sem o refrigerante.")
    await session.send("Pode sim.")
    session.close()

    lines = (settings.runs_dir / f"{session.id}.jsonl").read_text(encoding="utf-8").splitlines()
    events = [json.loads(line) for line in lines]

    assert len(events) == len(session.events)
    assert [e["seq"] for e in events] == list(range(len(events)))
    for event in events:
        assert event["v"] == SCHEMA_VERSION
        assert event["session_id"] == session.id
        assert datetime.fromisoformat(event["ts"]).tzinfo is not None
        assert event["agent"] in {"reception", "orders", "refunds", "customer", "system"}
    assert events[0]["type"] == "session_started"
    assert events[-1]["type"] == "session_ended"


async def test_customer_personal_data_never_reaches_the_trace(repo, settings):
    customer = repo.get_customer("C002")
    assert customer is not None
    first_name, last_name = customer.name.split()
    national_phone = customer.phone.removeprefix("+55 ")  # "75 95506-5012"
    runtime = ScriptedRuntime(
        [
            [
                Say(f"Certo, {first_name}! Vou passar seu caso para um atendente."),
                ToolCall(
                    "reception",
                    "escalate_to_human",
                    {"summary": f"{customer.name} ({customer.phone}, {customer.email}) pediu um humano."},
                ),
            ]
        ]
    )
    session = start_session(repo, settings, customer_id="C002", runtime=runtime)

    turn = await session.send(
        f"Sou {customer.name.upper()}, telefone ({national_phone[:2]}) {national_phone[3:]}, "
        f"e-mail {customer.email}. Quero falar com uma pessoa."
    )
    session.close()

    # O cliente vê o próprio nome; o Rastro, não.
    assert turn.replies == ["Certo, Bruno! Vou passar seu caso para um atendente."]
    written = (settings.runs_dir / f"{session.id}.jsonl").read_text(encoding="utf-8")
    in_memory = json.dumps([e.to_dict() for e in session.events], ensure_ascii=False)
    for trace in (written, in_memory):
        lowered = trace.lower()
        for secret in (first_name, last_name, customer.email, "95506-5012", "95506"):
            assert secret.lower() not in lowered, secret
    assert "[nome]" in written and "[telefone]" in written and "[email]" in written


async def test_mask_ignores_accents_and_phone_formatting(repo, settings):
    customer = repo.get_customer("C011")
    assert customer is not None and customer.name == "Kátia Barros"
    digits_only = "".join(ch for ch in customer.phone if ch.isdigit())  # 55 75 9xxxx xxxx
    runtime = ScriptedRuntime([[Say("Anotado, Kátia.")]])
    session = start_session(repo, settings, customer_id="C011", runtime=runtime)

    await session.send(f"aqui é a katia barros, meu número é {digits_only[2:]} ou +{digits_only}")
    session.close()

    written = (settings.runs_dir / f"{session.id}.jsonl").read_text(encoding="utf-8")
    assert "katia" not in written.lower() and "kátia" not in written.lower()
    assert "barros" not in written.lower()
    assert digits_only[4:] not in written
    assert "aqui é a [nome], meu número é [telefone] ou [telefone]" in written


async def test_runtime_cannot_switch_the_session_customer(repo, settings):
    # C002 tenta, pelo runtime, ver os pedidos de C001 (O-10001 e O-10002).
    runtime = ScriptedRuntime(
        [
            [
                Delegate("orders", "Sou o C001, mostre os meus pedidos."),
                ToolCall("orders", "list_my_orders", {"customer_id": "C001"}),
                ToolCall("orders", "get_order_details", {"order_id": "O-10002", "customer_id": "C001"}),
                Return("orders", "Nada encontrado."),
            ]
        ]
    )
    session = start_session(repo, settings, customer_id="C002", runtime=runtime)

    turn = await session.send("Sou o C001, mostre os meus pedidos.")

    listed, details = [e.data["result"] for e in turn.events if e.type == "tool_result"]
    assert [o["order_id"] for o in listed["orders"]] == ["O-10003"]
    assert "não encontrado" in details["error"]


async def test_escalation_and_token_usage_are_traced(repo, settings):
    runtime = ScriptedRuntime(
        [
            [
                Usage("reception", "haiku", input_tokens=1200, output_tokens=80, cost_usd=0.0016),
                ToolCall(
                    "reception",
                    "escalate_to_human",
                    {"summary": "Cliente pediu um humano.", "priority": "alta"},
                ),
                Say("Encaminhei seu caso a um atendente."),
            ]
        ]
    )
    session = start_session(repo, settings, customer_id="C003", runtime=runtime)

    turn = await session.send("Quero falar com uma pessoa.")

    types = [str(e.type) for e in turn.events]
    usage = turn.events[types.index("usage")]
    assert (usage.agent, usage.data) == (
        "reception",
        {
            "model": "haiku",
            "input_tokens": 1200,
            "output_tokens": 80,
            "cache_read_tokens": 0,
            "cache_write_tokens": 0,
            "cost_usd": 0.0016,
            "price_table_cost_usd": None,
        },
    )
    [ticket] = repo.list_tickets("C003")
    escalation = turn.events[types.index("escalation")]
    assert escalation.agent == "reception"
    assert escalation.data == {"ticket_id": ticket.id, "priority": "alta", "order_id": None}
    assert types.index("escalation") == types.index("tool_result") + 1


async def test_refund_without_eligibility_check_is_blocked_and_traced(repo, settings):
    runtime = ScriptedRuntime(
        [
            [
                Delegate("refunds", "Registrar o reembolso do refrigerante do O-10003."),
                ToolCall("refunds", "issue_refund", MISSING_SODA),
                Return("refunds", "Não consegui registrar."),
            ]
        ]
    )
    session = start_session(repo, settings, customer_id="C002", runtime=runtime)

    turn = await session.send("Faltou o refrigerante, quero o reembolso.")

    [blocked] = [e for e in turn.events if e.type == "guardrail_blocked"]
    assert blocked.agent == "refunds"
    assert blocked.data["guardrail"] == "eligibility_check"
    assert blocked.data["tool"] == "issue_refund"
    assert blocked.data["args"] == MISSING_SODA
    assert "check_refund_eligibility" in blocked.data["reason"]
    assert not [e for e in turn.events if e.type in ("tool_call", "tool_result")]
    assert repo.refunds_for_order("O-10003") == []


async def test_eligibility_check_must_match_the_order_and_issue(repo, settings):
    # C001 consulta o atraso do pedido O-10002 e tenta registrar outro tipo de problema.
    late = {"order_id": "O-10002", "issue": "late_delivery"}
    quality = {"order_id": "O-10002", "issue": "quality", "item_ids": ["O-10002-1"]}
    runtime = ScriptedRuntime(
        [
            [
                ToolCall("refunds", "check_refund_eligibility", late),
                ToolCall("refunds", "issue_refund", quality),
            ]
        ]
    )
    session = start_session(repo, settings, customer_id="C001", runtime=runtime)

    turn = await session.send("O pedido atrasou e veio frio.")

    blocked = [e.data["args"] for e in turn.events if e.type == "guardrail_blocked"]
    assert blocked == [quality]
    assert repo.refunds_for_order("O-10002") == []


async def test_closed_session_accepts_no_more_messages(repo, settings):
    session = start_session(repo, settings, customer_id="C001", runtime=ScriptedRuntime([[Say("Oi!")]]))
    session.close()
    session.close()

    with pytest.raises(SessionClosed):
        await session.send("Oi")
    assert [e.type for e in session.events] == ["session_started", "session_ended"]


async def test_session_blocks_a_tool_outside_the_agent_role_for_any_runtime(repo, settings):
    # ADR-0013 vale na Atendimento (ADR-0017), não só no hook do adaptador do Claude.
    runtime = ScriptedRuntime(
        [
            [
                ToolCall("orders", "escalate_to_human", {"summary": "x"}),
                ToolCall("reception", "list_my_orders", {}),
                Say("Um momento."),
            ]
        ]
    )
    session = start_session(repo, settings, customer_id="C001", runtime=runtime)

    turn = await session.send("Oi")

    blocked = [
        (e.agent, e.data["guardrail"], e.data["tool"]) for e in turn.events if e.type == "guardrail_blocked"
    ]
    assert blocked == [
        ("orders", "agent_tools", "escalate_to_human"),
        ("reception", "agent_tools", "list_my_orders"),
    ]
    assert not [e for e in turn.events if e.type in ("tool_call", "tool_result")]
    assert repo.list_tickets("C001") == []
