"""Testes da avaliação (costura 3 do PRD): cenário com runtime e provedor roteirizados.

Nenhum teste chama modelo: o cliente simulado fala pelo provedor roteirizado e os agentes
seguem o runtime roteirizado. O estado final do banco é conferido por código (ADR-0011).
"""

import json
from dataclasses import replace
from pathlib import Path

from comanda.cli import main
from comanda.evals import Judge, ScriptedProvider, load_scenario, load_scenarios, run_scenario
from comanda.evals.run import Tokens
from comanda.evaluate import run_eval
from comanda.runtime import Delegate, QuotaReading, Return, Say, ScriptedRuntime, ToolCall, Usage

MISSING_SODA = {"order_id": "O-10003", "issue": "missing_item", "item_ids": ["O-10003-3"]}

SCENARIO_YAML = """\
id: C002-missing-soda
customer: C002
goal: Seu pedido mais recente chegou sem o refrigerante; você quer o reembolso dele.
personality: Educado e direto.
expected:
  refunds:
    - order: O-10003
      issue: missing_item
      status: approved
      amount_cents: 690
"""


def scenario(tmp_path: Path):
    path = tmp_path / "C002-missing-soda.yaml"
    path.write_text(SCENARIO_YAML, encoding="utf-8")
    return load_scenario(path)


def customer_says(*messages: str) -> ScriptedProvider:
    return ScriptedProvider([*messages, "[FIM]"])


def refunding_runtime() -> ScriptedRuntime:
    return ScriptedRuntime(
        [
            [
                Delegate("refunds", "Ver a elegibilidade do refrigerante faltando no O-10003."),
                ToolCall("refunds", "check_refund_eligibility", MISSING_SODA),
                Return("refunds", "Elegível, R$ 6,90."),
                Say("Você tem direito a R$ 6,90 pelo refrigerante. Posso registrar?"),
            ],
            [
                Delegate("refunds", "Registrar o reembolso do refrigerante."),
                ToolCall("refunds", "issue_refund", MISSING_SODA),
                Return("refunds", "Reembolso aprovado."),
                Say("Pronto! Os R$ 6,90 voltam para o meio de pagamento."),
            ],
        ]
    )


async def test_scenario_passes_when_final_state_matches(tmp_path, settings):
    result = await run_scenario(
        scenario(tmp_path),
        runtime=refunding_runtime(),
        provider=customer_says("Meu pedido veio sem o refrigerante.", "Pode sim."),
        settings=settings,
    )

    assert result.passed, result.mismatches
    assert result.mismatches == []
    assert [m.text for m in result.transcript if m.role == "customer"] == [
        "Meu pedido veio sem o refrigerante.",
        "Pode sim.",
    ]


def forgetful_runtime() -> ScriptedRuntime:
    """O agente consulta a elegibilidade e promete, mas nunca registra o reembolso."""
    return ScriptedRuntime(
        [
            [
                ToolCall("refunds", "check_refund_eligibility", MISSING_SODA),
                Say("Você tem direito a R$ 6,90 pelo refrigerante. Posso registrar?"),
            ],
            [Say("Pronto! Os R$ 6,90 voltam para o meio de pagamento.")],
        ]
    )


async def test_scenario_fails_when_final_state_differs(tmp_path, settings):
    result = await run_scenario(
        scenario(tmp_path),
        runtime=forgetful_runtime(),
        provider=customer_says("Meu pedido veio sem o refrigerante.", "Pode sim."),
        settings=settings,
    )

    assert not result.passed
    assert result.mismatches == [
        "Faltou o reembolso esperado: pedido O-10003, missing_item, approved, R$ 6,90."
    ]


def judge_says(*violations: tuple[str, str]) -> Judge:
    """Um juiz roteirizado que aponta estas violações: (tipo, trecho da fala)."""
    found = [{"kind": kind, "evidence": evidence} for kind, evidence in violations]
    return Judge(ScriptedProvider([json.dumps({"violations": found}, ensure_ascii=False)]))


def kinds(result) -> list[tuple[str, str]]:
    return [(v.kind, v.severity) for v in result.violations]


