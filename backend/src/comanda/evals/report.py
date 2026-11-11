"""Métricas e relatório de uma rodada de avaliação (ADR-0011, ADR-0014, ADR-0015, ADR-0023).

As taxas e contagens por cenário usam o primeiro atendimento de cada cenário, para que as
repetições do pass^3 não pesem mais que os outros cenários. Custo e latência são médias por
atendimento e usam todos os atendimentos da rodada.
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from .run import ScenarioResult, Tokens

PASS_K = 3


@dataclass(frozen=True)
class Metrics:
    scenarios: int
    attendances: int
    passed: int
    resolved_without_human_rate: float
    grave_violations: int
    minor_violations: int
    violations_by_kind: dict[str, int]
    # Custo equivalente em API por atendimento: estimativa, não gasto real (ADR-0014).
    mean_cost_usd: float
    tokens_by_model: dict[str, Tokens]
    # Tempo de resposta do sistema somado no atendimento, e por fala do cliente.
    mean_latency_s: float
    mean_turn_latency_s: float
    # Fração dos cenários repetidos `PASS_K` vezes que passaram em todas; `None` sem repetição.
    pass3: float | None
    pass3_scenarios: int
    failed_scenarios: list[str] = field(default_factory=list)


def _mean(values: Sequence[float]) -> float:
    return round(sum(values) / len(values), 6) if values else 0.0


def compute_metrics(results: Sequence[ScenarioResult]) -> Metrics:
    first = [r for r in results if r.trial == 1]
    violations = [v for r in first for v in r.violations]

    trials: dict[str, list[ScenarioResult]] = defaultdict(list)
    for r in results:
        trials[r.scenario_id].append(r)
    repeated = [rs for rs in trials.values() if len(rs) >= PASS_K]

    tokens: dict[str, Tokens] = {}
    for r in results:
        for model, t in r.tokens_by_model.items():
            tokens[model] = tokens.get(model, Tokens()) + t

    turn_times = [t for r in results for t in r.response_times_s]
    return Metrics(
        scenarios=len(first),
        attendances=len(results),
        passed=sum(r.passed for r in first),
        resolved_without_human_rate=_mean([float(r.passed and not r.needed_human) for r in first]),
        grave_violations=sum(v.severity == "grave" for v in violations),
        minor_violations=sum(v.severity == "leve" for v in violations),
        violations_by_kind=dict(sorted(Counter(v.kind for v in violations).items())),
        mean_cost_usd=_mean([r.cost_usd for r in results]),
        tokens_by_model=dict(sorted(tokens.items())),
        mean_latency_s=_mean([sum(r.response_times_s) for r in results]),
        mean_turn_latency_s=_mean(turn_times),
        pass3=_mean([float(all(r.passed for r in rs[:PASS_K])) for rs in repeated]) if repeated else None,
        pass3_scenarios=len(repeated),
        failed_scenarios=[r.scenario_id for r in first if not r.passed],
    )


@dataclass(frozen=True)
class QuotaUse:
    """Consumo observado de uma janela de limite da assinatura durante a rodada (ADR-0024).

    `consumed` é a diferença entre a última e a primeira fração lidas, somada por período da
    janela quando ela renova no meio da rodada (`renewed`). Mede a partir da primeira leitura,
    então não conta o que veio antes dela; `None` quando o CLI não informou a fração.
    """

    readings: int
    first: float | None
    last: float | None
    consumed: float | None
    status: str
    renewed: bool


def summarize_quota(results: Sequence[ScenarioResult]) -> dict[str, QuotaUse]:
    by_window: dict[str, list] = defaultdict(list)
    for r in results:
        for reading in r.quota:
            by_window[reading.window].append(reading)

    summary: dict[str, QuotaUse] = {}
    for window, readings in sorted(by_window.items()):
        periods: dict[int | None, list[float]] = defaultdict(list)
        for reading in readings:
            if reading.utilization is not None:
                periods[reading.resets_at].append(reading.utilization)
        values = [u for r in readings if (u := r.utilization) is not None]
        consumed = round(sum(p[-1] - p[0] for p in periods.values()), 4) if values else None
        summary[window] = QuotaUse(
            readings=len(readings),
            first=values[0] if values else None,
            last=values[-1] if values else None,
            consumed=consumed,
            status=readings[-1].status,
            renewed=len({r.resets_at for r in readings}) > 1,
        )
    return summary


def _attendance(r: ScenarioResult) -> dict[str, Any]:
    return {
        "scenario_id": r.scenario_id,
        "trial": r.trial,
        "customer_id": r.customer_id,
        "session_id": r.session_id,
        "passed": r.passed,
        "needed_human": r.needed_human,
        "customer_turns": r.customer_turns,
        "ended_by_customer": r.ended_by_customer,
        "error": r.error,
        "mismatches": r.mismatches,
        "violations": [asdict(v) for v in r.violations],
        "cost_usd": r.cost_usd,
        "tokens_by_model": {m: asdict(t) for m, t in r.tokens_by_model.items()},
        "latency_s": round(sum(r.response_times_s), 3),
        "response_times_s": [round(t, 3) for t in r.response_times_s],
    }


def build_report(
    results: Sequence[ScenarioResult],
    *,
    created_at: datetime,
    commit: str,
    models: dict[str, str],
    subset: Sequence[str] | None,
) -> dict[str, Any]:
    """O relatório da rodada, pronto para gravar em JSON."""
    return {
        "created_at": created_at.isoformat(),
        "commit": commit,
        "models": models,
        "subset": list(subset) if subset is not None else None,
        "metrics": asdict(compute_metrics(results)),
        "cost_note": "Custo equivalente em API pela tabela pública: estimativa, não gasto real (ADR-0014).",
        "quota": {w: asdict(q) for w, q in summarize_quota(results).items()},
        "attendances": [_attendance(r) for r in results],
    }


def write_report(report: dict[str, Any], directory: Path) -> Path:
    """Grava em `<diretório>/<data>-<commit>.json`; o nome ordena as rodadas no tempo."""
    directory.mkdir(parents=True, exist_ok=True)
    stamp = datetime.fromisoformat(report["created_at"]).strftime("%Y%m%dT%H%M%SZ")
    path = directory / f"{stamp}-{report['commit']}.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path
