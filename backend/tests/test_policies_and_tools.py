"""Testes das regras de negócio através das ferramentas, cenário a cenário."""

from comanda.domain.models import RefundStatus
from comanda.knowledge import search_policies
from comanda.tools import run_tool


def latest_order(ctx):
    return run_tool(ctx, "list_my_orders", {"limit": 1})["orders"][0]["order_id"]


def item_id(ctx, order_id, name_part):
    details = run_tool(ctx, "get_order_details", {"order_id": order_id})
    return next(i["item_id"] for i in details["items"] if name_part in i["name"])


def test_seed_is_deterministic(tmp_path):
    from comanda.data.seed import seed_database

    a = seed_database(tmp_path / "a.db")
    b = seed_database(tmp_path / "b.db")
    assert a.list_customer_ids() == b.list_customer_ids()
    assert [o.id for o in a.list_orders("C012", 10)] == [o.id for o in b.list_orders("C012", 10)]


def test_late_75_minutes_gets_fee_plus_25_percent(ctx_for):
    ctx = ctx_for("C001")
    order_id = latest_order(ctx)
    result = run_tool(ctx, "check_refund_eligibility", {"order_id": order_id, "issue": "late_delivery"})
    assert result["eligible"] and result["rule_id"] == "R-LATE-60"
    # Lasanha 42,90 + espaguete 32,90 = 75,80; 25% = 18,95; taxa 6,99 -> 25,94
    assert result["amount_cents"] == 2594
    assert not result["requires_human_approval"]


def test_missing_item_is_refunded_automatically(ctx_for, repo):
    ctx = ctx_for("C002")
    order_id = latest_order(ctx)
    soda = item_id(ctx, order_id, "Refrigerante")
    args = {"order_id": order_id, "issue": "missing_item", "item_ids": [soda]}
    check = run_tool(ctx, "check_refund_eligibility", args)
    assert check["amount_cents"] == 690
    refund = run_tool(ctx, "issue_refund", args)
    assert refund["status"] == RefundStatus.APPROVED
    # O mesmo item não pode ser reembolsado duas vezes.
    again = run_tool(ctx, "issue_refund", args)
    assert "error" in again and "já tiveram reembolso" in again["error"]


def test_lost_order_above_limit_needs_approval(ctx_for):
    ctx = ctx_for("C003")
    order_id = latest_order(ctx)
    result = run_tool(ctx, "issue_refund", {"order_id": order_id, "issue": "not_delivered"})
    assert result["status"] == RefundStatus.PENDING_APPROVAL
    assert result["rule_id"] == "R-LOST"


def test_cancelled_order_full_refund(ctx_for):
    ctx = ctx_for("C004")
    order_id = latest_order(ctx)
    result = run_tool(ctx, "check_refund_eligibility", {"order_id": order_id, "issue": "not_delivered"})
    assert result["rule_id"] == "R-CANCELLED" and result["amount_cents"] == 5490 + 1190 + 699


def test_frequent_refunder_needs_approval(ctx_for):
    ctx = ctx_for("C005")
    order_id = latest_order(ctx)
    brownie = item_id(ctx, order_id, "Brownie")
    result = run_tool(
        ctx,
        "check_refund_eligibility",
        {"order_id": order_id, "issue": "missing_item", "item_ids": [brownie]},
    )
    assert result["eligible"] and result["requires_human_approval"]


def test_claim_after_48h_is_denied(ctx_for):
    ctx = ctx_for("C006")
    order_id = latest_order(ctx)
    result = run_tool(ctx, "check_refund_eligibility", {"order_id": order_id, "issue": "late_delivery"})
    assert not result["eligible"] and result["rule_id"] == "R-WINDOW"


def test_order_in_preparation_is_not_refundable(ctx_for):
    ctx = ctx_for("C007")
    order_id = latest_order(ctx)
    result = run_tool(ctx, "check_refund_eligibility", {"order_id": order_id, "issue": "not_delivered"})
    assert not result["eligible"] and result["rule_id"] == "R-STILL-IN-PROGRESS"


def test_large_missing_items_need_approval(ctx_for):
    ctx = ctx_for("C008")
    order_id = latest_order(ctx)
    pizzas = [item_id(ctx, order_id, "calabresa"), item_id(ctx, order_id, "portuguesa")]
    result = run_tool(
        ctx,
        "check_refund_eligibility",
        {"order_id": order_id, "issue": "missing_item", "item_ids": pizzas},
    )
    assert result["amount_cents"] == 5490 + 5790 and result["requires_human_approval"]


def test_cannot_read_another_customers_order(ctx_for):
    other_order = latest_order(ctx_for("C002"))
    result = run_tool(ctx_for("C001"), "get_order_details", {"order_id": other_order})
    assert "error" in result and "não encontrado" in result["error"]


def test_cannot_refund_another_customers_order(ctx_for):
    other_order = latest_order(ctx_for("C004"))
    result = run_tool(ctx_for("C001"), "issue_refund", {"order_id": other_order, "issue": "not_delivered"})
    assert "error" in result


def test_invalid_issue_is_rejected(ctx_for):
    ctx = ctx_for("C001")
    result = run_tool(ctx, "check_refund_eligibility", {"order_id": latest_order(ctx), "issue": "free_money"})
    assert "error" in result


def test_escalation_creates_ticket(ctx_for, repo):
    ctx = ctx_for("C006")
    result = run_tool(ctx, "escalate_to_human", {"summary": "Cliente insiste no reembolso", "priority": "alta"})
    assert result["ticket_id"].startswith("TK-")
    assert repo.list_tickets("C006")[0].priority == "alta"


def test_policy_search_finds_relevant_section():
    top = search_policies("o pedido atrasou uma hora, tenho direito a reembolso?")[0]
    assert top["title"] == "Atraso na entrega"
    top = search_policies("quanto tempo demora para o dinheiro voltar no pix")[0]
    assert top["title"] == "Formas de reembolso"
