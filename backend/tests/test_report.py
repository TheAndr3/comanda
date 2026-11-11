"""Métricas da rodada de avaliação (costura 3 do PRD) a partir de resultados roteirizados."""

import json
from datetime import UTC, datetime
from pathlib import Path

from comanda.evals import ScenarioResult, Violation
from comanda.evals.report import build_report, compute_metrics, summarize_quota, write_report
from comanda.runtime import QuotaReading


def result(
    scenario_id: str,
    *,
    passed: bool = True,
    needed_human: bool = False,
    trial: int = 1,
    violations: tuple[Violation, ...] = (),
    cost_usd: float = 0.0,
    response_times_s: tuple[float, ...] = (),
    quota: tuple[QuotaReading, ...] = (),
    judge_error: str | None = None,
    error: str | None = None,
    ignored_judge_kinds: tuple[str, ...] = (),
) -> ScenarioResult:
    return ScenarioResult(
        scenario_id=scenario_id,
        customer_id="C002",
        session_id=f"S-{scenario_id}-{trial}",
        trace_path=Path(f"{scenario_id}-{trial}.jsonl"),
        mismatches=[] if passed else ["Faltou o reembolso esperado."],
        needed_human=needed_human,
        trial=trial,
        violations=list(violations),
        cost_usd=cost_usd,
        response_times_s=list(response_times_s),
        quota=list(quota),
        judge_error=judge_error,
        error=error,
        ignored_judge_kinds=list(ignored_judge_kinds),
    )


def test_pass3_counts_only_scenarios_that_passed_all_three_trials():
    results = [
        *(result("A", trial=t) for t in (1, 2, 3)),
        result("B", trial=1),
        result("B", trial=2, passed=False),
        result("B", trial=3),
        result("C"),
    ]

    metrics = compute_metrics(results)

    assert metrics.pass3_scenarios == 2
    assert metrics.pass3 == 0.5


def test_pass3_is_absent_when_no_scenario_ran_three_times():
    assert compute_metrics([result("A"), result("B")]).pass3 is None


def test_resolution_without_human_counts_passed_scenarios_that_no_human_touched():
    results = [
        result("A"),
        result("B", needed_human=True),
        result("C", passed=False),
        result("D"),
        # Repetições do pass^3 não entram na taxa: só o primeiro atendimento de cada cenário.
        result("A", trial=2, passed=False),
    ]

    metrics = compute_metrics(results)

    assert metrics.scenarios == 4
    assert metrics.passed == 3
    assert metrics.resolved_without_human_rate == 0.5


def test_violations_cost_and_latency_per_attendance():
    grave = Violation("amount_outside_policy", "grave", "judge", "R$ 50,00")
    minor = Violation("guardrail_blocked", "leve", "trace", "eligibility_check")
    results = [
        result("A", passed=False, violations=(grave, minor), cost_usd=0.03, response_times_s=(2.0, 4.0)),
        result("B", violations=(minor,), cost_usd=0.01, response_times_s=(3.0,)),
    ]

    metrics = compute_metrics(results)

    assert (metrics.grave_violations, metrics.minor_violations) == (1, 2)
    assert metrics.violations_by_kind == {"amount_outside_policy": 1, "guardrail_blocked": 2}
    assert metrics.mean_cost_usd == 0.02
    assert metrics.mean_latency_s == 4.5
    assert metrics.mean_turn_latency_s == 3.0


def test_quota_consumed_is_summed_per_window_period_when_the_window_renews_mid_run():
    results = [
        result(
            "A",
            quota=(
                QuotaReading("five_hour", "allowed", 0.10, 1000),
                QuotaReading("five_hour", "allowed", 0.40, 1000),
            ),
        ),
        # A janela renovou (outro `resets_at`) e recomeçou perto de zero.
        result(
            "B",
            quota=(
                QuotaReading("five_hour", "allowed", 0.02, 2000),
                QuotaReading("five_hour", "allowed", 0.12, 2000),
            ),
        ),
    ]

    use = summarize_quota(results)["five_hour"]

    assert use.consumed == 0.4  # 0,30 no primeiro período + 0,10 no segundo
    assert (use.first, use.last, use.renewed, use.readings) == (0.10, 0.12, True, 4)


def test_quota_without_a_utilization_fraction_reports_none_instead_of_a_number():
    results = [result("A", quota=(QuotaReading("seven_day", "allowed_warning"),))]

    use = summarize_quota(results)["seven_day"]

    assert (use.consumed, use.first, use.last, use.status) == (None, None, None, "allowed_warning")


def test_quota_is_empty_when_the_runtime_reported_nothing():
    assert summarize_quota([result("A")]) == {}


