"""Adaptador Ollama do cliente simulado e do juiz, com resposta HTTP simulada: nenhum Ollama rodando."""

import json
from dataclasses import replace

import httpx
import pytest

from comanda.config import Settings
from comanda.evals import OllamaProvider, ProviderError, ProviderMessage, provider_from_settings


def ollama_answering(content: str, seen: list[httpx.Request]) -> httpx.MockTransport:
    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200,
            json={"model": "qwen-test", "message": {"role": "assistant", "content": content}, "done": True},
        )

    return httpx.MockTransport(handle)


async def test_ollama_sends_the_conversation_to_the_local_host_and_returns_the_text():
    seen: list[httpx.Request] = []
    provider = OllamaProvider("qwen-test", transport=ollama_answering(" Pode sim. ", seen))

    reply = await provider.complete(
        "Você é um cliente.",
        [ProviderMessage("user", "Abriu o chat."), ProviderMessage("assistant", "Faltou o refrigerante.")],
    )

    assert reply == "Pode sim."
    [request] = seen
    assert str(request.url) == "http://localhost:11434/api/chat"
    body = json.loads(request.content)
    assert body["model"] == "qwen-test"
    assert body["stream"] is False
    assert [(m["role"], m["content"]) for m in body["messages"]] == [
        ("system", "Você é um cliente."),
        ("user", "Abriu o chat."),
        ("assistant", "Faltou o refrigerante."),
    ]


async def test_ollama_asks_for_a_context_window_that_fits_the_judge_prompt():
    seen: list[httpx.Request] = []
    default = OllamaProvider("qwen-test", transport=ollama_answering("Oi.", seen))
    larger = OllamaProvider("qwen-test", num_ctx=32768, transport=ollama_answering("Oi.", seen))

    await default.complete("sistema", [ProviderMessage("user", "oi")])
    await larger.complete("sistema", [ProviderMessage("user", "oi")])

    assert [json.loads(r.content)["options"]["num_ctx"] for r in seen] == [16384, 32768]


async def test_ollama_error_status_becomes_provider_error_with_its_message():
    missing = {"error": "model 'qwen-test' not found, try pulling it first"}
    transport = httpx.MockTransport(lambda _: httpx.Response(404, json=missing))
    provider = OllamaProvider("qwen-test", transport=transport)

    with pytest.raises(ProviderError, match=r"404.*not found"):
        await provider.complete("sistema", [ProviderMessage("user", "oi")])


async def test_ollama_not_running_becomes_provider_error_naming_the_host():
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("conexão recusada", request=request)

    provider = OllamaProvider("qwen-test", host="http://gpu-box:11434", transport=httpx.MockTransport(refuse))

    with pytest.raises(ProviderError, match=r"http://gpu-box:11434.*conexão recusada"):
        await provider.complete("sistema", [ProviderMessage("user", "oi")])


async def test_ollama_drops_the_reasoning_of_thinking_models():
    content = '<think>\nO formato pede {"violations": ...}.\n</think>\n\n{"violations": []}'
    provider = OllamaProvider("qwen-test", transport=ollama_answering(content, []))

    assert await provider.complete("sistema", [ProviderMessage("user", "oi")]) == '{"violations": []}'


def test_ollama_is_chosen_by_the_environment_for_the_customer_and_the_judge(monkeypatch):
    monkeypatch.setenv("COMANDA_EVAL_PROVIDER", "ollama")
    monkeypatch.setenv("COMANDA_OLLAMA_MODEL", "qwen3:8b")
    monkeypatch.setenv("COMANDA_OLLAMA_HOST", "http://gpu-box:11434")
    monkeypatch.delenv("COMANDA_JUDGE_MODEL", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)  # o Ollama não precisa de chave
    settings = Settings()

    customer = provider_from_settings(settings)
    judge = provider_from_settings(settings, settings.judge_model)

    assert isinstance(customer, OllamaProvider)
    assert (customer.model, customer.host) == ("qwen3:8b", "http://gpu-box:11434")
    assert isinstance(judge, OllamaProvider)
    assert judge.model == "qwen3:8b"
    assert settings.simulated_customer_model == "qwen3:8b"


def test_the_judge_can_use_its_own_ollama_model(monkeypatch):
    monkeypatch.setenv("COMANDA_EVAL_PROVIDER", "ollama")
    monkeypatch.setenv("COMANDA_OLLAMA_MODEL", "llama3.2:3b")
    monkeypatch.setenv("COMANDA_JUDGE_MODEL", "qwen3:14b")
    settings = Settings()

    judge = provider_from_settings(settings, settings.judge_model)

    assert isinstance(judge, OllamaProvider)
    assert judge.model == "qwen3:14b"
    assert settings.simulated_customer_model == "llama3.2:3b"


def test_switching_a_copy_of_the_settings_to_ollama_moves_the_judge_too():
    gemini = Settings(eval_provider="gemini", judge_model_override=None)

    ollama = replace(gemini, eval_provider="ollama", ollama_model="qwen3:8b")

    assert gemini.judge_model == gemini.gemini_model
    assert ollama.judge_model == "qwen3:8b"


def test_an_unknown_provider_names_both_choices():
    with pytest.raises(ProviderError, match="'gemini' ou 'ollama'"):
        provider_from_settings(Settings(eval_provider="openai"))


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(200, json={"message": {"role": "assistant", "content": "  "}, "done_reason": "stop"}),
        httpx.Response(200, json={"done": True}),
        httpx.Response(200, text="<html>proxy</html>"),
    ],
    ids=["empty-content", "no-message", "not-json"],
)
async def test_ollama_without_text_becomes_provider_error(response: httpx.Response):
    provider = OllamaProvider("qwen-test", transport=httpx.MockTransport(lambda _: response))

    with pytest.raises(ProviderError, match="Ollama não devolveu texto"):
        await provider.complete("sistema", [ProviderMessage("user", "oi")])
