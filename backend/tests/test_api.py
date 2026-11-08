"""Testes de fumaça da API: adaptador fino sobre o Atendimento e a Fila (costuras 1 e 2 do PRD).

Usam o runtime roteirizado e o banco do seed; nenhum chama modelo.
"""

import asyncio
import json
from collections.abc import AsyncIterator

import httpx
import pytest
import uvicorn

from comanda.api import create_app
from comanda.runtime import AgentRuntime, Delegate, Return, Say, ScriptedRuntime, ToolCall
from comanda.tools import run_tool

LATE_ORDER_SCRIPT = [
    [
        Delegate("orders", "Ver o pedido mais recente do cliente."),
        ToolCall("orders", "list_my_orders", {"limit": 1}),
        Return("orders", "Pedido O-10001, em rota."),
        Say("Seu pedido O-10001 está a caminho."),
    ],
]


@pytest.fixture
def runtimes() -> list[AgentRuntime]:
    """Runtimes entregues, em ordem, a cada atendimento iniciado pela API."""
    return []


@pytest.fixture
def app(settings, repo, runtimes):
    return create_app(settings, runtime_factory=lambda: runtimes.pop(0))


@pytest.fixture
async def client(app) -> AsyncIterator[httpx.AsyncClient]:
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://api") as c:
        yield c


@pytest.fixture
async def live_client(app) -> AsyncIterator[httpx.AsyncClient]:
    """Conexão HTTP com um servidor de verdade, para ler o SSE enquanto ele chega."""
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning"))
    task = asyncio.create_task(server.serve())
    async with asyncio.timeout(5):
        while not server.started:
            await asyncio.sleep(0.01)
    port = server.servers[0].sockets[0].getsockname()[1]
    async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}", timeout=5) as c:
        yield c
    server.should_exit = True
    await task


async def sse_events(lines: AsyncIterator[str]) -> AsyncIterator[tuple[str, dict]]:
    """Lê eventos `id`/`data` do SSE, ignorando comentários de keep-alive."""
    event_id, data = None, None
    async for line in lines:
        if line.startswith("id: "):
            event_id = line.removeprefix("id: ")
        elif line.startswith("data: "):
            data = json.loads(line.removeprefix("data: "))
        elif line == "" and data is not None:
            yield event_id or "", data
            event_id, data = None, None


async def test_session_events_stream_live_in_trace_order(live_client, runtimes, settings):
    runtimes.append(ScriptedRuntime(LATE_ORDER_SCRIPT))
    started = await live_client.post("/sessions", json={"customer_id": "C001"})
    assert started.status_code == 201
    session_id = started.json()["session_id"]

    received: list[tuple[str, dict]] = []
    async with live_client.stream("GET", f"/sessions/{session_id}/events") as stream:
        assert stream.headers["content-type"].startswith("text/event-stream")
        events = sse_events(stream.aiter_lines())
        received.append(await anext(events))
        assert received[0][1]["type"] == "session_started"

        sent = await live_client.post(f"/sessions/{session_id}/messages", json={"text": "Cadê meu pedido?"})
        assert sent.status_code == 200
        assert sent.json()["replies"] == ["Seu pedido O-10001 está a caminho."]
        closed = await live_client.post(f"/sessions/{session_id}/close")
        assert closed.status_code == 204

        received.extend([event async for event in events])

    trace = [
        json.loads(line)
        for line in (settings.runs_dir / f"{session_id}.jsonl").read_text("utf-8").splitlines()
    ]
    assert [data for _, data in received] == trace
    assert [event_id for event_id, _ in received] == [str(e["seq"]) for e in trace]
    assert [e["type"] for e in trace] == [
        "session_started",
        "customer_message",
        "delegation",
        "tool_call",
        "tool_result",
        "specialist_returned",
        "agent_message",
        "session_ended",
    ]


async def finished_session(client, runtimes, customer_id: str = "C001") -> str:
    runtimes.append(ScriptedRuntime(LATE_ORDER_SCRIPT))
    session_id = (await client.post("/sessions", json={"customer_id": customer_id})).json()["session_id"]
    await client.post(f"/sessions/{session_id}/messages", json={"text": "Cadê meu pedido?"})
    await client.post(f"/sessions/{session_id}/close")
    return session_id


async def read_sse(response: httpx.Response) -> list[tuple[str, dict]]:
    async def lines() -> AsyncIterator[str]:
        for line in response.text.splitlines():
            yield line
        yield ""

    return [event async for event in sse_events(lines())]


async def test_reconnect_resumes_after_last_event_id(client, runtimes):
    session_id = await finished_session(client, runtimes)

    response = await client.get(f"/sessions/{session_id}/events", headers={"Last-Event-ID": "5"})

    events = await read_sse(response)
    assert [event_id for event_id, _ in events] == ["6", "7"]
    assert [data["type"] for _, data in events] == ["agent_message", "session_ended"]
    # Um id fora do Rastro não pula nem repete eventos: o stream recomeça do início.
    from_start = await client.get(f"/sessions/{session_id}/events", headers={"Last-Event-ID": "-5"})
    assert [event_id for event_id, _ in await read_sse(from_start)] == [str(seq) for seq in range(8)]


async def test_panel_origin_is_allowed_by_cors(client):
    response = await client.get("/customers", headers={"Origin": "http://localhost:3000"})

    assert response.headers["access-control-allow-origin"] == "http://localhost:3000"


