"""Regras dos prompts que a rodada final mostrou faltando (evals/results/20261116T214239Z-164804e.json).

Os prompts são texto, então estes testes só garantem que cada regra está escrita; quem mede se o
modelo a segue é a avaliação.
"""

from comanda.agents import RECEPTION_PROMPT
from comanda.config import Settings
from comanda.evals.scenario import load_scenarios
from comanda.evals.simulated_customer import system_prompt


def _flat(text: str) -> str:
    return " ".join(text.split())


def test_reception_tells_wrong_item_from_missing_item():
    prompt = _flat(RECEPTION_PROMPT)

    assert "wrong_item quando veio outro item no lugar" in prompt
    assert "missing_item quando o item não veio e nada veio no lugar" in prompt


def test_reception_checks_a_late_order_before_escalating_its_cancellation():
    prompt = _flat(RECEPTION_PROMPT)

    assert "cancelar um pedido muito atrasado ou que não chegou" in prompt
    assert "trate como not_delivered e consulte a elegibilidade antes" in prompt


def test_reception_escalates_mandatory_cases_without_asking():
    assert "escalone direto, sem perguntar se pode" in _flat(RECEPTION_PROMPT)


def test_reception_finds_the_order_before_escalating_an_allergic_reaction():
    prompt = _flat(RECEPTION_PROMPT)

    assert "reação alérgica ou risco à saúde, peça antes a Pedidos o pedido mais recente" in prompt
    assert "escalone com esse order_id" in prompt


def test_simulated_customer_confirms_a_refund_that_meets_the_goal():
    [scenario] = [s for s in load_scenarios(Settings().scenarios_dir) if s.id == "C002-missing-soda"]

    prompt = _flat(system_prompt(scenario, "Bruno"))

    assert "perguntar se pode registrar algo que atende ao seu objetivo, responda que sim" in prompt
    assert "só está atendido depois que o atendimento confirmar que registrou" in prompt
