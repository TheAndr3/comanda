"""Interface do runtime de agentes (ADR-0017).

O runtime decide o caminho da conversa; o Atendimento executa. O runtime nunca recebe o
contexto das ferramentas nem o repositório: tudo o que ele faz passa por `AgentHost`, que o
Atendimento implementa. É isso que mantém o cliente fixo e o Rastro completo.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from .trace import Agent, Specialist


@dataclass(frozen=True)
class Usage:
    """Tokens de um agente num modelo e o custo equivalente em API (ADR-0014).

    `cost_usd` é o custo usado nos relatórios: o que o SDK informou, quando informa, ou o da
    tabela pública. `price_table_cost_usd` é sempre o cálculo pela tabela, para conferir o
    valor do SDK. Os dois são estimativas em dólares com frações de centavo, não dinheiro do
    domínio; por isso são `float`, e não centavos inteiros. `input_tokens` não inclui os
    tokens lidos ou gravados no cache, que têm preço próprio.
    """

    agent: Agent
    model: str
    input_tokens: int
    output_tokens: int
    cost_usd: float | None = None
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    price_table_cost_usd: float | None = None


class AgentHost(Protocol):
    """O que o Atendimento oferece ao runtime durante um turno."""

    def say(self, text: str) -> None:
        """Fala da Recepção ao cliente. Especialistas não falam com o cliente."""
        ...

    def delegate(self, specialist: Specialist, task: str) -> None:
        """A Recepção passa uma tarefa a um especialista."""
        ...

    def specialist_returned(self, specialist: Specialist, result: str) -> None:
        """O especialista devolve o resultado à Recepção."""
        ...

    def call_tool(self, agent: Agent, name: str, args: dict[str, Any]) -> dict[str, Any]:
        """Executa uma ferramenta no contexto do cliente do atendimento e devolve o resultado."""
        ...

    def record_usage(self, usage: Usage) -> None:
        """Registra tokens e custo de uma chamada de modelo."""
        ...

    def guardrail_blocked(
        self, agent: Agent, guardrail: str, tool: str, args: dict[str, Any], reason: str
    ) -> None:
        """Registra uma ação que um guardrail impediu antes de a ferramenta rodar."""
        ...


class AgentRuntime(Protocol):
    """Um runtime responde a cada fala do cliente agindo sobre o `AgentHost`."""

    async def respond(self, message: str, host: AgentHost) -> None: ...
