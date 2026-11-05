"""CLI do Comanda: adaptador fino sobre as operações de domínio.

`comanda seed` recria o banco; `comanda queue ...` é a Fila do atendente humano (ADR-0007).
"""

from __future__ import annotations

import argparse
import getpass
import sys
from collections.abc import Sequence

from .config import get_settings
from .data.seed import seed_database
from .domain.db import Repository
from .domain.policies import format_brl
from .domain.queue import QueueError, approve_refund, list_queue, reject_refund, resolve_ticket


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="comanda", description="Atendimento multi-agente de delivery.")
    commands = parser.add_subparsers(dest="command", required=True)

    commands.add_parser("seed", help="Recria o banco a partir do seed determinístico.")

    queue = commands.add_parser("queue", help="Fila do atendente humano.").add_subparsers(
        dest="action", required=True
    )
    queue.add_parser("list", help="Lista reembolsos pendentes e tickets abertos.")
    by = argparse.ArgumentParser(add_help=False)
    by.add_argument("--by", default=getpass.getuser(), help="Quem decide (padrão: usuário do sistema).")
    approve = queue.add_parser("approve", parents=[by], help="Aprova um reembolso pendente.")
    approve.add_argument("refund_id")
    reject = queue.add_parser("reject", parents=[by], help="Recusa um reembolso pendente com motivo.")
    reject.add_argument("refund_id")
    reject.add_argument("--reason", required=True, help="Motivo da recusa (obrigatório).")
    resolve = queue.add_parser("resolve", help="Marca um ticket como resolvido.")
    resolve.add_argument("ticket_id")
    return parser


def _customer_label(repo: Repository, customer_id: str) -> str:
    customer = repo.get_customer(customer_id)
    return f"{customer_id} {customer.name}" if customer else customer_id


def _print_queue(repo: Repository) -> None:
    queue = list_queue(repo)
    print(f"Reembolsos pendentes ({len(queue.pending_refunds)})")
    for r in queue.pending_refunds:
        customer = _customer_label(repo, r.customer_id)
        print(f"  {r.id}  {customer}  pedido {r.order_id}  {r.rule_id}  {format_brl(r.amount_cents)}")
    print(f"Tickets abertos ({len(queue.open_tickets)})")
    for t in queue.open_tickets:
        order = f"pedido {t.order_id}" if t.order_id else "sem pedido"
        print(f"  {t.id}  {_customer_label(repo, t.customer_id)}  {t.priority}  {order}  {t.summary}")


def _run_queue(repo: Repository, args: argparse.Namespace) -> None:
    if args.action == "list":
        _print_queue(repo)
    elif args.action == "approve":
        approve_refund(repo, args.refund_id, decided_by=args.by)
        print(f"Reembolso {args.refund_id} aprovado.")
    elif args.action == "reject":
        reject_refund(repo, args.refund_id, decided_by=args.by, reason=args.reason)
        print(f"Reembolso {args.refund_id} recusado.")
    elif args.action == "resolve":
        resolve_ticket(repo, args.ticket_id)
        print(f"Ticket {args.ticket_id} resolvido.")


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    settings = get_settings()

    if args.command == "seed":
        seed_database(settings.db_path)
        print(f"Banco recriado em {settings.db_path}.")
        return 0

    if not settings.db_path.exists():
        print(f"Banco não encontrado em {settings.db_path}. Rode `comanda seed` antes.", file=sys.stderr)
        return 1
    try:
        _run_queue(Repository(settings.db_path), args)
    except QueueError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
