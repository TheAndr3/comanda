"""Adaptador Gemini do cliente simulado, com resposta HTTP simulada: nenhum teste sai à rede."""

import json

import httpx
import pytest

from comanda.evals import GeminiProvider, ProviderError, ProviderMessage

FAKE_KEY = "chave-falsa-de-teste"


def gemini_answering(text: str, seen: list[httpx.Request]) -> httpx.MockTransport:
    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": text}]}}]})

    return httpx.MockTransport(handle)


async def test_gemini_sends_the_conversation_and_returns_the_text():
    seen: list[httpx.Request] = []
    provider = GeminiProvider("gemini-test", api_key=FAKE_KEY, transport=gemini_answering("Pode sim.", seen))

    reply = await provider.complete(
        "Você é um cliente.",
        [ProviderMessage("user", "Abriu o chat."), ProviderMessage("assistant", "Faltou o refrigerante.")],
    )

    assert reply == "Pode sim."
    [request] = seen
    assert request.url.path == "/v1beta/models/gemini-test:generateContent"
    assert request.headers["x-goog-api-key"] == FAKE_KEY
    assert FAKE_KEY not in str(request.url)
    body = json.loads(request.content)
    assert body["system_instruction"] == {"parts": [{"text": "Você é um cliente."}]}
    assert [(c["role"], c["parts"][0]["text"]) for c in body["contents"]] == [
        ("user", "Abriu o chat."),
        ("model", "Faltou o refrigerante."),
    ]
    assert FAKE_KEY not in repr(provider)


def test_gemini_key_comes_from_the_environment(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    with pytest.raises(ProviderError, match="GEMINI_API_KEY"):
        GeminiProvider("gemini-test")

    monkeypatch.setenv("GEMINI_API_KEY", FAKE_KEY)
    assert GeminiProvider("gemini-test").model == "gemini-test"


async def test_gemini_error_status_becomes_provider_error():
    transport = httpx.MockTransport(lambda _: httpx.Response(400, json={"error": {"message": "inválido"}}))
    provider = GeminiProvider("gemini-test", api_key=FAKE_KEY, transport=transport)

    with pytest.raises(ProviderError, match="400"):
        await provider.complete("sistema", [ProviderMessage("user", "oi")])
