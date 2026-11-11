"""`comanda chat` sobre o Atendimento: o que o terminal mostra e como ele encerra (sem chamar modelo)."""

import asyncio
import builtins
import io
import json
import sys
import threading

from comanda.chat import describe, run_chat
from comanda.runtime import (
    AgentRuntimeError,
    Delegate,
    Return,
    Say,
    ScriptedRuntime,
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
