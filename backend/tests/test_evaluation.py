"""Testes da avaliação (costura 3 do PRD): cenário com runtime e provedor roteirizados.

Nenhum teste chama modelo: o cliente simulado fala pelo provedor roteirizado e os agentes
seguem o runtime roteirizado. O estado final do banco é conferido por código (ADR-0011).
"""

from dataclasses import replace
from pathlib import Path

from comanda.cli import main
from comanda.evals import ScriptedProvider, load_scenario, load_scenarios, run_scenario
from comanda.evaluate import run_eval
from comanda.runtime import Delegate, Return, Say, ScriptedRuntime, ToolCall

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


def test_versioned_scenarios_cover_the_seed_customers(repo, settings):
    scenarios = load_scenarios(settings.scenarios_dir)

    assert {s.customer_id for s in scenarios} >= {f"C00{n}" for n in range(1, 9)}
    for s in scenarios:
        assert repo.get_customer(s.customer_id) is not None, s.id
        for refund in s.refunds:
            order = repo.get_order(refund.order_id)
            assert order is not None and order.customer_id == s.customer_id, s.id


async def test_eval_prints_the_verdict_of_each_scenario(tmp_path, settings, capsys):
    scenarios_dir = tmp_path / "scenarios"
    scenarios_dir.mkdir()
    for scenario_id in ("C002-forgotten", "C002-refunded"):
        text = SCENARIO_YAML.replace("id: C002-missing-soda", f"id: {scenario_id}")
        (scenarios_dir / f"{scenario_id}.yaml").write_text(text, encoding="utf-8")
    runtimes = {"C002-forgotten": forgetful_runtime, "C002-refunded": refunding_runtime}
    conversation = ["Meu pedido veio sem o refrigerante.", "Pode sim.", "[FIM]"]

    status = await run_eval(
        replace(settings, scenarios_dir=scenarios_dir),
        subset=None,
        runtime_factory=lambda s: runtimes[s.id](),
        provider=ScriptedProvider(conversation * 2),
    )

    out = capsys.readouterr().out
    assert status == 0
    forgotten = out.index("C002-forgotten")
    refunded = out.index("C002-refunded")
    assert "REPROVOU" in out[forgotten:refunded]
    assert "Faltou o reembolso esperado: pedido O-10003" in out[forgotten:refunded]
    assert "PASSOU" in out[refunded:]
    assert "1 de 2 cenários passaram" in out


def test_eval_without_gemini_key_explains_how_to_set_it(monkeypatch, capsys):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setenv("COMANDA_EVAL_PROVIDER", "gemini")

    assert main(["eval", "--subset", "C002"]) == 1
    assert "GEMINI_API_KEY" in capsys.readouterr().err
