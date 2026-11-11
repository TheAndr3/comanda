"""Rodada de um cenário: cliente simulado conversando com o Atendimento (costura 3 do PRD).

Cada cenário parte de um banco recém-criado pelo seed, conversa pela interface do
Atendimento até o cliente simulado encerrar ou acabarem os turnos, e confere o estado
final do banco por código. Depois o juiz lê a conversa e aponta as violações da fala; uma
violação grave reprova o cenário mesmo com o estado certo (ADR-0011).
"""

from __future__ import annotations

import contextlib
import tempfile
import time
from dataclasses import dataclass, field, replace
from pathlib import Path

from ..config import Settings
from ..data.seed import seed_database
from ..runtime import AgentRuntime, EventType, QuotaReading, TraceEvent, start_session
from ..runtime.masking import CustomersMask
from .judge import Judge, Violation, guardrail_violations
from .providers import Provider
from .scenario import Scenario
from .simulated_customer import NO_REPLY, SimulatedCustomer, TranscriptLine
from .state import check_final_state, needed_human, take_baseline

MAX_CUSTOMER_TURNS = 12


@dataclass(frozen=True)
class Tokens:
    """Tokens de um modelo; `input` não inclui os lidos e gravados no cache, como em `Usage`."""

    input: int = 0
    output: int = 0
    cache_read: int = 0
    cache_write: int = 0

    def __add__(self, other: Tokens) -> Tokens:
        return Tokens(
            self.input + other.input,
            self.output + other.output,
            self.cache_read + other.cache_read,
            self.cache_write + other.cache_write,
        )


@dataclass(frozen=True)
class ScenarioResult:
    scenario_id: str
    customer_id: str
    session_id: str
    trace_path: Path
    # Diferenças entre o estado final do banco e o esperado.
    mismatches: list[str]
    transcript: list[TranscriptLine] = field(default_factory=list)
    # A conversa acabou porque o cliente simulado encerrou, e não por falta de turnos.
    ended_by_customer: bool = False
    # Falha do runtime ou do provedor que interrompeu a conversa.
    error: str | None = None
    # Falha do juiz (limite de uso do provedor, resposta fora do formato): o agente pode estar
    # certo, e o atendimento fica sem veredito em vez de reprovado.
    judge_error: str | None = None
    # Tipos de violação que o juiz inventou e foram ignorados (ADR-0023).
    ignored_judge_kinds: list[str] = field(default_factory=list)
    duration_s: float = 0.0
    violations: list[Violation] = field(default_factory=list)
    # O atendimento passou por um humano: abriu ticket ou deixou reembolso pendente.
    needed_human: bool = False
    # Qual repetição do cenário na rodada; o pass^3 roda alguns cenários três vezes.
    trial: int = 1
    # Custo equivalente em API (ADR-0014), estimativa em dólares, e tokens por modelo.
    cost_usd: float = 0.0
    tokens_by_model: dict[str, Tokens] = field(default_factory=dict)
    # Quanto o sistema levou para responder a cada fala do cliente.
    response_times_s: list[float] = field(default_factory=list)
    # Leituras da cota da assinatura que o runtime informou, em ordem (ADR-0024).
    quota: list[QuotaReading] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        no_failure = self.error is None and not self.mismatches and not self.grave_violations
        return no_failure and self.judge_error is None

    @property
    def inconclusive(self) -> bool:
        """Só a falha do juiz impede o veredito: sem erro do agente, sem estado errado."""
        no_failure = self.error is None and not self.mismatches and not self.grave_violations
        return no_failure and self.judge_error is not None

    @property
    def grave_violations(self) -> list[Violation]:
        return [v for v in self.violations if v.severity == "grave"]

    @property
    def customer_turns(self) -> int:
        return sum(1 for line in self.transcript if line.role == "customer")


def failed_result(
    scenario: Scenario, settings: Settings, trial: int, error: str, session_id: str = "(sem atendimento)"
) -> ScenarioResult:
    """O resultado de um cenário que nem chegou a conversar: reprova só ele, sem derrubar a rodada."""
    return ScenarioResult(
        scenario_id=scenario.id,
        customer_id=scenario.customer_id,
        session_id=session_id,
        trace_path=settings.runs_dir,
        mismatches=[],
        error=error,
        trial=trial,
    )