def test_report_is_written_under_a_name_that_sorts_by_time_and_names_the_commit(tmp_path):
    created = datetime(2026, 11, 12, 15, 30, 5, tzinfo=UTC)
    models = {"reception": "haiku", "specialist": "sonnet"}

    report = build_report([result("A")], created_at=created, commit="abc1234", models=models, subset=None)
    path = write_report(report, tmp_path / "results")

    assert path.name == "20261112T153005Z-abc1234.json"
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert (saved["commit"], saved["models"], saved["subset"]) == ("abc1234", models, None)
    assert saved["metrics"]["scenarios"] == 1


def test_attendances_the_judge_could_not_read_are_left_out_of_the_rates_and_pass3():
    unjudged = "ProviderError: 429"
    results = [
        result("A"),
        result("B", needed_human=True),
        # C: o 1º atendimento ficou sem veredito, e os outros dois passaram. A taxa usa o 1º
        # atendimento julgado (o 2º), mas o pass^3 não pode contar um trial que ninguém leu.
        result("C", judge_error=unjudged),
        result("C", trial=2),
        result("C", trial=3),
        # D falhou no estado: reprova mesmo que o juiz também tenha falhado.
        result("D", passed=False, judge_error=unjudged),
        # E: todos os atendimentos sem veredito, o cenário fica de fora de tudo.
        result("E", judge_error=unjudged),
    ]

    metrics = compute_metrics(results)

    assert metrics.judge_errors == 3
    assert metrics.unjudged_scenarios == ["C", "E"]
    assert metrics.scenarios == 4  # A, B, C e D
    assert metrics.passed == 3
    assert metrics.failed_scenarios == ["D"]
    assert metrics.resolved_without_human_rate == 0.5  # A e C, de 4
    assert metrics.pass3 is None


def test_a_failed_trial_with_a_verdict_makes_pass3_zero_even_if_another_trial_has_no_verdict():
    results = [
        result("A", judge_error="ProviderError: 429"),  # sem veredito
        result("A", trial=2, passed=False),  # estado errado: reprova com certeza
        result("A", trial=3),
    ]

    metrics = compute_metrics(results)

    assert (metrics.pass3, metrics.pass3_scenarios) == (0.0, 1)
    # O 1º atendimento julgado (o 2º) é o do cenário nas taxas: ele reprovou.
    assert (metrics.scenarios, metrics.passed, metrics.failed_scenarios) == (1, 0, ["A"])


def test_an_unjudged_trial_is_left_out_of_pass3_only_when_no_judged_trial_failed():
    results = [result("A"), result("A", trial=2, judge_error="ProviderError: 429"), result("A", trial=3)]

    assert compute_metrics(results).pass3 is None


def test_violations_count_every_attendance_not_only_the_first():
    grave = Violation("promised_refund", "grave", "judge", "Pode contar com o reembolso.")
    results = [result("A"), result("A", trial=2, passed=False, violations=(grave,)), result("A", trial=3)]

    metrics = compute_metrics(results)

    assert (metrics.grave_violations, metrics.scenarios) == (1, 1)
    assert metrics.violations_by_kind == {"promised_refund": 1}


def test_unjudged_attendances_of_the_repetitions_are_named_too():
    results = [
        result("A"),
        result("A", trial=2, judge_error="ProviderError: 429"),
        result("A", trial=3, judge_error="ProviderError: 429"),
    ]

    assert compute_metrics(results).unjudged_scenarios == ["A (repetição 2)", "A (repetição 3)"]


def test_attendances_that_broke_are_left_out_of_the_cost_and_latency_means():
    results = [
        result("A", cost_usd=0.02, response_times_s=(2.0,)),
        result("B", cost_usd=0.001, response_times_s=(9.0,), error="RuntimeError: caiu"),
    ]

    metrics = compute_metrics(results)

    assert (metrics.mean_cost_usd, metrics.mean_latency_s) == (0.02, 2.0)
    assert metrics.errored_attendances == 1


def test_violation_kinds_the_judge_made_up_are_counted_in_the_metrics_and_the_report(tmp_path):
    results = [
        result("A", ignored_judge_kinds=("tom_rude",)),
        result("B", ignored_judge_kinds=("tom_rude", "gíria")),
    ]

    metrics = compute_metrics(results)
    report = build_report(
        results,
        created_at=datetime(2026, 11, 12, tzinfo=UTC),
        commit="abc1234",
        models={},
        subset=None,
    )

    assert metrics.ignored_judge_kinds == {"gíria": 1, "tom_rude": 2}
    assert report["metrics"]["ignored_judge_kinds"] == {"gíria": 1, "tom_rude": 2}
    assert report["attendances"][1]["ignored_judge_kinds"] == ["tom_rude", "gíria"]
