"""Custo equivalente em API: o que os tokens de um atendimento custariam na API pública (ADR-0014).

Os agentes rodam na assinatura, que não cobra por token; este valor é uma estimativa para
comparar configurações de modelos, nunca um gasto real.

Preços em dólares por milhão de tokens, da tabela pública da Anthropic (consultada em
2026-11-07, referência de 2026-10-06). Gravar no cache custa 1,25 vez a entrada com validade
de 5 minutos e 2 vezes com validade de 1 hora. O Haiku 5.5 cobra mais acima de 100 mil
tokens de prompt; um turno de atendimento fica bem abaixo disso, então a faixa é ignorada.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Price:
    input: float
    output: float
    cache_read: float

    @property
    def cache_write_5m(self) -> float:
        return self.input * 1.25

    @property
    def cache_write_1h(self) -> float:
        return self.input * 2


# Chave: trecho do id do modelo. Ids com data ou prefixo de provedor também casam.
PRICES: dict[str, Price] = {
    "haiku-5-5": Price(input=0.10, output=0.50, cache_read=0.01),
    "sonnet-5-5": Price(input=2.00, output=10.00, cache_read=0.20),
    # No Opus 5.5 a leitura de cache custa 0,05 vez a entrada, não 0,1 como nos outros.
    "opus-5-5": Price(input=4.00, output=20.00, cache_read=0.20),
}


def price_for(model: str) -> Price | None:
    return next((price for key, price in PRICES.items() if key in model), None)


def estimate_cost_usd(
    model: str,
    *,
    input_tokens: int,
    output_tokens: int,
    cache_read_tokens: int = 0,
    cache_write_5m_tokens: int = 0,
    cache_write_1h_tokens: int = 0,
) -> float | None:
    """Custo pela tabela, ou `None` para um modelo fora dela."""
    price = price_for(model)
    if price is None:
        return None
    total = (
        input_tokens * price.input
        + output_tokens * price.output
        + cache_read_tokens * price.cache_read
        + cache_write_5m_tokens * price.cache_write_5m
        + cache_write_1h_tokens * price.cache_write_1h
    )
    return round(total / 1_000_000, 8)
