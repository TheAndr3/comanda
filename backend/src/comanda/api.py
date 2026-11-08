"""API do Comanda para o painel: adaptador fino sobre o Atendimento e a Fila (ADR-0005).

O painel escolhe um cliente, inicia um atendimento, envia falas e assina os eventos do Rastro
por SSE, no mesmo formato do arquivo JSONL (ADR-0010). Os atendimentos vivos ficam na memória
do processo (ADR-0021). `comanda serve` sobe esta API com o runtime do Claude Agent SDK.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Annotated

from fastapi import Depends, FastAPI, Header, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.sse import EventSourceResponse, ServerSentEvent
from pydantic import BaseModel, StringConstraints

from .config import Settings
from .domain.db import Repository
from .domain.models import Refund, Ticket
from .domain.queue import (
    Queue,
    QueueError,
    QueueItemNotFound,
    approve_refund,
    list_queue,
    reject_refund,
    resolve_ticket,
)
from .runtime import AgentRuntime, AgentRuntimeError, ClaudeAgentRuntime, Session, start_session

RuntimeFactory = Callable[[], AgentRuntime]
NonEmpty = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class CustomerOption(BaseModel):
    """Um cliente no seletor do painel; e-mail e telefone não saem pela API."""

    id: str
    name: str


class StartSession(BaseModel):
    customer_id: str


class SessionStarted(BaseModel):
    session_id: str


class CustomerMessage(BaseModel):
    text: NonEmpty


class TurnResult(BaseModel):
    # Só as falas ao cliente: os eventos do Rastro chegam pelo SSE.
    replies: list[str]


class Approval(BaseModel):
    decided_by: NonEmpty


class Rejection(BaseModel):
    decided_by: NonEmpty
    reason: NonEmpty


@dataclass
class _LiveSession:
    session: Session
    runtime: AgentRuntime
    # Um turno por vez: o Atendimento não aceita uma fala enquanto responde a outra.
    turn_lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    async def close(self) -> None:
        if self.session.closed:
            return
        try:
            aclose = getattr(self.runtime, "aclose", None)
            if aclose is not None:
                await aclose()
        finally:
            self.session.close()


def _live_session(request: Request, session_id: str) -> _LiveSession:
    live = request.app.state.sessions.get(session_id)
    if live is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Atendimento {session_id} não encontrado.")
    return live


Live = Annotated[_LiveSession, Depends(_live_session)]


def create_app(settings: Settings, runtime_factory: RuntimeFactory | None = None) -> FastAPI:
    repo = Repository(settings.db_path)
    new_runtime = runtime_factory or (lambda: ClaudeAgentRuntime(settings))
    sessions: dict[str, _LiveSession] = {}

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        yield
        # Um runtime que falha ao fechar não impede os outros atendimentos de terminar.
        await asyncio.gather(*(live.close() for live in sessions.values()), return_exceptions=True)

    app = FastAPI(title="Comanda", lifespan=lifespan)
    app.state.sessions = sessions
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(settings.cors_origins),
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type", "Last-Event-ID"],
    )

    @app.exception_handler(QueueError)
    async def queue_error(_: Request, exc: QueueError) -> JSONResponse:
        code = status.HTTP_404_NOT_FOUND if isinstance(exc, QueueItemNotFound) else status.HTTP_409_CONFLICT
        return JSONResponse({"detail": str(exc)}, status_code=code)

    @app.get("/customers")
    async def customers() -> list[CustomerOption]:
        found = (repo.get_customer(customer_id) for customer_id in repo.list_customer_ids())
        return [CustomerOption(id=c.id, name=c.name) for c in found if c is not None]

    @app.post("/sessions", status_code=status.HTTP_201_CREATED)
    async def start(body: StartSession) -> SessionStarted:
        if repo.get_customer(body.customer_id) is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, f"Cliente {body.customer_id} não encontrado.")
        runtime = new_runtime()
        session = start_session(repo, settings, body.customer_id, runtime)
        sessions[session.id] = _LiveSession(session, runtime)
        return SessionStarted(session_id=session.id)

    @app.post("/sessions/{session_id}/messages")
    async def send(live: Live, body: CustomerMessage) -> TurnResult:
        if live.session.closed:
            raise HTTPException(
                status.HTTP_409_CONFLICT, f"O atendimento {live.session.id} já foi encerrado."
            )
        if live.turn_lock.locked():
            raise HTTPException(
                status.HTTP_409_CONFLICT, "O atendimento ainda está respondendo à fala anterior."
            )
        async with live.turn_lock:
            try:
                turn = await live.session.send(body.text)
            except AgentRuntimeError as exc:
                raise HTTPException(
                    status.HTTP_502_BAD_GATEWAY, f"O runtime de agentes falhou: {exc}"
                ) from exc
        return TurnResult(replies=turn.replies)

    @app.get("/sessions/{session_id}/events", response_class=EventSourceResponse)
    async def events(
        live: Live, last_event_id: Annotated[int | None, Header()] = None
    ) -> AsyncIterator[ServerSentEvent]:
        # O `id` de cada evento é a posição no Rastro; ao reconectar, o navegador manda o último
        # recebido em `Last-Event-ID` e o stream continua dali.
        after = last_event_id if last_event_id is not None else -1
        async for event in live.session.follow(after):
            yield ServerSentEvent(raw_data=json.dumps(event.to_dict(), ensure_ascii=False), id=str(event.seq))

    @app.post("/sessions/{session_id}/close", status_code=status.HTTP_204_NO_CONTENT)
    async def close(live: Live) -> None:
        # Espera o turno em andamento: `session_ended` é sempre o último evento do Rastro.
        async with live.turn_lock:
            await live.close()

    # --- Fila do atendente humano (ADR-0007) -----------------------------------

    @app.get("/queue")
    async def queue() -> Queue:
        return list_queue(repo)

    def decided(refund_id: str) -> Refund:
        refund = repo.get_refund(refund_id)
        assert refund is not None
        return refund

    @app.post("/queue/refunds/{refund_id}/approve")
    async def approve(refund_id: str, body: Approval) -> Refund:
        approve_refund(repo, refund_id, decided_by=body.decided_by)
        return decided(refund_id)

    @app.post("/queue/refunds/{refund_id}/reject")
    async def reject(refund_id: str, body: Rejection) -> Refund:
        reject_refund(repo, refund_id, decided_by=body.decided_by, reason=body.reason)
        return decided(refund_id)

    @app.post("/queue/tickets/{ticket_id}/resolve")
    async def resolve(ticket_id: str) -> Ticket:
        resolve_ticket(repo, ticket_id)
        ticket = repo.get_ticket(ticket_id)
        assert ticket is not None
        return ticket

    return app
