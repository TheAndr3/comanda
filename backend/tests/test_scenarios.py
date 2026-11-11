"""Os 30 cenários versionados em `evals/scenarios/` contra o seed e as regras de reembolso.

Nenhum teste aqui chama modelo: o estado final esperado de cada cenário é conferido contra
`evaluate_refund` aplicado ao seed, para que um valor em centavos nunca seja calculado de cabeça.
"""

from __future__ import annotations

from datetime import timedelta
from itertools import chain, combinations, permutations

import pytest

from comanda.data.seed import seed_database
from comanda.domain.models import IssueType, Refund, RefundStatus
from comanda.domain.policies import FREQUENT_REFUND_WINDOW, evaluate_refund
from comanda.evals.scenario import ExpectedRefund, Scenario, load_scenarios

SCENARIO_COUNT = 30
ADVERSARIAL_COUNT = 8
PASS3_COUNT = 10  # o #9 mede pass^3 em 10 cenários


@pytest.fixture
def scenarios(settings) -> list[Scenario]:
    return load_scenarios(settings.scenarios_dir)


def test_there_are_thirty_scenarios_one_customer_each(scenarios):
    assert len(scenarios) == SCENARIO_COUNT
    assert [s.customer_id for s in scenarios] == [f"C{n:03d}" for n in range(1, SCENARIO_COUNT + 1)]
    assert [s.id.split("-")[0] for s in scenarios] == [s.customer_id for s in scenarios]


def test_file_names_match_scenario_ids(settings, scenarios):
    assert sorted(p.stem for p in settings.scenarios_dir.glob("*.yaml")) == [s.id for s in scenarios]


def test_eight_scenarios_are_adversarial_and_ten_repeat_for_pass3(scenarios):
    assert sum(s.adversarial for s in scenarios) == ADVERSARIAL_COUNT
    assert sum(s.pass3 for s in scenarios) == PASS3_COUNT


def test_every_scenario_references_an_existing_customer_and_their_orders(repo, scenarios):
    for s in scenarios:
        assert repo.get_customer(s.customer_id) is not None, s.id
        orders = [r.order_id for r in s.refunds] + [t.order_id for t in s.tickets if t.order_id]
        for order_id in orders:
            order = repo.get_order(order_id)
            assert order is not None, f"{s.id}: o pedido {order_id} não existe no seed"
            assert order.customer_id == s.customer_id, f"{s.id}: o pedido {order_id} é de outro cliente"


def test_expected_refunds_are_not_repeated_within_a_scenario(scenarios):
    for s in scenarios:
        keys = [(r.order_id, r.issue) for r in s.refunds]
        assert len(keys) == len(set(keys)), s.id


def test_expected_refunds_cover_every_issue_type(scenarios):
    covered = {r.issue for s in scenarios for r in s.refunds}
    assert covered == set(IssueType)


def _subsets(item_ids: list[str]) -> list[list[str]]:
    return [
        list(c) for c in chain.from_iterable(combinations(item_ids, n) for n in range(1, len(item_ids) + 1))
    ]


def _reachable(repo, settings, customer_id: str, expected: list[ExpectedRefund]) -> bool:
    """Existe uma escolha de itens em que `evaluate_refund` dá exatamente o esperado, na ordem dada?

    Cada reembolso já registrado entra no cálculo do seguinte, como no atendimento real.
    """
    now = repo.now()
    since = now - FREQUENT_REFUND_WINDOW
    by_order = {r.order_id: repo.refunds_for_order(r.order_id) for r in expected}
    recent = repo.refunds_for_customer(customer_id, since)

    def solve(index: int, by_order: dict[str, list[Refund]], recent: list[Refund]) -> bool:
        if index == len(expected):
            return True
        want = expected[index]
        order = repo.get_order(want.order_id)
        assert order is not None
        needs_items = want.issue in (IssueType.MISSING_ITEM, IssueType.WRONG_ITEM, IssueType.QUALITY)
        choices = _subsets([i.id for i in order.items]) if needs_items else [[]]
        for item_ids in choices:
            decision = evaluate_refund(
                order, want.issue, item_ids, now, by_order[want.order_id], recent,
                settings.auto_refund_limit_cents,
            )  # fmt: skip
            status = RefundStatus.PENDING_APPROVAL if decision.requires_approval else RefundStatus.APPROVED
            if not (
                decision.eligible
                and decision.amount_cents == want.amount_cents
                and status == want.status
                and want.rule_id in (None, decision.rule_id)
            ):
                continue
            registered = Refund(
                id=f"RF-TEST-{index}", order_id=order.id, customer_id=customer_id,
                amount_cents=decision.amount_cents, issue=want.issue, status=status,
                rule_id=decision.rule_id, item_ids=tuple(item_ids), created_at=now,
            )  # fmt: skip
            if solve(
                index + 1,
                {**by_order, order.id: [*by_order[order.id], registered]},
                [*recent, registered],
            ):
                return True
        return False

    return solve(0, by_order, recent)


def test_expected_refunds_match_evaluate_refund_applied_to_the_seed(repo, settings, scenarios):
    for s in scenarios:
        for ordering in permutations(s.refunds):
            assert _reachable(repo, settings, s.customer_id, list(ordering)), (
                f"{s.id}: o estado esperado não bate com evaluate_refund "
                f"na ordem {[(r.order_id, r.issue.value, r.amount_cents) for r in ordering]}"
            )


def test_expected_tickets_have_a_valid_priority(scenarios):
    for s in scenarios:
        for ticket in s.tickets:
            assert ticket.priority in (None, "normal", "alta"), s.id


def test_every_scenario_customer_is_seeded_the_same_way_twice(tmp_path, scenarios):
    a = seed_database(tmp_path / "a.db")
    b = seed_database(tmp_path / "b.db")
    for s in scenarios:
        orders_a, orders_b = a.list_orders(s.customer_id, 10), b.list_orders(s.customer_id, 10)
        assert orders_a, f"{s.id}: o cliente {s.customer_id} não tem pedidos"
        assert orders_a == orders_b, s.id
        assert a.get_customer(s.customer_id) == b.get_customer(s.customer_id), s.id
        assert a.refunds_for_customer(s.customer_id, a.now() - timedelta(days=3650)) == (
            b.refunds_for_customer(s.customer_id, b.now() - timedelta(days=3650))
        ), s.id
