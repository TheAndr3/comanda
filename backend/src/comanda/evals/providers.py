"""Interface de provedor do cliente simulado e do juiz (ADR-0004).

Um provedor recebe uma instrução de sistema e a conversa até aqui e devolve o próximo texto
do modelo. Os papéis são os do próprio modelo: `assistant` é o que ele já disse e `user` é o
que o outro lado disse. Adaptadores: Gemini (camada gratuita), Ollama (local) e roteirizado (testes).
"""

from __future__ import annotations

import asyncio
import os
import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Literal, Protocol

import httpx

from ..config import OLLAMA_DEFAULT_HOST, OLLAMA_DEFAULT_NUM_CTX, Settings

GEMINI_API_KEY_ENV = "GEMINI_API_KEY"
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
# Falhas passageiras da camada gratuita: limite de uso e indisponibilidade.
_RETRY_STATUS = {429, 500, 502, 503, 504}
# Teto da espera somada de uma chamada: cobre a janela por minuto do Gemini sem prender a rodada.
MAX_TOTAL_WAIT_S = 90.0
_RETRY_DELAY = re.compile(r"^(\d+(?:\.\d+)?)s$")


@dataclass(frozen=True)
class ProviderMessage:
    role: Literal["user", "assistant"]
    text: str


class ProviderError(Exception):
    """O provedor não devolveu texto."""


class Provider(Protocol):
    async def complete(self, system: str, messages: Sequence[ProviderMessage]) -> str: ...


class ScriptedProvider:
    """Devolve respostas fixas, uma por chamada, sem chamar modelo."""

    def __init__(self, replies: Sequence[str]) -> None:
        self._replies = list(replies)
        self._next = 0

    async def complete(self, system: str, messages: Sequence[ProviderMessage]) -> str:
        if self._next >= len(self._replies):
            raise ProviderError(f"O roteiro do provedor tem {len(self._replies)} respostas e acabou.")
        reply = self._replies[self._next]
        self._next += 1
        return reply


class GeminiProvider:
    """Gemini pela API REST do Google AI Studio.

    A chave vem só da variável de ambiente `GEMINI_API_KEY` e vai no cabeçalho, nunca na URL,
    para não aparecer em logs de requisição. Ela não é guardada em `Settings` nem no Rastro.
    """

    def __init__(
        self,
        model: str,
        api_key: str | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        max_retries: int = 6,
        max_total_wait_s: float = MAX_TOTAL_WAIT_S,
    ) -> None:
        key = api_key or os.getenv(GEMINI_API_KEY_ENV)
        if not key:
            raise ProviderError(
                f"Defina {GEMINI_API_KEY_ENV} com uma chave do Google AI Studio para usar o Gemini."
            )
        self.model = model
        self._key = key
        self._transport = transport
        self._max_retries = max_retries
        self._max_total_wait_s = max_total_wait_s

    def __repr__(self) -> str:
        return f"GeminiProvider(model={self.model!r})"

    async def complete(self, system: str, messages: Sequence[ProviderMessage]) -> str:
        body: dict[str, Any] = {
            "system_instruction": {"parts": [{"text": system}]},
            "contents": [
                {"role": "model" if m.role == "assistant" else "user", "parts": [{"text": m.text}]}
                for m in messages
            ],
        }
        headers = {"x-goog-api-key": self._key, "Content-Type": "application/json"}
        url = GEMINI_URL.format(model=self.model)
        # Repete 429, 5xx passageiro e queda de conexão. Espera o que o Gemini pede (`Retry-After` ou
        # `retryDelay`) ou, sem pedido, 1 s, 2 s, 4 s...; a soma das esperas tem teto, e uma espera
        # que o estouraria nem começa: o erro volta na hora em vez de prender a rodada.
        response: httpx.Response | None = None
        failure = "sem resposta"
        hint: float | None = None
        waited = 0.0
        async with httpx.AsyncClient(transport=self._transport, timeout=60) as client:
            for attempt in range(self._max_retries + 1):
                if attempt:
                    wait = max(hint or 0.0, 2 ** (attempt - 1))
                    if waited + wait > self._max_total_wait_s:
                        break
                    await asyncio.sleep(wait)
                    waited += wait
                try:
                    response = await client.post(url, json=body, headers=headers)
                except httpx.TransportError as exc:
                    response, failure, hint = None, str(exc) or type(exc).__name__, None
                    continue
                if response.status_code not in _RETRY_STATUS:
                    break
                hint = _retry_hint(response)
        if response is None:
            raise ProviderError(f"Gemini inacessível: {failure}")
        if response.status_code != 200:
            raise ProviderError(f"Gemini respondeu {response.status_code}: {response.text[:300]}")
        return _gemini_text(response.json())


