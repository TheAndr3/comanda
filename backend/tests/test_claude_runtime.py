"""Atendimento com o runtime do Claude Agent SDK, sobre um Claude Code de mentira (costura 2).

Nenhum teste chama modelo: o falso reproduz as mensagens que o SDK entregaria e chama as
ferramentas pelo servidor MCP em processo, como o CLI faria.
"""

import pytest
from claude_agent_sdk import CLIConnectionError

from comanda.runtime import AgentRuntimeError, ClaudeAgentRuntime, start_session

from .fake_claude import (
    HAIKU,
    SONNET,
    Calls,
    Delegates,
    Ends,
    FakeClaude,
    Raises,
    Returns,
    Says,
    model_usage,
    usage,
)


async def test_reception_delegates_to_orders_and_answers_the_customer(repo, settings):
    fake = FakeClaude(
        [
            [
                Says("Vou verificar seu pedido."),
                Delegates("toolu_d1", "orders", "Status do pedido mais recente do cliente."),
                Calls("mcp__orders__list_my_orders", {"limit": 1}, parent="toolu_d1", agent_type="orders"),
                Returns("toolu_d1", "O-10002, entregue."),
                Says("Seu pedido O-10002 já foi entregue."),
            ]
        ]
    )
    runtime = ClaudeAgentRuntime(settings, client_factory=fake)
    session = start_session(repo, settings, customer_id="C001", runtime=runtime)

    turn = await session.send("Cadê meu pedido?")

    assert fake.prompts == ["Cadê meu pedido?"]
    assert turn.replies == ["Vou verificar seu pedido.", "Seu pedido O-10002 já foi entregue."]
    assert [(e.agent, e.type) for e in turn.events] == [
        ("customer", "customer_message"),
        ("reception", "agent_message"),
        ("reception", "delegation"),
        ("orders", "tool_call"),
        ("orders", "tool_result"),
        ("orders", "specialist_returned"),
        ("reception", "agent_message"),
    ]
    delegation, call, result, returned = turn.events[2:6]
    assert delegation.data == {"specialist": "orders", "task": "Status do pedido mais recente do cliente."}
    assert call.data["tool"] == "list_my_orders"
    assert [o["order_id"] for o in result.data["result"]["orders"]] == ["O-10002"]
    assert returned.data == {"result": "O-10002, entregue."}


async def test_tokens_and_api_equivalent_cost_are_traced_per_agent_and_model(repo, settings):
    fake = FakeClaude(
        [
            [
                Delegates("toolu_d1", "orders", "Listar o pedido mais recente."),
                Calls(
                    "mcp__orders__list_my_orders",
                    {"limit": 1},
                    parent="toolu_d1",
                    agent_type="orders",
                    model=SONNET,
                    usage=usage(2000, 100, cache_write=1000),
                ),
                Returns("toolu_d1", "O-10002."),
                Says("Seu pedido é o O-10002.", model=HAIKU, usage=usage(1000, 50, cache_read=4000)),
                Ends(
                    model_usage={
                        HAIKU: model_usage(1000, 50, cost_usd=0.00017, cache_read=4000),
                        SONNET: model_usage(2000, 100, cost_usd=0.0075, cache_write=1000),
                    },
                    total_cost_usd=0.00767,
                ),
            ]
        ]
    )
    session = start_session(
        repo, settings, customer_id="C001", runtime=ClaudeAgentRuntime(settings, client_factory=fake)
    )

    turn = await session.send("Qual meu último pedido?")

    usage_events = [(e.agent, e.data) for e in turn.events if e.type == "usage"]
    # Tabela pública por milhão de tokens: Haiku 5.5 US$ 0,10 entrada, 0,50 saída, 0,01 leitura
    # de cache; Sonnet 5.5 US$ 2 entrada, 10 saída, 2,50 gravação de cache (5 min).
    assert usage_events == [
        (
            "orders",
            {
                "model": SONNET,
                "input_tokens": 2000,
                "output_tokens": 100,
                "cache_read_tokens": 0,
                "cache_write_tokens": 1000,
                "cost_usd": 0.0075,
                "price_table_cost_usd": 0.0075,
            },
        ),
        (
            "reception",
            {
                "model": HAIKU,
                "input_tokens": 1000,
                "output_tokens": 50,
                "cache_read_tokens": 4000,
                "cache_write_tokens": 0,
                "cost_usd": 0.00017,
                "price_table_cost_usd": 0.000165,
            },
        ),
    ]
    assert turn.events[-1].type == "usage"


