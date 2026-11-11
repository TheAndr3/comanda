"""Runtime roteirizado: reproduz uma sequência fixa de passos, sem chamar modelo.

Cada fala do cliente consome o próximo turno do roteiro. As chamadas de ferramenta são
executadas de verdade pelo Atendimento. Serve para testar tudo acima das ferramentas
(Atendimento, Rastro, guardrails, avaliação) de forma determinística.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from ..agents.roles import Agent, Specialist
from .base import AgentHost, QuotaReading, Usage


@dataclass(frozen=True)
class Say:
    """A Recepção fala com o cliente."""

    text: str


@dataclass(frozen=True)
class Delegate:
    """A Recepção delega uma tarefa a um especialista."""

    specialist: Specialist
    task: str


@dataclass(frozen=True)
class Return:
    """O especialista devolve o resultado à Recepção."""

    specialist: Specialist
    result: str


@dataclass(frozen=True)
class ToolCall:
    """Um agente chama uma ferramenta."""

    agent: Agent
    tool: str
    args: dict[str, Any] = field(default_factory=dict)


Step = Say | Delegate | Return | ToolCall | Usage | QuotaReading


class ScriptExhausted(Exception):
    """O cliente falou mais vezes do que o roteiro previa."""


class ScriptedRuntime:
    def __init__(self, turns: Sequence[Sequence[Step]]) -> None:
        self._turns = [list(turn) for turn in turns]
        self._next = 0

    async def respond(self, message: str, host: AgentHost) -> None:
        if self._next >= len(self._turns):
            raise ScriptExhausted(f"O roteiro tem {len(self._turns)} turnos; não há resposta para: {message}")
        for step in self._turns[self._next]:
            match step:
                case Say(text):
                    host.say(text)
                case Delegate(specialist, task):
                    host.delegate(specialist, task)
                case Return(specialist, result):
                    host.specialist_returned(specialist, result)
                case ToolCall(agent, tool, args):
                    host.call_tool(agent, tool, args)
                case Usage():
                    host.record_usage(step)
                case QuotaReading():
                    host.record_quota(step)
        self._next += 1

    async def aclose(self) -> None:
        pass