async def test_lists_customers_for_the_panel_selector(client):
    response = await client.get("/customers")

    assert response.status_code == 200
    customers = response.json()
    assert {"id": "C003", "name": "Carla Souza"} in customers
    # O seletor só precisa de id e nome; e-mail e telefone não saem pela API.
    assert all(set(c) == {"id", "name"} for c in customers)


async def test_session_errors_map_to_http_status(client, runtimes):
    unknown = await client.post("/sessions", json={"customer_id": "C999"})
    assert unknown.status_code == 404

    session_id = await finished_session(client, runtimes)
    after_close = await client.post(f"/sessions/{session_id}/messages", json={"text": "Oi de novo"})
    assert after_close.status_code == 409
    assert "encerrado" in after_close.json()["detail"]

    missing = await client.get("/sessions/S-NAOEXISTE/events")
    assert missing.status_code == 404


def pending_lost_order(ctx_for) -> str:
    """C003 relata o pedido perdido; o reembolso integral passa do limite e fica pendente."""
    return run_tool(ctx_for("C003"), "issue_refund", {"order_id": "O-10004", "issue": "not_delivered"})[
        "refund_id"
    ]


def open_ticket(ctx_for) -> str:
    return run_tool(ctx_for("C006"), "escalate_to_human", {"summary": "Quer falar com um humano."})[
        "ticket_id"
    ]


async def test_queue_lists_pending_refunds_and_open_tickets(client, ctx_for):
    refund_id = pending_lost_order(ctx_for)
    ticket_id = open_ticket(ctx_for)

    response = await client.get("/queue")

    assert response.status_code == 200
    queue = response.json()
    [refund] = queue["pending_refunds"]
    assert refund["id"] == refund_id
    assert refund["customer_id"] == "C003"
    assert refund["rule_id"] == "R-LOST"
    assert refund["amount_cents"] == 10979
    assert refund["status"] == "pending_approval"
    [ticket] = queue["open_tickets"]
    assert ticket["id"] == ticket_id
    assert ticket["summary"] == "Quer falar com um humano."


async def test_approve_takes_refund_out_of_queue_and_only_once(client, ctx_for):
    refund_id = pending_lost_order(ctx_for)

    approved = await client.post(f"/queue/refunds/{refund_id}/approve", json={"decided_by": "marina"})

    assert approved.status_code == 200
    assert approved.json()["status"] == "approved"
    assert approved.json()["decided_by"] == "marina"
    assert approved.json()["amount_cents"] == 10979
    assert (await client.get("/queue")).json()["pending_refunds"] == []
    again = await client.post(f"/queue/refunds/{refund_id}/approve", json={"decided_by": "marina"})
    assert again.status_code == 409
    assert "já foi decidido" in again.json()["detail"]


async def test_reject_requires_a_reason_and_records_it(client, ctx_for):
    refund_id = pending_lost_order(ctx_for)

    blank = await client.post(
        f"/queue/refunds/{refund_id}/reject", json={"decided_by": "marina", "reason": "   "}
    )
    assert blank.status_code == 422

    rejected = await client.post(
        f"/queue/refunds/{refund_id}/reject", json={"decided_by": "marina", "reason": "Entrega confirmada."}
    )
    assert rejected.status_code == 200
    assert rejected.json()["status"] == "rejected"
    assert rejected.json()["rejection_reason"] == "Entrega confirmada."


async def test_resolve_ticket_and_unknown_items_are_not_found(client, ctx_for):
    ticket_id = open_ticket(ctx_for)

    resolved = await client.post(f"/queue/tickets/{ticket_id}/resolve")

    assert resolved.status_code == 200
    assert resolved.json()["status"] == "resolved"
    assert (await client.get("/queue")).json()["open_tickets"] == []
    assert (await client.post(f"/queue/tickets/{ticket_id}/resolve")).status_code == 409
    assert (await client.post("/queue/tickets/T-NAOEXISTE/resolve")).status_code == 404
    unknown_refund = await client.post("/queue/refunds/R-NAOEXISTE/approve", json={"decided_by": "marina"})
    assert unknown_refund.status_code == 404


class HeldTurn:
    """Runtime cujo turno só termina quando o teste libera; simula um modelo lento."""

    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def respond(self, message: str, host) -> None:
        self.started.set()
        await self.release.wait()
        host.say("Desculpe a demora.")


async def test_closing_during_a_turn_waits_for_it_so_session_ended_is_last(client, runtimes, settings):
    held = HeldTurn()
    runtimes.append(held)
    session_id = (await client.post("/sessions", json={"customer_id": "C001"})).json()["session_id"]
    send = asyncio.create_task(client.post(f"/sessions/{session_id}/messages", json={"text": "Oi"}))
    await held.started.wait()

    close = asyncio.create_task(client.post(f"/sessions/{session_id}/close"))
    await asyncio.sleep(0.05)
    held.release.set()

    assert (await send).json()["replies"] == ["Desculpe a demora."]
    assert (await close).status_code == 204
    lines = (settings.runs_dir / f"{session_id}.jsonl").read_text("utf-8").splitlines()
    assert [json.loads(line)["type"] for line in lines][-2:] == ["agent_message", "session_ended"]