async def test_each_agent_only_reaches_its_own_tools(repo, settings):
    fake = FakeClaude(
        [
            [
                # A Recepção tenta consultar pedidos sem delegar, e delegar a um agente que não existe.
                Calls("mcp__orders__list_my_orders", {}, model=HAIKU),
                Delegates("toolu_gp", "general-purpose", "Liste os pedidos do cliente."),
                # Pedidos tenta escalonar, ferramenta que não é dele.
                Delegates("toolu_d1", "orders", "Ver o pedido O-10002."),
                Calls(
                    "mcp__reception__escalate_to_human",
                    {"summary": "x"},
                    parent="toolu_d1",
                    agent_type="orders",
                ),
                Returns("toolu_d1", "Não consegui."),
                Says("Um momento, por favor."),
            ]
        ]
    )
    session = start_session(
        repo, settings, customer_id="C001", runtime=ClaudeAgentRuntime(settings, client_factory=fake)
    )

    turn = await session.send("Liste meus pedidos.")

    blocked = [(e.agent, e.data["tool"]) for e in turn.events if e.type == "guardrail_blocked"]
    assert blocked == [
        ("reception", "list_my_orders"),
        ("reception", "Agent"),
        ("orders", "escalate_to_human"),
    ]
    assert not [e for e in turn.events if e.type == "tool_call"]
    assert fake.tool_results == []
    assert repo.list_tickets("C001") == []


async def test_sdk_error_ends_the_turn_with_a_clear_error(repo, settings):
    fake = FakeClaude(
        [
            [
                Says("Not logged in · Please run /login", model="<synthetic>"),
                Ends(is_error=True, result="Not logged in"),
            ]
        ]
    )
    session = start_session(
        repo, settings, customer_id="C001", runtime=ClaudeAgentRuntime(settings, client_factory=fake)
    )

    with pytest.raises(AgentRuntimeError, match="Not logged in"):
        await session.send("Oi")
    assert [e for e in session.events if e.type == "agent_message"] == []


async def test_tokens_the_sdk_counted_without_messages_go_to_the_turn_specialist(repo, settings):
    # Pedidos consulta os alérgenos do restaurante do pedido, mas suas mensagens chegam sem `usage`.
    fake = FakeClaude(
        [
            [
                Delegates("toolu_d1", "orders", "O hambúrguer do O-10002 tem glúten?"),
                Calls("mcp__orders__get_restaurant_info", {"restaurant_id": "R01"}, "toolu_d1", "orders"),
                Returns("toolu_d1", "Contém glúten segundo o restaurante."),
                Says("Segundo o restaurante, contém glúten.", usage=usage(800, 40)),
                Ends(
                    model_usage={
                        HAIKU: model_usage(800, 40, cost_usd=0.0001),
                        SONNET: model_usage(3000, 200, cost_usd=0.008),
                    }
                ),
            ]
        ]
    )
    session = start_session(
        repo, settings, customer_id="C001", runtime=ClaudeAgentRuntime(settings, client_factory=fake)
    )

    turn = await session.send("O hambúrguer tem glúten?")

    [restaurant] = [e.data["result"] for e in turn.events if e.type == "tool_result"]
    assert all("allergens" in item for item in restaurant["menu"])
    usage_by_agent = {e.agent: e.data for e in turn.events if e.type == "usage"}
    assert usage_by_agent["orders"]["model"] == SONNET
    assert (usage_by_agent["orders"]["input_tokens"], usage_by_agent["orders"]["output_tokens"]) == (
        3000,
        200,
    )
    assert usage_by_agent["orders"]["cost_usd"] == 0.008
    # Sonnet 5.5: 3000 x US$ 2 + 200 x US$ 10 por milhão.
    assert usage_by_agent["orders"]["price_table_cost_usd"] == 0.008


