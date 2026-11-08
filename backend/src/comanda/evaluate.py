"""`comanda eval`: roda cenários com o cliente simulado e imprime o veredito de cada um.

Adaptador fino sobre `run_scenario` (ADR-0015). Cada rodada ganha um diretório próprio em
`runs/eval/`, com um banco recriado pelo seed por cenário e os Rastros dos atendimentos, para
nunca tocar no banco de uso. Sem `--subset` roda todos os cenários; `--subset` sem ids roda
`DEFAULT_SUBSET`, e com ids roda os cenários com esses ids ou desses clientes.
"""

from __future__ import annotations

import sys
from collections.abc import Callable, Sequence
from dataclasses import replace
from datetime import UTC, datetime

from .config import Settings
from .evals import Provider, ScenarioResult, run_scenario
from .evals.scenario import Scenario, load_scenarios
from .runtime import AgentRuntime

# Um caso simples, um reembolso pendente e uma negativa: barato e cobre os três desfechos.
DEFAULT_SUBSET = ("C002-missing-soda", "C003-lost-order", "C006-outside-window")

RuntimeFactory = Callable[[Scenario], AgentRuntime]


def select_scenarios(scenarios: list[Scenario], subset: Sequence[str] | None) -> list[Scenario]:
    """Os cenários pedidos, por id ou por cliente; `subset` vazio é o `DEFAULT_SUBSET`."""
    if subset is None:
        return scenarios
    wanted = list(subset) or list(DEFAULT_SUBSET)
    unknown = [w for w in wanted if not any(w in (s.id, s.customer_id) for s in scenarios)]
    if unknown:
        raise ValueError(f"Cenário não encontrado: {', '.join(unknown)}.")
    return [s for s in scenarios if s.id in wanted or s.customer_id in wanted]


def _print_result(result: ScenarioResult) -> None:
    verdict = "PASSOU" if result.passed else "REPROVOU"
    print(f"  {verdict} em {result.customer_turns} turnos, {result.duration_s:.1f} s")
    for mismatch in result.mismatches:
        print(f"    - {mismatch}")
    if result.error:
        print(f"    - A conversa foi interrompida: {result.error}")
    elif not result.ended_by_customer:
        print("    - O cliente simulado não encerrou a conversa antes do limite de turnos.")
    print(f"    Rastro: {result.trace_path}")


async def run_eval(
    settings: Settings,
    subset: Sequence[str] | None,
    runtime_factory: RuntimeFactory,
    provider: Provider,
) -> int:
    try:
        scenarios = select_scenarios(load_scenarios(settings.scenarios_dir), subset)
    except ValueError as exc:  # inclui ScenarioError
        print(str(exc), file=sys.stderr)
        return 1
    if not scenarios:
        print(f"Nenhum cenário em {settings.scenarios_dir}.", file=sys.stderr)
        return 1

    run_dir = settings.runs_dir / "eval" / datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    print(f"Avaliação de {len(scenarios)} cenários; bancos e Rastros em {run_dir}")
    results: list[ScenarioResult] = []
    for n, scenario in enumerate(scenarios, start=1):
        print(f"\n[{n}/{len(scenarios)}] {scenario.id} ({scenario.customer_id})", flush=True)
        scenario_settings = replace(settings, db_path=run_dir / f"{scenario.id}.db", runs_dir=run_dir)
        result = await run_scenario(
            scenario,
            runtime=runtime_factory(scenario),
            provider=provider,
            settings=scenario_settings,
        )
        _print_result(result)
        results.append(result)

    passed = sum(r.passed for r in results)
    print(f"\n{passed} de {len(results)} cenários passaram.")
    return 0
