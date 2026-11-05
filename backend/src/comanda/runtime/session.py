"""Atendimento: a interface principal do sistema.

CLI, API e avaliação usam só esta interface: iniciar um atendimento para um cliente com um
runtime, enviar falas do cliente, receber as falas do sistema e os eventos do Rastro, encerrar.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from ..config import Settings
from ..domain.db import Repository
from ..tools import ToolContext, run_tool
from .base import AgentRuntime, Usage
from .masking import PersonalDataMask
from .trace import Actor, Agent, EventType, Specialist, TraceEvent, TraceWriter


class SessionClosed(Exception):
    """O atendimento já foi encerrado."""


@dataclass
class Turn:
    """O que um turno produziu: falas ao cliente e eventos do Rastro, em ordem."""

    replies: list[str] = field(default_factory=list)
    events: list[TraceEvent] = field(default_factory=list)


class Session:
    def __init__(self, repo: Repository, settings: Settings, customer_id: str, runtime: AgentRuntime) -> None:
        customer = repo.get_customer(customer_id)
        if customer is None:
            raise ValueError(f"Cliente {customer_id} não encontrado.")
        self.id = f"S-{uuid.uuid4().hex[:12].upper()}"
        self._mask = PersonalDataMask(customer)
        self._ctx = ToolContext(repo=repo, settings=settings, customer_id=customer_id)
        self._runtime = runtime
        self._turn: Turn | None = None
        self._call_seq = 0
        self.closed = False
        self._writer = TraceWriter(settings.runs_dir, self.id)
        self.events: list[TraceEvent] = []
        self._record("system", EventType.SESSION_STARTED, {"customer_id": customer_id})

    async def send(self, message: str) -> Turn:
        if self.closed:
            raise SessionClosed(f"O atendimento {self.id} já foi encerrado.")
        self._turn = Turn()
        self._record("customer", EventType.CUSTOMER_MESSAGE, {"text": message})
        turn = self._turn
        try:
            await self._runtime.respond(message, self)
        finally:
            self._turn = None
        return turn

    def close(self) -> None:
        if not self.closed:
            self._record("system", EventType.SESSION_ENDED, {})
            self.closed = True

    def _record(self, agent: Actor, type: EventType, data: dict[str, Any]) -> None:
        event = TraceEvent(
            session_id=self.id,
            seq=len(self.events),
            ts=datetime.now(UTC),
            agent=agent,
            type=type,
            data=self._mask.apply(data),
        )
        self._writer.write(event)
        self.events.append(event)
        if self._turn is not None:
            self._turn.events.append(event)

    # --- AgentHost -------------------------------------------------------------

    def say(self, text: str) -> None:
        assert self._turn is not None
        self._turn.replies.append(text)
        self._record("reception", EventType.AGENT_MESSAGE, {"text": text})

    def delegate(self, specialist: Specialist, task: str) -> None:
        self._record("reception", EventType.DELEGATION, {"specialist": specialist, "task": task})

    def specialist_returned(self, specialist: Specialist, result: str) -> None:
        self._record(specialist, EventType.SPECIALIST_RETURNED, {"result": result})

    def call_tool(self, agent: Agent, name: str, args: dict[str, Any]) -> dict[str, Any]:
        self._call_seq += 1
        call_id = f"call-{self._call_seq}"
        self._record(agent, EventType.TOOL_CALL, {"call_id": call_id, "tool": name, "args": args})
        result = run_tool(self._ctx, name, args)
        self._record(
            agent,
            EventType.TOOL_RESULT,
            {"call_id": call_id, "tool": name, "result": result, "is_error": "error" in result},
        )
        if name == "escalate_to_human" and "ticket_id" in result:
            self._record(
                agent,
                EventType.ESCALATION,
                {
                    "ticket_id": result["ticket_id"],
                    "priority": result["priority"],
                    "order_id": args.get("order_id"),
                },
            )
        return result

    def record_usage(self, usage: Usage) -> None:
        self._record(
            usage.agent,
            EventType.USAGE,
            {
                "model": usage.model,
                "input_tokens": usage.input_tokens,
                "output_tokens": usage.output_tokens,
                "cost_usd": usage.cost_usd,
            },
        )


def start_session(repo: Repository, settings: Settings, customer_id: str, runtime: AgentRuntime) -> Session:
    return Session(repo, settings, customer_id, runtime)
