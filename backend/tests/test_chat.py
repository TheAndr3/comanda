"""`comanda chat` sobre o Atendimento: o que o terminal mostra e como ele encerra (sem chamar modelo)."""

import asyncio
import builtins
import io
import json
import sys
import threading

import pytest

from comanda.chat import describe, run_chat
from comanda.runtime import (
    AgentRuntimeError,
    Delegate,
    Return,
    Say,
    ScriptedRuntime,
    Session,
    ToolCall,
    Usage,
    start_session,
)

SCRIPT = [
    [
        Say("Vou verificar."),
        Delegate("orders", "Status do pedido."),
        ToolCall("orders", "list_my_orders", {"limit": 1}),
        Return("orders", "O-10002, entregue."),
        Usage("reception", "haiku", input_tokens=10, output_tokens=5, cost_usd=0.001),
        ToolCall("orders", "escalate_to_human", {"summary": "x"}),
        Say("Entregue."),
    ]
]


def feed(monkeypatch, *lines: str) -> None:
    answers = iter(lines)

    def fake_input(prompt: str = "") -> str:
        try:
            return next(answers)
        except StopIteration:
            raise EOFError from None

    monkeypatch.setattr(builtins, "input", fake_input)


def trace_types(path) -> list[str]:
    return [json.loads(line)["type"] for line in path.read_text(encoding="utf-8").splitlines()]


async def test_trace_lines_can_be_printed_in_a_windows_console(repo, settings, monkeypatch):
    # cp1252 não tem `→` nem `←`; saída redirecionada no Windows quebrava com UnicodeEncodeError.
    feed(monkeypatch, "Cadê meu pedido?")
    out = io.TextIOWrapper(io.BytesIO(), encoding="cp1252", errors="strict")
    monkeypatch.setattr(sys, "stdout", out)

    status = await run_chat(repo, settings, "C001", show_trace=True, runtime=ScriptedRuntime(SCRIPT))
    out.flush()

    assert status == 0
    assert "Entregue." in out.buffer.getvalue().decode("cp1252")  # type: ignore[attr-defined]


async def test_reply_with_characters_the_console_cannot_print_does_not_crash_the_chat(
    repo, settings, monkeypatch
):
    feed(monkeypatch, "Oi")
    out = io.TextIOWrapper(io.BytesIO(), encoding="cp1252", errors="strict")
    monkeypatch.setattr(sys, "stdout", out)

    status = await run_chat(repo, settings, "C001", runtime=ScriptedRuntime([[Say("Pronto 🍕")]]))

    assert status == 0


async def test_redirected_utf8_input_reaches_the_reception_intact_on_a_cp1252_system(
    repo, settings, monkeypatch
):
    # `comanda chat < falas.txt` no Windows: o stdin redirecionado vem em cp1252 por padrão.
    stdin = io.TextIOWrapper(io.BytesIO("Não chegou a coxinha, é sério\n".encode()), encoding="cp1252")
    monkeypatch.setattr(sys, "stdin", stdin)

    status = await run_chat(repo, settings, "C001", runtime=ScriptedRuntime([[Say("Entendi.")]]))

    assert status == 0
    [trace] = list(settings.runs_dir.glob("*.jsonl"))
    said = [json.loads(line)["data"] for line in trace.read_text(encoding="utf-8").splitlines()]
    assert {"text": "Não chegou a coxinha, é sério"} in said


async def test_every_trace_line_is_plain_cp1252(repo, settings):
    session = start_session(repo, settings, "C001", ScriptedRuntime(SCRIPT))
    turn = await session.send("Oi")

    for event in turn.events:
        if (line := describe(event)) is not None:
            line.encode("cp1252")


async def test_ctrl_c_while_waiting_for_the_customer_still_closes_the_session(
    repo, settings, monkeypatch, capsys
):
    release = threading.Event()
    monkeypatch.setattr(builtins, "input", lambda prompt="": release.wait(5) and "")

    task = asyncio.create_task(run_chat(repo, settings, "C001", runtime=ScriptedRuntime([])))
    await asyncio.sleep(0.2)
    task.cancel()  # é o que `asyncio.run` faz com o Ctrl+C
    try:
        status = await task
    finally:
        release.set()

    out = capsys.readouterr().out
    assert status == 0
    assert "Rastro em " in out
    [trace] = list(settings.runs_dir.glob("*.jsonl"))
    assert trace_types(trace)[-1] == "session_ended"


class SpeaksThenHangs:
    def __init__(self) -> None:
        self.closed = False

    async def respond(self, message, host):
        host.say("Vou verificar seu pedido.")
        host.record_usage(Usage("reception", "haiku", input_tokens=10, output_tokens=5, cost_usd=0.001))
        await asyncio.sleep(30)

    async def aclose(self) -> None:
        self.closed = True


async def test_ctrl_c_during_a_turn_shows_the_partial_reply_the_tokens_and_the_trace(
    repo, settings, monkeypatch, capsys
):
    feed(monkeypatch, "Cadê meu pedido?")
    runtime = SpeaksThenHangs()

    task = asyncio.create_task(run_chat(repo, settings, "C001", runtime=runtime))
    await asyncio.sleep(0.3)
    task.cancel()  # é o que `asyncio.run` faz com o Ctrl+C
    status = await task

    out = capsys.readouterr().out
    assert status == 0
    assert "Vou verificar seu pedido." in out
    assert "Tokens por modelo:" in out
    assert "Rastro em " in out
    assert runtime.closed


async def test_the_runtime_is_closed_even_if_closing_the_session_fails(repo, settings, monkeypatch):
    feed(monkeypatch, "sair")
    runtime = SpeaksThenHangs()

    def disk_full(self) -> None:
        raise OSError("disco cheio")

    monkeypatch.setattr(Session, "close", disk_full)

    with pytest.raises(OSError, match="disco cheio"):
        await run_chat(repo, settings, "C001", runtime=runtime)
    assert runtime.closed


class SpeaksThenFails:
    async def respond(self, message, host):
        host.say("Vou verificar seu pedido.")
        raise AgentRuntimeError("error_max_turns")

    async def aclose(self) -> None:
        pass


async def test_what_the_reception_said_before_a_runtime_error_is_shown(repo, settings, monkeypatch, capsys):
    feed(monkeypatch, "Cadê meu pedido?")

    status = await run_chat(repo, settings, "C001", runtime=SpeaksThenFails())

    captured = capsys.readouterr()
    assert status == 1
    assert "Vou verificar seu pedido." in captured.out
    assert "error_max_turns" in captured.err


class FailsToClose:
    async def respond(self, message, host):
        host.say("Oi!")

    async def aclose(self) -> None:
        raise RuntimeError("o CLI não respondeu ao disconnect")


async def test_session_is_closed_in_the_trace_even_if_the_runtime_fails_to_close(
    repo, settings, monkeypatch, capsys
):
    feed(monkeypatch, "Oi", "sair")

    status = await run_chat(repo, settings, "C001", runtime=FailsToClose())

    captured = capsys.readouterr()
    assert status == 0
    assert "Rastro em " in captured.out
    assert "o CLI não respondeu" in captured.err
    [trace] = list(settings.runs_dir.glob("*.jsonl"))
    assert trace_types(trace)[-1] == "session_ended"
