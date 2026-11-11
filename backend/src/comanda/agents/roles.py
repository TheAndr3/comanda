"""Papéis e ferramentas de cada agente (ADR-0013).

Módulo sem dependências do resto do pacote: o Rastro, o Atendimento e o adaptador do SDK
importam daqui, e `comanda.agents` não importa o runtime, então não há ciclo.
"""

from __future__ import annotations

from typing import Literal

Specialist = Literal["orders", "refunds"]
Agent = Literal["reception", Specialist]

# ADR-0013: cada agente recebe só as ferramentas do seu papel. O Atendimento recusa
# qualquer chamada fora desta tabela, seja qual for o runtime (ADR-0017).
AGENT_TOOLS: dict[Agent, tuple[str, ...]] = {
    "reception": ("search_policies", "escalate_to_human"),
    "orders": ("list_my_orders", "get_order_details", "track_delivery", "get_restaurant_info"),
    "refunds": ("check_refund_eligibility", "issue_refund", "escalate_to_human"),
}
