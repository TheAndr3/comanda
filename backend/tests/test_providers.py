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


@pytest.fixture
def no_backoff(monkeypatch):
    waits: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        waits.append(seconds)

    monkeypatch.setattr("comanda.evals.providers.asyncio.sleep", fake_sleep)
    return waits


async def test_gemini_retries_a_429_with_a_short_growing_backoff_and_then_answers(no_backoff):
    answers = iter([429, 429, 200])

    def handle(_: httpx.Request) -> httpx.Response:
        if next(answers) == 429:
            return httpx.Response(429, json={"error": {"message": "limite"}})
        return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": "Oi."}]}}]})

    provider = GeminiProvider("gemini-test", api_key=FAKE_KEY, transport=httpx.MockTransport(handle))

    assert await provider.complete("sistema", [ProviderMessage("user", "oi")]) == "Oi."
    assert no_backoff == [1, 2]


async def test_gemini_gives_up_after_a_bounded_number_of_retries(no_backoff):
    calls: list[int] = []

    def handle(_: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(429, json={"error": {"message": "limite"}})

    provider = GeminiProvider(
        "gemini-test", api_key=FAKE_KEY, transport=httpx.MockTransport(handle), max_retries=3
    )

    with pytest.raises(ProviderError, match="429"):
        await provider.complete("sistema", [ProviderMessage("user", "oi")])
    assert len(calls) == 4  # a chamada e 3 repetições
    assert sum(no_backoff) <= 10


async def test_gemini_retries_a_dropped_connection_and_reports_it_as_a_provider_error(no_backoff):
    def handle(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("sem rede", request=request)

    provider = GeminiProvider(
        "gemini-test", api_key=FAKE_KEY, transport=httpx.MockTransport(handle), max_retries=2
    )

    with pytest.raises(ProviderError, match="sem rede"):
        await provider.complete("sistema", [ProviderMessage("user", "oi")])
    assert no_backoff == [1, 2]


def sequence(*responses: httpx.Response):
    answers = iter(responses)
    calls: list[int] = []

    def handle(_: httpx.Request) -> httpx.Response:
        calls.append(1)
        return next(answers)

    return httpx.MockTransport(handle), calls


OK = httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": "Oi."}]}}]})


async def test_gemini_waits_as_long_as_the_retry_after_header_says(no_backoff):
    transport, _ = sequence(httpx.Response(429, headers={"Retry-After": "7"}), OK)
    provider = GeminiProvider("gemini-test", api_key=FAKE_KEY, transport=transport)

    assert await provider.complete("sistema", [ProviderMessage("user", "oi")]) == "Oi."
    assert no_backoff == [7]


async def test_gemini_waits_as_long_as_the_retry_delay_in_the_body_says(no_backoff):
    body = {
        "error": {
            "code": 429,
            "details": [{"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": "12s"}],
        }
    }
    transport, _ = sequence(httpx.Response(429, json=body), OK)
    provider = GeminiProvider("gemini-test", api_key=FAKE_KEY, transport=transport)

    assert await provider.complete("sistema", [ProviderMessage("user", "oi")]) == "Oi."
    assert no_backoff == [12]


async def test_gemini_retries_502_503_and_504_too(no_backoff):
    transport, calls = sequence(httpx.Response(502), httpx.Response(504), httpx.Response(503), OK)
    provider = GeminiProvider("gemini-test", api_key=FAKE_KEY, transport=transport)

    assert await provider.complete("sistema", [ProviderMessage("user", "oi")]) == "Oi."
    assert len(calls) == 4


async def test_gemini_does_not_wait_past_the_total_budget(no_backoff):
    limited = httpx.Response(429, headers={"Retry-After": "40"})
    transport, calls = sequence(limited, limited, limited, OK)
    provider = GeminiProvider("gemini-test", api_key=FAKE_KEY, transport=transport)

    with pytest.raises(ProviderError, match="429"):
        await provider.complete("sistema", [ProviderMessage("user", "oi")])
    assert no_backoff == [40, 40]  # a 3ª espera passaria dos 90 s e nem começa
    assert len(calls) == 3


async def test_the_default_backoff_covers_a_per_minute_window_within_the_total_budget(no_backoff):
    transport = httpx.MockTransport(lambda _: httpx.Response(429))
    provider = GeminiProvider("gemini-test", api_key=FAKE_KEY, transport=transport)

    with pytest.raises(ProviderError, match="429"):
        await provider.complete("sistema", [ProviderMessage("user", "oi")])
    assert 60 <= sum(no_backoff) <= 90