async def run_scenario(
    scenario: Scenario,
    *,
    runtime: AgentRuntime,
    provider: Provider,
    settings: Settings,
    judge: Judge | None = None,
    trial: int = 1,
    max_turns: int = MAX_CUSTOMER_TURNS,
) -> ScenarioResult:
    """Roda um cenário. Recria o banco em `settings.db_path`: nunca aponte para o banco de uso."""
    mask = CustomersMask()  # sem banco ainda, nada a mascarar
    try:
        repo = seed_database(settings.db_path)
        # Mensagens de erro e trechos de violação podem citar dados de qualquer cliente.
        mask = CustomersMask.of(repo)
        baseline = take_baseline(repo)
        customer = repo.get_customer(scenario.customer_id)
        if customer is None:
            raise ValueError(
                f"O cenário {scenario.id} referencia o cliente {scenario.customer_id}, que não existe."
            )
        simulated = SimulatedCustomer(scenario, customer.name.split()[0], provider)
        session = start_session(repo, settings, scenario.customer_id, runtime)
    except Exception as exc:
        # Cenário quebrado: fecha o runtime e reprova só ele; os outros da rodada seguem.
        with contextlib.suppress(Exception):  # o erro que vale é o de montagem
            await runtime.aclose()
        return failed_result(scenario, settings, trial, mask.apply(f"{type(exc).__name__}: {exc}"))

    transcript: list[TranscriptLine] = []
    response_times_s: list[float] = []
    ended_by_customer = False
    error: str | None = None
    started = time.monotonic()
    try:
        for _ in range(max_turns):
            message = await simulated.next_message(transcript)
            if message is None:
                ended_by_customer = True
                break
            transcript.append(TranscriptLine("customer", message))
            sent = time.monotonic()
            try:
                turn = await session.send(message)
            finally:
                # Também o turno que falhou: o tempo dele fica no relatório (o atendimento com erro
                # só fica fora das médias de custo e latência).
                response_times_s.append(time.monotonic() - sent)
            transcript.append(TranscriptLine("system", "\n\n".join(turn.replies) or NO_REPLY))
    except Exception as exc:
        # Uma falha num cenário reprova esse cenário, sem derrubar a rodada inteira.
        error = mask.apply(f"{type(exc).__name__}: {exc}")
    finally:
        try:
            session.close()  # primeiro: o Rastro registra o fim mesmo que o runtime falhe ao fechar
        except Exception as exc:
            error = error or mask.apply(f"{type(exc).__name__}: {exc}")
        # Falhar ao fechar o runtime não pode trocar o resultado real do atendimento.
        with contextlib.suppress(Exception):
            await runtime.aclose()
    duration_s = time.monotonic() - started

    # O gasto sai primeiro: o que já rodou conta mesmo que algo adiante falhe.
    cost_usd = _cost_usd(session.events)
    tokens_by_model = _tokens_by_model(session.events)
    quota = [
        QuotaReading(e.data["window"], e.data["status"], e.data["utilization"], e.data["resets_at"])
        for e in session.events
        if e.type == EventType.QUOTA
    ]

    violations: list[Violation] = []
    judge_error: str | None = None
    ignored_judge_kinds: list[str] = []
    mismatches: list[str] = []
    human = False
    try:
        violations = guardrail_violations(session.events)
        if judge is not None and error is None:
            try:
                review = await judge.review(customer, transcript, session.events)
                violations += review.violations
                ignored_judge_kinds = review.ignored_kinds
            except Exception as exc:
                judge_error = mask.apply(f"{type(exc).__name__}: {exc}")
        mismatches = check_final_state(repo, scenario, baseline)
        human = needed_human(repo, baseline)
    except Exception as exc:
        error = error or mask.apply(f"{type(exc).__name__}: {exc}")
    # O trecho citado de uma violação (other_customer_data) iria ao relatório versionado.
    violations = [replace(v, detail=mask.apply(v.detail)) for v in violations]

    return ScenarioResult(
        scenario_id=scenario.id,
        customer_id=scenario.customer_id,
        session_id=session.id,
        trace_path=session.trace_path,
        mismatches=mismatches,
        transcript=transcript,
        ended_by_customer=ended_by_customer,
        error=error,
        judge_error=judge_error,
        ignored_judge_kinds=ignored_judge_kinds,
        duration_s=duration_s,
        violations=violations,
        trial=trial,
        needed_human=human,
        cost_usd=cost_usd,
        tokens_by_model=tokens_by_model,
        response_times_s=response_times_s,
        quota=quota,
    )


def seeded_customers_mask() -> CustomersMask:
    """A máscara de todos os clientes do seed, para erros de quem nem chegou a abrir um banco."""
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        return CustomersMask.of(seed_database(Path(tmp) / "mask.db"))


def _usage(events: list[TraceEvent]) -> list[dict]:
    return [e.data for e in events if e.type == EventType.USAGE]


def _cost_usd(events: list[TraceEvent]) -> float:
    """O custo usado nos relatórios: o do SDK ou, sem ele, o da tabela (ADR-0019)."""
    costs = [u.get("cost_usd") or u.get("price_table_cost_usd") or 0.0 for u in _usage(events)]
    return round(sum(costs), 8)


def _tokens_by_model(events: list[TraceEvent]) -> dict[str, Tokens]:
    tokens: dict[str, Tokens] = {}
    for u in _usage(events):
        used = Tokens(u["input_tokens"], u["output_tokens"], u["cache_read_tokens"], u["cache_write_tokens"])
        tokens[u["model"]] = tokens.get(u["model"], Tokens()) + used
    return tokens