async def test_judge_answer_out_of_format_fails_only_that_scenario(tmp_path, settings):
    result = await run_scenario(
        scenario(tmp_path),
        runtime=refunding_runtime(),
        provider=customer_says("Meu pedido veio sem o refrigerante.", "Pode sim."),
        settings=settings,
        judge=Judge(ScriptedProvider(["Achei tudo certo, sem violações."])),
    )

    assert result.mismatches == []
    assert not result.passed
    assert result.error is not None and result.error.startswith("Juiz:")


async def test_grave_violation_fails_scenario_even_with_right_state(tmp_path, settings):
    result = await run_scenario(
        scenario(tmp_path),
        runtime=refunding_runtime(),
        provider=customer_says("Meu pedido veio sem o refrigerante.", "Pode sim."),
        settings=settings,
        judge=judge_says(("deadline_outside_policy", "Os R$ 6,90 caem na sua conta em uma hora.")),
    )

    assert result.mismatches == []
    assert kinds(result) == [("deadline_outside_policy", "grave")]
    assert not result.passed


def hasty_runtime() -> ScriptedRuntime:
    """O agente tenta registrar sem consultar a elegibilidade, é bloqueado e corrige."""
    return ScriptedRuntime(
        [
            [
                Delegate("refunds", "Registrar o reembolso do refrigerante."),
                ToolCall("refunds", "issue_refund", MISSING_SODA),
                ToolCall("refunds", "check_refund_eligibility", MISSING_SODA),
                ToolCall("refunds", "issue_refund", MISSING_SODA),
                Return("refunds", "Reembolso aprovado."),
                Say("Pronto! Os R$ 6,90 voltam para o meio de pagamento."),
            ]
        ]
    )


async def test_corrected_guardrail_block_passes_and_counts_as_minor(tmp_path, settings):
    result = await run_scenario(
        scenario(tmp_path),
        runtime=hasty_runtime(),
        provider=customer_says("Meu pedido veio sem o refrigerante."),
        settings=settings,
        judge=judge_says(),
    )

    assert result.passed, (result.mismatches, result.violations)
    assert kinds(result) == [("guardrail_blocked", "leve")]


async def test_result_carries_cost_tokens_and_response_times(tmp_path, settings):
    runtime = ScriptedRuntime(
        [
            [
                Usage("reception", "claude-haiku-5-5", 1000, 100, cost_usd=0.002),
                Usage("refunds", "claude-sonnet-5-5", 2000, 200, cost_usd=0.01),
                Say("Você tem direito a R$ 6,90 pelo refrigerante. Posso registrar?"),
            ],
            [
                Usage("reception", "claude-haiku-5-5", 500, 50, cost_usd=0.001),
                Say("Certo, então não registro nada."),
            ],
        ]
    )

    result = await run_scenario(
        scenario(tmp_path),
        runtime=runtime,
        provider=customer_says("Meu pedido veio sem o refrigerante.", "Deixa pra lá."),
        settings=settings,
    )

    assert result.cost_usd == 0.013
    assert result.tokens_by_model == {
        "claude-haiku-5-5": Tokens(1500, 150),
        "claude-sonnet-5-5": Tokens(2000, 200),
    }
    assert len(result.response_times_s) == 2
    assert not result.needed_human


async def test_escalated_attendance_needed_a_human(tmp_path, settings):
    runtime = ScriptedRuntime(
        [
            [
                ToolCall("reception", "escalate_to_human", {"summary": "Cliente quer falar com humano."}),
                Say("Passei o seu caso a um atendente."),
            ]
        ]
    )

    result = await run_scenario(
        scenario(tmp_path),
        runtime=runtime,
        provider=customer_says("Quero falar com uma pessoa."),
        settings=settings,
    )

    assert result.needed_human


def test_versioned_scenarios_cover_the_seed_customers(repo, settings):
    scenarios = load_scenarios(settings.scenarios_dir)

    assert {s.customer_id for s in scenarios} >= {f"C00{n}" for n in range(1, 9)}
    for s in scenarios:
        assert repo.get_customer(s.customer_id) is not None, s.id
        for refund in s.refunds:
            order = repo.get_order(refund.order_id)
            assert order is not None and order.customer_id == s.customer_id, s.id


def no_violations(attendances: int) -> Judge:
    return Judge(ScriptedProvider(['{"violations": []}'] * attendances))


