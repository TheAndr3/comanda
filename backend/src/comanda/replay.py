"""Exportação de atendimentos para o replay público do painel (ADR-0002, ADR-0012).

`comanda export-replay` copia Rastros escolhidos de `runs/` para o diretório versionado do
painel, sem mudar um byte, e mantém ali um índice com título curto e o que cada replay mostra,
para a tela de escolha.
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from .domain.models import RefundStatus
from .runtime.masking import CustomersMask
from .runtime.trace import EventType

INDEX_FILE = "index.json"
INDEX_VERSION = 1


class ReplayError(Exception):
    """Um atendimento escolhido não pode ir para o replay."""


_SPECIALIST_NAMES = {"orders": "Pedidos", "refunds": "Reembolsos"}
# O id vira nome de arquivo: só letras, dígitos e hífen, como `S-7F6D91F123ED`.
_SESSION_ID = re.compile(r"[A-Za-z0-9-]+")


def _read_events(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _title(session_id: str, events: list[dict[str, Any]]) -> str:
    """A primeira fala do cliente, que costuma dizer o problema; sem fala, o id do atendimento."""
    return next((e["data"]["text"] for e in events if e["type"] == EventType.CUSTOMER_MESSAGE), session_id)


def _shows(events: list[dict[str, Any]]) -> list[str]:
    """O que o replay mostra, em frases curtas, lido dos eventos do Rastro."""
    specialists: list[str] = list(
        dict.fromkeys(e["data"]["specialist"] for e in events if e["type"] == EventType.DELEGATION)
    )
    shows = []
    if specialists:
        names = " e ".join(_SPECIALIST_NAMES.get(s, s) for s in specialists)
        shows.append(f"Recepção delega a {names}")
    for event in events:
        data = event["data"]
        if event["type"] == EventType.GUARDRAIL_BLOCKED:
            shows.append(f"Guardrail {data['guardrail']} bloqueia {data['tool']}")
        elif event["type"] == EventType.ESCALATION:
            shows.append(f"Escalonamento a um atendente humano, prioridade {data['priority']}")
        elif event["type"] == EventType.TOOL_RESULT and data["tool"] == "issue_refund":
            result = data["result"]
            amount, rule = result.get("amount"), result.get("rule_id")
            if result.get("status") == RefundStatus.APPROVED:
                shows.append(f"Reembolso automático de {amount} (regra {rule})")
            elif result.get("status") == RefundStatus.PENDING_APPROVAL:
                shows.append(f"Reembolso de {amount} pendente de aprovação humana (regra {rule})")
    return shows


def _find_trace(runs_dir: Path, session_id: str) -> Path:
    """O Rastro do atendimento em `runs/`, também dentro de uma rodada de avaliação (`runs/eval/...`)."""
    found = next(runs_dir.rglob(f"{session_id}.jsonl"), None) if _SESSION_ID.fullmatch(session_id) else None
    if found is None:
        raise ReplayError(f"Rastro do atendimento {session_id} não encontrado em {runs_dir}.")
    return found


def _read_index(replays_dir: Path) -> list[dict[str, Any]]:
    path = replays_dir / INDEX_FILE
    if not path.exists():
        return []
    return json.loads(path.read_text(encoding="utf-8"))["replays"]


def _check_masked(session_id: str, events: list[dict[str, Any]], mask: CustomersMask) -> None:
    """Recusa o Rastro em que algum evento ainda tem nome, telefone ou e-mail de um cliente.

    A gravação mascara só o cliente do atendimento (ADR-0008); aqui entram todos os clientes do
    banco, porque um replay público não pode mostrar dado de ninguém.
    """
    for event in events:
        if mask.apply(event) != event:
            raise ReplayError(
                f"O Rastro {session_id} (evento {event['seq']}) tem nome, telefone ou e-mail de um "
                "cliente sem máscara; nada foi exportado."
            )


def export_replays(
    session_ids: Sequence[str],
    runs_dir: Path,
    replays_dir: Path,
    mask: CustomersMask,
    title: str | None = None,
) -> list[dict[str, Any]]:
    """Copia os Rastros para `replays_dir` e põe cada um no índice; devolve as entradas.

    O título é `title`, que só vale para um atendimento, ou a primeira fala do cliente.
    Confere todos antes de gravar qualquer um: se um falha, nada muda no diretório do replay.
    Um atendimento exportado de novo troca a própria entrada, no mesmo lugar do índice.
    """
    if title is not None and len(session_ids) != 1:
        raise ReplayError("--title vale para um atendimento só; exporte um de cada vez.")
    chosen = []
    for session_id in session_ids:
        source = _find_trace(runs_dir, session_id)
        events = _read_events(source)
        _check_masked(session_id, events, mask)
        chosen.append((source, events))

    replays_dir.mkdir(parents=True, exist_ok=True)
    index = {entry["session_id"]: entry for entry in _read_index(replays_dir)}
    entries = []
    for source, events in chosen:
        session_id = source.stem
        (replays_dir / source.name).write_bytes(source.read_bytes())
        entry = {
            "session_id": session_id,
            "title": title or _title(session_id, events),
            "customer_id": events[0]["data"].get("customer_id"),
            "shows": _shows(events),
        }
        index[session_id] = entry
        entries.append(entry)
    document = {"v": INDEX_VERSION, "replays": list(index.values())}
    (replays_dir / INDEX_FILE).write_text(
        json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return entries