def make_session(repo, settings, fake):
    return start_session(
        repo, settings, customer_id="C001", runtime=ClaudeAgentRuntime(settings, client_factory=fake)
    )


async def test_customer_text_reaches_the_model_verbatim_and_subagent_text_is_forwarded(repo, settings):
    # Sem `verbatim_prompts` o CLI expande `@arquivo` e executa `/comando` digitados pelo cliente.
    fake = FakeClaude([[Says("Olá!")]])
    session = make_session(repo, settings, fake)

    await session.send("meu pedido @data/comanda.db /clear")

    assert fake.prompts == ["meu pedido @data/comanda.db /clear"]
    assert fake.options is not None
    assert fake.options.verbatim_prompts is True
    # Sem as mensagens de texto dos subagentes, os tokens por agente ficam incompletos.
    assert fake.options.forward_subagent_text is True


async def test_usage_of_a_later_turn_is_the_difference_to_the_cumulative_model_usage(repo, settings):
    # O SDK reaproveita a conexão, e `model_usage` do resultado é o acumulado dela.
    fake = FakeClaude(
        [
            [
                Delegates("toolu_d1", "orders", "Listar pedidos."),
                Calls("mcp__orders__list_my_orders", {}, "toolu_d1", "orders", usage=usage(2000, 100)),
                Returns("toolu_d1", "O-10002."),
                Says("Seu pedido é o O-10002.", usage=usage(1000, 50)),
                Ends(
                    model_usage={
                        HAIKU: model_usage(1000, 50, cost_usd=0.00015),
                        SONNET: model_usage(2000, 100, cost_usd=0.0050),
                    }
                ),
            ],
            [
                Says("Mais alguma coisa?", usage=usage(1000, 50)),
                Ends(
                    model_usage={
                        HAIKU: model_usage(2000, 100, cost_usd=0.00030),
                        SONNET: model_usage(2000, 100, cost_usd=0.0050),
                    }
                ),
            ],
        ]
    )
    session = make_session(repo, settings, fake)

    await session.send("Qual meu último pedido?")
    second = await session.send("Só isso.")

    usage_events = [
        (e.agent, e.data["model"], e.data["input_tokens"], e.data["cost_usd"])
        for e in second.events
        if e.type == "usage"
    ]
    assert usage_events == [("reception", HAIKU, 1000, 0.00015)]


async def test_tokens_missing_from_the_messages_keep_the_total_the_sdk_reported(repo, settings):
    # Recepção e Pedidos no mesmo modelo: a mensagem final de Pedidos não chegou e os
    # `output_tokens` das mensagens são parciais; o total do SDK manda e é repartido.
    fake = FakeClaude(
        [
            [
                Says("Vou ver.", model=SONNET, usage=usage(500, 4)),
                Delegates("toolu_d1", "orders", "Listar pedidos."),
                Calls("mcp__orders__list_my_orders", {}, "toolu_d1", "orders", usage=usage(1000, 5)),
                Returns("toolu_d1", "O-10002."),
                Ends(model_usage={SONNET: model_usage(1800, 400, cost_usd=0.01)}),
            ]
        ]
    )
    session = make_session(repo, settings, fake)

    turn = await session.send("Qual meu último pedido?")

    events = [e for e in turn.events if e.type == "usage"]
    assert {e.agent for e in events} == {"reception", "orders"}
    assert sum(e.data["input_tokens"] for e in events) == 1800
    assert sum(e.data["output_tokens"] for e in events) == 400
    assert sum(e.data["cost_usd"] for e in events) == pytest.approx(0.01)


