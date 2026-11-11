"""Cenário de avaliação, lido de YAML em `evals/scenarios/` (ADR-0011, ADR-0022).

Cada cenário declara o Cliente (já autenticado, ADR-0009), o objetivo e a personalidade do
cliente simulado, se é adversarial, se entra no pass^3 e o estado final esperado do banco: os reembolsos que o
atendimento deve registrar e os tickets que deve abrir.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from ..domain.models import IssueType, RefundStatus


class ScenarioError(ValueError):
    """O arquivo de cenário está mal formado."""


@dataclass(frozen=True)
class ExpectedRefund:
    order_id: str
    issue: IssueType
    status: RefundStatus
    amount_cents: int
    rule_id: str | None = None


@dataclass(frozen=True)
class ExpectedTicket:
    order_id: str | None = None
    priority: str | None = None


@dataclass(frozen=True)
class Scenario:
    id: str
    customer_id: str
    goal: str
    personality: str
    refunds: tuple[ExpectedRefund, ...]
    tickets: tuple[ExpectedTicket, ...]
    adversarial: bool = False
    # Repetido três vezes na rodada completa para medir o pass^3 (ADR-0023).
    pass3: bool = False


def _require(data: dict[str, Any], key: str, where: str) -> Any:
    if key not in data or data[key] in (None, ""):
        raise ScenarioError(f"{where}: falta o campo `{key}`.")
    return data[key]


def _refund(data: dict[str, Any], where: str) -> ExpectedRefund:
    amount = _require(data, "amount_cents", where)
    if not isinstance(amount, int) or isinstance(amount, bool) or amount <= 0:
        raise ScenarioError(f"{where}: `amount_cents` deve ser um inteiro positivo em centavos.")
    try:
        issue = IssueType(_require(data, "issue", where))
        status = RefundStatus(_require(data, "status", where))
    except ValueError as exc:
        raise ScenarioError(f"{where}: {exc}") from exc
    return ExpectedRefund(
        order_id=str(_require(data, "order", where)),
        issue=issue,
        status=status,
        amount_cents=amount,
        rule_id=data.get("rule"),
    )


def _ticket(data: dict[str, Any], where: str) -> ExpectedTicket:
    priority = data.get("priority")
    if priority not in (None, "normal", "alta"):
        raise ScenarioError(f"{where}: `priority` deve ser 'normal' ou 'alta'.")
    return ExpectedTicket(order_id=data.get("order"), priority=priority)


def load_scenario(path: Path) -> Scenario:
    where = path.name
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ScenarioError(f"{where}: YAML inválido: {exc}") from exc
    if not isinstance(data, dict):
        raise ScenarioError(f"{where}: o cenário deve ser um mapa YAML.")
    expected = data.get("expected") or {}
    return Scenario(
        id=str(_require(data, "id", where)),
        customer_id=str(_require(data, "customer", where)),
        goal=str(_require(data, "goal", where)).strip(),
        personality=str(_require(data, "personality", where)).strip(),
        refunds=tuple(
            _refund(r, f"{where} reembolso {n}") for n, r in enumerate(expected.get("refunds") or [], 1)
        ),
        tickets=tuple(
            _ticket(t or {}, f"{where} ticket {n}") for n, t in enumerate(expected.get("tickets") or [], 1)
        ),
        adversarial=bool(data.get("adversarial", False)),
        pass3=bool(data.get("pass3", False)),
    )


def load_scenarios(directory: Path) -> list[Scenario]:
    """Todos os cenários do diretório, em ordem de id; ids repetidos são erro."""
    scenarios = sorted((load_scenario(p) for p in directory.glob("*.yaml")), key=lambda s: s.id)
    ids = [s.id for s in scenarios]
    if repeated := sorted({i for i in ids if ids.count(i) > 1}):
        raise ScenarioError(f"Cenários com id repetido: {', '.join(repeated)}.")
    return scenarios