def write_scenarios(directory: Path, *ids: str, pass3: tuple[str, ...] = ()) -> None:
    directory.mkdir()
    for scenario_id in ids:
        text = SCENARIO_YAML.replace("id: C002-missing-soda", f"id: {scenario_id}")
        if scenario_id in pass3:
            text += "pass3: true\n"
        (directory / f"{scenario_id}.yaml").write_text(text, encoding="utf-8")


async def test_eval_prints_the_verdict_of_each_scenario(tmp_path, settings, capsys):
    write_scenarios(tmp_path / "scenarios", "C002-forgotten", "C002-refunded")
    runtimes = {"C002-forgotten": forgetful_runtime, "C002-refunded": refunding_runtime}
    conversation = ["Meu pedido veio sem o refrigerante.", "Pode sim.", "[FIM]"]

    status = await run_eval(
        replace(settings, scenarios_dir=tmp_path / "scenarios", results_dir=tmp_path / "results"),
        subset=None,
        runtime_factory=lambda s: runtimes[s.id](),
        provider=ScriptedProvider(conversation * 2),
        judge=no_violations(2),
    )

    out = capsys.readouterr().out
    assert status == 0
    forgotten = out.index("C002-forgotten")
    refunded = out.index("C002-refunded")
    assert "REPROVOU" in out[forgotten:refunded]
    assert "Faltou o reembolso esperado: pedido O-10003" in out[forgotten:refunded]
    assert "PASSOU" in out[refunded:]
    assert "1 de 2 cenários passaram" in out


def quota_runtime(utilization: float) -> ScriptedRuntime:
    """Resolve o caso em um turno e informa a cota da assinatura, como o SDK faria."""
    return ScriptedRuntime(
        [
            [
                QuotaReading("five_hour", "allowed", utilization, resets_at=1_790_000_000),
                ToolCall("refunds", "check_refund_eligibility", MISSING_SODA),
                ToolCall("refunds", "issue_refund", MISSING_SODA),
                Usage("reception", "claude-haiku-5-5", 1000, 100, cost_usd=0.002),
                Say("Pronto! Os R$ 6,90 voltam para o meio de pagamento."),
            ]
        ]
    )


async def test_eval_writes_a_versioned_report_with_metrics_and_quota(tmp_path, settings, capsys):
    write_scenarios(tmp_path / "scenarios", "C002-once", "C002-thrice", pass3=("C002-thrice",))
    readings = iter([0.10, 0.12, 0.18, 0.25])

    status = await run_eval(
        replace(settings, scenarios_dir=tmp_path / "scenarios", results_dir=tmp_path / "results"),
        subset=None,
        runtime_factory=lambda _: quota_runtime(next(readings)),
        provider=ScriptedProvider(["Meu pedido veio sem o refrigerante.", "[FIM]"] * 4),
        judge=no_violations(4),
        commit="abc1234",
    )

    assert status == 0
    [path] = (tmp_path / "results").glob("*.json")
    assert path.name.endswith("-abc1234.json")
    report = json.loads(path.read_text(encoding="utf-8"))
    assert report["commit"] == "abc1234"
    assert report["models"]["reception"] == settings.reception_model
    assert report["models"]["judge"].endswith(settings.judge_model)
    assert [(a["scenario_id"], a["trial"]) for a in report["attendances"]] == [
        ("C002-once", 1),
        ("C002-thrice", 1),
        ("C002-thrice", 2),
        ("C002-thrice", 3),
    ]
    metrics = report["metrics"]
    assert (metrics["scenarios"], metrics["attendances"], metrics["passed"]) == (2, 4, 2)
    assert metrics["pass3"] == 1.0
    assert metrics["resolved_without_human_rate"] == 1.0
    assert metrics["mean_cost_usd"] == 0.002
    assert report["quota"]["five_hour"]["consumed"] == 0.15

    out = capsys.readouterr().out
    assert "2 de 2 cenários passaram" in out
    assert "pass^3: 100%" in out
    assert "Cota five_hour: 15% consumidos" in out
    assert str(path) in out


def test_eval_without_gemini_key_explains_how_to_set_it(monkeypatch, capsys):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setenv("COMANDA_EVAL_PROVIDER", "gemini")

    assert main(["eval", "--subset", "C002"]) == 1
    assert "GEMINI_API_KEY" in capsys.readouterr().err
