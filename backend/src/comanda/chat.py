"""`comanda chat`: o Desenvolvedor conversa com o atendimento pelo terminal.

Adaptador fino sobre o Atendimento: lê a fala do cliente, mostra as falas da Recepção e,
com `--trace`, os eventos do Rastro de cada turno. No fim, resume tokens por modelo e o
custo equivalente em API, que é uma estimativa (ADR-0014).
"""

from __future__ import annotations

import asyncio
import json
import sys
from collections import defaultdict
from typing import Any

from .config import Settings
from .domain.db import Repository
from .runtime import AgentRuntime, AgentRuntimeError, ClaudeAgentRuntime, Session, TraceEvent, start_session

AGENT_PT = {"reception": "Recepção", "orders": "Pedidos", "refunds": "Reembolsos"}
EXIT_WORDS = {"sair", "exit", "quit"}


def _short(value: Any, limit: int = 140) -> str:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def describe(event: TraceEvent) -> str | None:
    """Uma linha legível para um evento do Rastro; `None` para falas, já mostradas à parte."""
    agent = AGENT_PT.get(event.agent, event.agent)
    d = event.data
    match event.type:
        case "delegation":
            return f"Recepção → {AGENT_PT[d['specialist']]}: {_short(d['task'])}"
        case "tool_call":
            return f"{agent} chama {d['tool']} {_short(d['args'])}"
        case "tool_result":
            status = "erro" if d["is_error"] else "ok"
            return f"{agent} ← {d['tool']} ({status}): {_short(d['result'])}"
        case "specialist_returned":
            return f"{agent} → Recepção: {_short(d['result'])}"
        case "guardrail_blocked":
            return f"bloqueado ({d['guardrail']}): {agent} tentou {d['tool']}. {d['reason']}"
        case "escalation":
            return f"ticket {d['ticket_id']} aberto, prioridade {d['priority']}"
        case "usage":
            cost = d["cost_usd"]
            cost_text = f"US$ {cost:.5f}" if cost is not None else "custo desconhecido"
            cached = d["cache_read_tokens"] + d["cache_write_tokens"]
            return (
                f"{agent} em {d['model']}: {d['input_tokens']} entrada (+{cached} cache), "
                f"{d['output_tokens']} saída, {cost_text} (estimativa)"
            )
    return None


def usage_summary(session: Session) -> list[str]:
    """Tokens e custo equivalente em API por modelo, com o cálculo pela tabela ao lado (ADR-0014)."""
    totals: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    unpriced: set[str] = set()
    for event in session.events:
        if event.type == "usage":
            data = event.data
            model = totals[data["model"]]
            for key in ("input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens"):
                model[key] += data[key]
            if data["cost_usd"] is None:
                unpriced.add(data["model"])
            model["cost_usd"] += data["cost_usd"] or 0
            model["price_table_cost_usd"] += data["price_table_cost_usd"] or 0
    if not totals:
        return []
    lines = [
        f"  {model}: {int(t['input_tokens'])} entrada, {int(t['cache_read_tokens'])} lidos do cache, "
        f"{int(t['cache_write_tokens'])} gravados no cache, {int(t['output_tokens'])} saída; "
        f"estimativa US$ {t['cost_usd']:.5f} (tabela pública: US$ {t['price_table_cost_usd']:.5f})"
        for model, t in totals.items()
    ]
    total = sum(t["cost_usd"] for t in totals.values())
    lines.append(f"  Custo equivalente em API (estimativa): US$ {total:.5f}")
    if unpriced:
        lines.append(f"  Sem preço conhecido, fora do total: {', '.join(sorted(unpriced))}")
    return lines


async def run_chat(
    repo: Repository,
    settings: Settings,
    customer_id: str,
    show_trace: bool = False,
    runtime: AgentRuntime | None = None,
) -> int:
    customer = repo.get_customer(customer_id)
    if customer is None:
        print(f"Cliente {customer_id} não encontrado.", file=sys.stderr)
        return 1
    runtime = runtime or ClaudeAgentRuntime(settings)
    session = start_session(repo, settings, customer_id, runtime)
    print(f"Atendimento {session.id} de {customer_id} {customer.name}. Digite 'sair' para encerrar.")
    status = 0
    try:
        while True:
            try:
                line = (await asyncio.to_thread(input, "\nvocê> ")).strip()
            except (EOFError, KeyboardInterrupt):
                break
            if not line:
                continue
            if line.lower() in EXIT_WORDS:
                break
            try:
                turn = await session.send(line)
            except AgentRuntimeError as exc:
                print(f"O runtime de agentes falhou: {exc}", file=sys.stderr)
                if "login" in str(exc).lower():
                    print(
                        "Entre na sua conta do Claude Code (rode `claude` e use /login) ou defina "
                        "CLAUDE_CODE_OAUTH_TOKEN com um token de `claude setup-token`.",
                        file=sys.stderr,
                    )
                status = 1
                break
            if show_trace:
                for event in turn.events:
                    if (text := describe(event)) is not None:
                        print(f"  · {text}")
            for reply in turn.replies:
                print(f"Recepção> {reply}")
    finally:
        aclose = getattr(runtime, "aclose", None)
        if aclose is not None:
            await aclose()
        session.close()
    summary = usage_summary(session)
    if summary:
        print("\nTokens por modelo:")
        print("\n".join(summary))
    print(f"Rastro em {session.trace_path}")
    return status
