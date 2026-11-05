"""Rastro: o registro, em ordem, de cada passo de um atendimento (ADR-0010).

Cada evento tem versão do esquema (`v`), id do atendimento, posição (`seq`), horário em UTC,
quem agiu (`agent`), tipo e dados. O mesmo formato alimenta o arquivo JSONL, o SSE do
painel, o replay e as métricas.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal

SCHEMA_VERSION = 1

Specialist = Literal["orders", "refunds"]
Agent = Literal["reception", Specialist]
# Quem age num evento: um agente, o cliente, ou o próprio Atendimento (início e fim).
Actor = Literal[Agent, "customer", "system"]


class EventType(StrEnum):
    SESSION_STARTED = "session_started"
    CUSTOMER_MESSAGE = "customer_message"
    AGENT_MESSAGE = "agent_message"
    DELEGATION = "delegation"
    SPECIALIST_RETURNED = "specialist_returned"
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    # Ainda sem emissor: o hook de elegibilidade (ADR-0008) entra em outro ticket.
    GUARDRAIL_BLOCKED = "guardrail_blocked"
    ESCALATION = "escalation"
    USAGE = "usage"
    SESSION_ENDED = "session_ended"


@dataclass(frozen=True)
class TraceEvent:
    session_id: str
    seq: int
    ts: datetime
    agent: Actor
    type: EventType
    data: dict[str, Any]
    v: int = SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {
            "v": self.v,
            "session_id": self.session_id,
            "seq": self.seq,
            "ts": self.ts.isoformat(),
            "agent": self.agent,
            "type": self.type.value,
            "data": self.data,
        }


class TraceWriter:
    """Grava um atendimento em `<dir>/<session_id>.jsonl`, uma linha por evento, na hora."""

    def __init__(self, directory: Path, session_id: str) -> None:
        directory.mkdir(parents=True, exist_ok=True)
        self.path = directory / f"{session_id}.jsonl"

    def write(self, event: TraceEvent) -> None:
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(event.to_dict(), ensure_ascii=False) + "\n")
