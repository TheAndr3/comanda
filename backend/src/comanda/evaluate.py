"""`comanda eval`: roda cenários com o cliente simulado e o juiz e grava o relatório da rodada.

Adaptador fino sobre `run_scenario` e o relatório (ADR-0015, ADR-0023). Cada rodada ganha um
diretório próprio em `runs/eval/`, com um banco recriado pelo seed por atendimento e os
Rastros, para nunca tocar no banco de uso; o relatório vai para `evals/results/`, versionado
com data, modelos e commit. Sem `--subset` roda todos os cenários, e os marcados com `pass3`
mais duas vezes; `--subset` sem ids roda `DEFAULT_SUBSET`, e com ids roda os cenários com
esses ids ou desses clientes, uma vez cada.
"""

from __future__ import annotations

import subprocess
import sys
from collections.abc import Callable, Sequence
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

from .config import REPO_ROOT, Settings
from .evals import Judge, Provider, ScenarioResult, run_scenario
from .evals.report import (
    PASS_K,
    Metrics,
    QuotaUse,
    build_report,
    compute_metrics,
    summarize_quota,
    write_report,
)
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


def current_commit(root: Path = REPO_ROOT) -> str:
    """O commit do repositório, com `-dirty` se há mudança em arquivo rastreado; `unknown` sem git.

    Arquivo não rastreado não conta: o relatório de uma rodada ainda não commitado deixaria
    todos os relatórios seguintes marcados como `-dirty` sem motivo.
    """
    git = ["git", "-C", str(root)]
    try:
        sha = subprocess.run(
            [*git, "rev-parse", "--short", "HEAD"], capture_output=True, text=True, check=True
        )
        status = subprocess.run(
            [*git, "status", "--porcelain", "--untracked-files=no"],
            capture_output=True,
            text=True,
            check=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return "unknown"
    return sha.stdout.strip() + ("-dirty" if status.stdout.strip() else "")


def _models(settings: Settings) -> dict[str, str]:
    return {
        "reception": settings.reception_model,
        "specialist": settings.specialist_model,
        "simulated_customer": f"{settings.eval_provider}:{settings.gemini_model}",
        "judge": f"{settings.eval_provider}:{settings.judge_model}",
    }


def _print_result(result: ScenarioResult) -> None:
    verdict = "PASSOU" if result.passed else "REPROVOU"
    if result.inconclusive:
        verdict = "SEM VEREDITO (falha do juiz)"
    print(f"  {verdict} em {result.customer_turns} turnos, {result.duration_s:.1f} s")
    if result.judge_error:
        print(f"    - O juiz falhou: {result.judge_error}")
    for mismatch in result.mismatches:
        print(f"    - {mismatch}")
    for v in result.violations:
        print(f"    - Violação {v.severity}, {v.kind}: {v.detail}")
    if result.error:
        print(f"    - A conversa foi interrompida: {result.error}")
    elif not result.ended_by_customer:
        print("    - O cliente simulado não encerrou a conversa antes do limite de turnos.")
    print(f"    Rastro: {result.trace_path}")


def _print_summary(metrics: Metrics, quota: dict[str, QuotaUse]) -> None:
    print(f"\n{metrics.passed} de {metrics.scenarios} cenários passaram.")
    if metrics.judge_errors:
        left_out = ", ".join(metrics.unjudged_scenarios) or "nenhum cenário inteiro"
        print(
            f"Falha do juiz em {metrics.judge_errors} atendimento(s); cenários sem veredito: {left_out}. "
            "Ficam fora da taxa de aprovação, da resolução sem humano, das violações e do pass^3."
        )
    print(f"Resolução sem humano: {metrics.resolved_without_human_rate:.0%}")
    print(f"Violações: {metrics.grave_violations} graves, {metrics.minor_violations} leves")
    print(f"Custo equivalente em API por atendimento (estimativa): US$ {metrics.mean_cost_usd:.4f}")
    print(
        f"Latência por atendimento: {metrics.mean_latency_s:.1f} s "
        f"({metrics.mean_turn_latency_s:.1f} s por fala)"
    )
    if metrics.pass3 is None:
        print("pass^3: não medido nesta rodada (nenhum cenário repetido).")
    else:
        print(f"pass^3: {metrics.pass3:.0%} em {metrics.pass3_scenarios} cenários")
    if not quota:
        print("Cota da assinatura: o runtime não informou nenhuma leitura nesta rodada.")
    for window, use in quota.items():
        if use.consumed is None or use.first is None or use.last is None:
            print(f"Cota {window}: status {use.status}, sem a fração consumida.")
            continue
        renewed = ", a janela renovou no meio" if use.renewed else ""
        print(
            f"Cota {window}: {use.consumed:.0%} consumidos "
            f"(de {use.first:.0%} a {use.last:.0%}{renewed}), status {use.status}"
        )


async def run_eval(
    settings: Settings,
    subset: Sequence[str] | None,
    runtime_factory: RuntimeFactory,
    provider: Provider,
    *,
    judge: Judge,
    commit: str | None = None,
) -> int:
    try:
        scenarios = select_scenarios(load_scenarios(settings.scenarios_dir), subset)
    except ValueError as exc:  # inclui ScenarioError
        print(str(exc), file=sys.stderr)
        return 1
    if not scenarios:
        print(f"Nenhum cenário em {settings.scenarios_dir}.", file=sys.stderr)
        return 1

    # A rodada completa repete os cenários do pass^3; o subconjunto é para iterar barato.
    plan = [(s, 1) for s in scenarios]
    if subset is None:
        plan += [(s, trial) for trial in range(2, PASS_K + 1) for s in scenarios if s.pass3]

    created_at = datetime.now(UTC)
    commit = commit or current_commit()
    run_dir = settings.runs_dir / "eval" / created_at.strftime("%Y%m%dT%H%M%SZ")
    print(
        f"Avaliação de {len(scenarios)} cenários em {len(plan)} atendimentos; bancos e Rastros em {run_dir}"
    )
    results: list[ScenarioResult] = []
    for n, (scenario, trial) in enumerate(plan, start=1):
        repetition = f", repetição {trial}" if trial > 1 else ""
        print(f"\n[{n}/{len(plan)}] {scenario.id} ({scenario.customer_id}{repetition})", flush=True)
        name = scenario.id if trial == 1 else f"{scenario.id}-{trial}"
        scenario_settings = replace(settings, db_path=run_dir / f"{name}.db", runs_dir=run_dir)
        result = await run_scenario(
            scenario,
            runtime=runtime_factory(scenario),
            provider=provider,
            settings=scenario_settings,
            judge=judge,
            trial=trial,
        )
        _print_result(result)
        results.append(result)

    report = build_report(
        results, created_at=created_at, commit=commit, models=_models(settings), subset=subset
    )
    path = write_report(report, settings.results_dir)
    _print_summary(compute_metrics(results), summarize_quota(results))
    print(f"Relatório: {path}")
    return 0
