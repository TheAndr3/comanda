"""Mascaramento dos dados pessoais do cliente nos eventos do Rastro (ADR-0008, ADR-0017)."""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable
from typing import Any, Protocol

from ..domain.models import Customer

_SEP = r"[\s().-]*"


def _accent_insensitive(word: str) -> str:
    """Padrão que aceita a palavra com ou sem acento: "Vinícius" casa com "Vinicius"."""
    parts = []
    for char in word:
        base = unicodedata.normalize("NFD", char)[0]
        parts.append(f"[{re.escape(char)}{re.escape(base)}]" if base != char else re.escape(char))
    return "".join(parts)


def _phone_pattern(phone: str) -> str:
    digits = re.sub(r"\D", "", phone)
    if digits.startswith("55") and len(digits) >= 12:  # +55 DDD número
        area, number = digits[2:4], digits[4:]
        prefix = rf"(?:(?:\+?55{_SEP})?\(?{_SEP.join(area)}{_SEP})?"
    else:
        prefix, number = "", digits
    return rf"(?<!\d){prefix}{_SEP.join(number)}(?!\d)"


class PersonalDataMask:
    """Troca nome, e-mail e telefone do cliente por marcadores em qualquer texto de um evento.

    O nome é mascarado inteiro e por partes, sem diferenciar maiúsculas e acentos; o telefone,
    com ou sem DDI e DDD e com qualquer separador.
    """

    def __init__(self, customer: Customer) -> None:
        names = [customer.name, *(part for part in customer.name.split() if len(part) >= 3)]
        self._rules: list[tuple[re.Pattern[str], str]] = [
            (re.compile(re.escape(customer.email), re.IGNORECASE), "[email]"),
            (re.compile(_phone_pattern(customer.phone)), "[telefone]"),
            *((re.compile(rf"\b{_accent_insensitive(name)}\b", re.IGNORECASE), "[nome]") for name in names),
        ]

    def apply(self, value: Any) -> Any:
        if isinstance(value, str):
            for pattern, marker in self._rules:
                value = pattern.sub(marker, value)
            return value
        if isinstance(value, dict):
            return {key: self.apply(item) for key, item in value.items()}
        if isinstance(value, list | tuple):
            return [self.apply(item) for item in value]
        return value


class _Repo(Protocol):
    def list_customer_ids(self) -> list[str]: ...
    def get_customer(self, customer_id: str) -> Customer | None: ...


class CustomersMask:
    """Mascara os dados pessoais de todos os clientes de um banco.

    O Rastro só mascara o cliente do atendimento. Um texto que vai a um relatório versionado, como
    o trecho de uma violação `other_customer_data`, pode citar outro cliente; aqui nenhum aparece.
    """

    def __init__(self, customers: Iterable[Customer] = ()) -> None:
        self._masks = [PersonalDataMask(c) for c in customers]

    @classmethod
    def of(cls, repo: _Repo) -> CustomersMask:
        return cls(c for cid in repo.list_customer_ids() if (c := repo.get_customer(cid)) is not None)

    def apply(self, value: Any) -> Any:
        for mask in self._masks:
            value = mask.apply(value)
        return value
