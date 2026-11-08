"""Atendimento com o runtime do Claude Agent SDK, sobre um Claude Code de mentira (costura 2).

Nenhum teste chama modelo: o falso reproduz as mensagens que o SDK entregaria e chama as
ferramentas pelo servidor MCP em processo, como o CLI faria.
"""

import pytest

from comanda.runtime import AgentRuntimeError, ClaudeAgentRuntime, start_session

from .fake_claude import HAIKU, SONNET, Calls, Delegates, Ends, FakeClaude, Returns, Says, model_usage, usage


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
