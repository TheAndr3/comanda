"""Rodada de um cenário: cliente simulado conversando com o Atendimento (costura 3 do PRD).

Cada cenário parte de um banco recém-criado pelo seed, conversa pela interface do
Atendimento até o cliente simulado encerrar ou acabarem os turnos, e confere o estado
final do banco por código. O juiz da fala entra depois, sobre o mesmo resultado.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path

from ..config import Settings
from ..data.seed import seed_database
from ..runtime import AgentRuntime, start_session
from .providers import Provider
from .scenario import Scenario
from .simulated_customer import NO_REPLY, SimulatedCustomer, TranscriptLine
from .state import check_final_state, take_baseline

MAX_CUSTOMER_TURNS = 12


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
    duration_s: float = 0.0

    @property
    def passed(self) -> bool:
        return self.error is None and not self.mismatches

    @property
    def customer_turns(self) -> int:
        return sum(1 for line in self.transcript if line.role == "customer")


async def run_scenario(
    scenario: Scenario,
    *,
    runtime: AgentRuntime,
    provider: Provider,
    settings: Settings,
    max_turns: int = MAX_CUSTOMER_TURNS,
) -> ScenarioResult:
    """Roda um cenário. Recria o banco em `settings.db_path`: nunca aponte para o banco de uso."""
    repo = seed_database(settings.db_path)
    baseline = take_baseline(repo)
    customer = repo.get_customer(scenario.customer_id)
    if customer is None:
        raise ValueError(
            f"O cenário {scenario.id} referencia o cliente {scenario.customer_id}, que não existe."
        )
    simulated = SimulatedCustomer(scenario, customer.name.split()[0], provider)
    session = start_session(repo, settings, scenario.customer_id, runtime)

    transcript: list[TranscriptLine] = []
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
            turn = await session.send(message)
            transcript.append(TranscriptLine("system", "\n\n".join(turn.replies) or NO_REPLY))
    except Exception as exc:
        # Uma falha num cenário reprova esse cenário, sem derrubar a rodada inteira.
        error = f"{type(exc).__name__}: {exc}"
    finally:
        try:
            session.close()  # primeiro: o Rastro registra o fim mesmo que o runtime falhe ao fechar
        finally:
            await runtime.aclose()

    return ScenarioResult(
        scenario_id=scenario.id,
        customer_id=scenario.customer_id,
        session_id=session.id,
        trace_path=session.trace_path,
        mismatches=check_final_state(repo, scenario, baseline),
        transcript=transcript,
        ended_by_customer=ended_by_customer,
        error=error,
        duration_s=time.monotonic() - started,
    )