def _retry_hint(response: httpx.Response) -> float | None:
    """Quanto o Gemini pede para esperar: `Retry-After` em segundos ou `retryDelay` ("34s") no corpo."""
    header = response.headers.get("Retry-After", "")
    if header.strip().isdigit():
        return float(header)
    try:
        details = response.json().get("error", {}).get("details") or []
    except (ValueError, AttributeError):
        return None
    for detail in details:
        if isinstance(detail, dict) and (match := _RETRY_DELAY.match(str(detail.get("retryDelay", "")))):
            return float(match.group(1))
    return None


def provider_from_settings(settings: Settings, model: str | None = None) -> Provider:
    """O provedor escolhido por `COMANDA_EVAL_PROVIDER`; sem `model`, o do cliente simulado."""
    if settings.eval_provider == "gemini":
        return GeminiProvider(model or settings.gemini_model)
    if settings.eval_provider == "ollama":
        return OllamaProvider(
            model or settings.ollama_model, host=settings.ollama_host, num_ctx=settings.ollama_num_ctx
        )
    raise ProviderError(
        f"Provedor de avaliação desconhecido: {settings.eval_provider}. Use 'gemini' ou 'ollama'."
    )


def _gemini_text(payload: dict[str, Any]) -> str:
    candidates = payload.get("candidates") or []
    if not candidates:
        reason = (payload.get("promptFeedback") or {}).get("blockReason", "sem candidatos")
        raise ProviderError(f"Gemini não devolveu texto ({reason}).")
    parts = (candidates[0].get("content") or {}).get("parts") or []
    text = "".join(p.get("text", "") for p in parts if not p.get("thought")).strip()
    if not text:
        reason = candidates[0].get("finishReason", "texto vazio")
        raise ProviderError(f"Gemini não devolveu texto ({reason}).")
    return text


# Raciocínio que modelos como qwen3 e deepseek-r1 põem no texto; como os `thought` do Gemini, não é fala.
_THINK_BLOCK = re.compile(r"<think>.*?</think>", re.DOTALL)


class OllamaProvider:
    """Modelo open source local pelo `/api/chat` do Ollama, sem chave e sem streaming."""

    def __init__(
        self,
        model: str,
        host: str = OLLAMA_DEFAULT_HOST,
        num_ctx: int = OLLAMA_DEFAULT_NUM_CTX,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.model = model
        self.host = host.rstrip("/")
        self._num_ctx = num_ctx
        self._transport = transport

    def __repr__(self) -> str:
        return f"OllamaProvider(model={self.model!r}, host={self.host!r})"

    async def complete(self, system: str, messages: Sequence[ProviderMessage]) -> str:
        body: dict[str, Any] = {
            "model": self.model,
            "stream": False,
            "options": {"num_ctx": self._num_ctx},
            "messages": [
                {"role": "system", "content": system},
                *({"role": m.role, "content": m.text} for m in messages),
            ],
        }
        try:
            async with httpx.AsyncClient(transport=self._transport, timeout=300) as client:
                response = await client.post(f"{self.host}/api/chat", json=body)
        except httpx.TransportError as exc:
            raise ProviderError(
                f"Ollama inacessível em {self.host}: {str(exc) or type(exc).__name__}. Rode `ollama serve`."
            ) from exc
        if response.status_code != 200:
            raise ProviderError(f"Ollama respondeu {response.status_code}: {response.text[:300]}")
        return _ollama_text(response)


def _ollama_text(response: httpx.Response) -> str:
    try:
        payload = response.json()
    except ValueError:
        payload = None
    message = payload.get("message") if isinstance(payload, dict) else None
    content = message.get("content") if isinstance(message, dict) else None
    text = _THINK_BLOCK.sub("", content).strip() if isinstance(content, str) else ""
    if not text:
        reason = payload.get("done_reason", "texto vazio") if isinstance(payload, dict) else "sem JSON"
        raise ProviderError(f"Ollama não devolveu texto ({reason}): {response.text[:300]}")
    return text