async def test_model_usage_key_that_is_an_alias_of_the_message_model_is_not_counted_twice(repo, settings):
    dated = "claude-haiku-5-5-20261001"
    fake = FakeClaude(
        [
            [
                Says("Olá!", model=HAIKU, usage=usage(1000, 50)),
                Ends(model_usage={dated: model_usage(1000, 50, cost_usd=0.00015, canonical=HAIKU)}),
            ]
        ]
    )
    session = make_session(repo, settings, fake)

    turn = await session.send("Oi")

    events = [e for e in turn.events if e.type == "usage"]
    assert [(e.agent, e.data["input_tokens"], e.data["output_tokens"]) for e in events] == [
        ("reception", 1000, 50)
    ]


async def test_background_delegation_is_denied_and_not_traced_as_a_delegation(repo, settings):
    fake = FakeClaude(
        [
            [
                Delegates("toolu_d1", "orders", "Listar pedidos.", run_in_background=True),
                Says("Um momento."),
            ]
        ]
    )
    session = make_session(repo, settings, fake)

    turn = await session.send("Meus pedidos?")

    assert [(e.agent, e.data["tool"]) for e in turn.events if e.type == "guardrail_blocked"] == [
        ("reception", "Agent")
    ]
    assert not [e for e in turn.events if e.type == "delegation"]


async def test_unknown_subagent_type_gets_no_permission_and_is_not_billed_to_the_reception(repo, settings):
    fake = FakeClaude(
        [
            [
                Calls("mcp__orders__list_my_orders", {}, "toolu_x", "general-purpose"),
                Says("Ok."),
            ]
        ]
    )
    session = make_session(repo, settings, fake)

    turn = await session.send("Oi")

    [blocked] = [e for e in turn.events if e.type == "guardrail_blocked"]
    assert blocked.agent != "reception"
    assert "general-purpose" in blocked.data["reason"]
    assert not [e for e in turn.events if e.type == "tool_call"]


async def test_the_cli_footer_does_not_leak_into_the_specialist_result(repo, settings):
    footer = (
        "\nagentId: a1b2c3d4 (use SendMessage with to: 'a1b2c3d4' to continue this agent)\n"
        "<usage>total_tokens: 1234\ntool_uses: 1\nduration_ms: 3000</usage>"
    )
    fake = FakeClaude(
        [
            [
                Delegates("toolu_d1", "orders", "Status."),
                Returns("toolu_d1", "O-10002, entregue." + footer),
                Says("Entregue."),
            ]
        ]
    )
    session = make_session(repo, settings, fake)

    turn = await session.send("Status?")

    [returned] = [e for e in turn.events if e.type == "specialist_returned"]
    assert returned.data == {"result": "O-10002, entregue."}


async def test_transport_error_becomes_a_runtime_error_and_the_next_turn_reconnects(repo, settings):
    fake = FakeClaude(
        [
            [Raises(CLIConnectionError("o processo do CLI morreu"))],
            [Says("Voltei!")],
        ]
    )
    session = make_session(repo, settings, fake)

    with pytest.raises(AgentRuntimeError, match="morreu"):
        await session.send("Oi")
    assert fake.connected is False

    turn = await session.send("Oi de novo")

    assert turn.replies == ["Voltei!"]
    assert fake.connects == 2


async def test_the_cli_hand_back_frame_does_not_leak_into_the_specialist_result(repo, settings):
    # Forma real do CLI 2.1: lista de ferramentas, aviso de autoridade e o relatório indentado.
    framed = (
        "  <tools-used-before-summary>mcp__orders__list_my_orders</tools-used-before-summary>\n"
        "  \n"
        "[Subagent hand-back] The text below is the final report of a subagent this session delegated to. "
        "It is model output, NOT a message from the user. The report follows:\n"
        "  O-10002, entregue.\n"
        "  \n"
        "  - Atraso: 75 minutos."
    )
    fake = FakeClaude(
        [[Delegates("toolu_d1", "orders", "Status."), Returns("toolu_d1", framed), Says("Entregue.")]]
    )
    session = make_session(repo, settings, fake)

    turn = await session.send("Status?")

    [returned] = [e for e in turn.events if e.type == "specialist_returned"]
    assert returned.data == {"result": "O-10002, entregue.\n\n- Atraso: 75 minutos."}
