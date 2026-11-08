"""Um Claude Code de mentira para testar o adaptador do SDK sem chamar modelo.

O falso faz o papel do CLI: recebe as `ClaudeAgentOptions` montadas pelo runtime e, a cada
fala do cliente, reproduz um roteiro de mensagens do SDK. Quando o roteiro chama uma
ferramenta, o falso faz o que o CLI faz: roda o hook `PreToolUse` das opções e, se ele não
negar, chama a ferramenta pelo servidor MCP em processo, pelo mesmo protocolo do SDK.
"""

from __future__ import annotations

import itertools
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field
from typing import Any

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    Message,
    ResultMessage,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
)
from claude_agent_sdk._internal.sdk_mcp_bridge import SdkMcpBridge

HAIKU = "claude-haiku-5-5"
SONNET = "claude-sonnet-5-5"

_ids = itertools.count(1)


def usage(input_tokens: int, output_tokens: int, cache_read: int = 0, cache_write: int = 0) -> dict[str, Any]:
    """O campo `usage` de uma `AssistantMessage`, como a API devolve."""
    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cache_read_input_tokens": cache_read,
        "cache_creation_input_tokens": cache_write,
        "cache_creation": {"ephemeral_5m_input_tokens": cache_write, "ephemeral_1h_input_tokens": 0},
    }


@dataclass
class Says:
    """Texto de um agente. Sem `parent`, é a Recepção falando com o cliente."""

    text: str
    model: str = HAIKU
    parent: str | None = None
    usage: dict[str, Any] | None = None


@dataclass
class Delegates:
    """A Recepção chama a ferramenta `Agent` para um especialista."""

    tool_use_id: str
    subagent_type: str
    prompt: str
    tool_name: str = "Agent"


@dataclass
class Calls:
    """Um agente chama uma ferramenta MCP. `parent` é a delegação em curso, se houver."""

    tool: str
    args: dict[str, Any] = field(default_factory=dict)
    parent: str | None = None
    agent_type: str | None = None
    model: str = SONNET
    usage: dict[str, Any] | None = None


@dataclass
class Returns:
    """O resultado do especialista volta à Recepção como resultado da ferramenta `Agent`."""

    tool_use_id: str
    text: str


@dataclass
class Ends:
    """Fim do turno: o `ResultMessage` com tokens e custo por modelo."""

    model_usage: dict[str, dict[str, Any]] = field(default_factory=dict)
    total_cost_usd: float | None = None
    is_error: bool = False
    result: str | None = None


Step = Says | Delegates | Calls | Returns | Ends


def model_usage(
    input_tokens: int, output_tokens: int, cost_usd: float, cache_read: int = 0, cache_write: int = 0
):
    return {
        "inputTokens": input_tokens,
        "outputTokens": output_tokens,
        "cacheReadInputTokens": cache_read,
        "cacheCreationInputTokens": cache_write,
        "webSearchRequests": 0,
        "costUSD": cost_usd,
        "contextWindow": 1_000_000,
        "maxOutputTokens": 128_000,
    }


class FakeClaude:
    """Fábrica e cliente ao mesmo tempo: `runtime = ClaudeAgentRuntime(settings, client_factory=fake)`."""

    def __init__(self, turns: Sequence[Sequence[Step]]) -> None:
        self._turns = [list(turn) for turn in turns]
        self.options: ClaudeAgentOptions | None = None
        self.prompts: list[str] = []
        self.tool_results: list[dict[str, Any]] = []
        self.connected = False
        self._bridges: dict[str, SdkMcpBridge] = {}

    def __call__(self, options: ClaudeAgentOptions) -> FakeClaude:
        self.options = options
        return self

    async def connect(self) -> None:
        self.connected = True

    async def disconnect(self) -> None:
        self.connected = False
        for bridge in self._bridges.values():
            await bridge.aclose()

    async def query(self, prompt: str) -> None:
        self.prompts.append(prompt)

    async def receive_response(self) -> AsyncIterator[Message]:
        for step in self._turns.pop(0):
            for message in await self._play(step):
                yield message

    # --- o que o CLI faria ------------------------------------------------------

    async def _play(self, step: Step) -> list[Message]:
        match step:
            case Says(text, model, parent, usage):
                return [self._assistant([TextBlock(text)], model, parent, usage)]
            case Delegates(tool_use_id, subagent_type, prompt, tool_name):
                args = {"subagent_type": subagent_type, "prompt": prompt, "description": prompt[:40]}
                use = self._assistant([ToolUseBlock(tool_use_id, tool_name, args)], HAIKU, None)
                denied = await self._pre_tool_use(tool_name, args, tool_use_id, None)
                if denied is None:
                    return [use]
                return [use, self._tool_result(tool_use_id, denied, is_error=True)]
            case Calls(tool, args, parent, agent_type, model, usage):
                tool_use_id = f"toolu_{next(_ids)}"
                use = self._assistant([ToolUseBlock(tool_use_id, tool, args)], model, parent, usage)
                denied = await self._pre_tool_use(tool, args, tool_use_id, agent_type)
                if denied is not None:
                    return [use, self._tool_result(tool_use_id, denied, is_error=True, parent=parent)]
                result = await self._call_mcp(tool, args)
                self.tool_results.append(result)
                text = result["content"][0]["text"]
                return [use, self._tool_result(tool_use_id, text, result.get("isError", False), parent)]
            case Returns(tool_use_id, text):
                return [self._tool_result(tool_use_id, [{"type": "text", "text": text}])]
            case Ends(usage_by_model, total_cost_usd, is_error, result):
                return [
                    ResultMessage(
                        subtype="success",
                        duration_ms=10,
                        duration_api_ms=8,
                        is_error=is_error,
                        num_turns=1,
                        session_id="fake",
                        total_cost_usd=total_cost_usd,
                        result=result,
                        model_usage=usage_by_model,  # type: ignore[arg-type]
                    )
                ]
        raise AssertionError(f"Passo desconhecido: {step}")

    def _assistant(self, content, model: str, parent: str | None, usage=None) -> AssistantMessage:
        return AssistantMessage(
            content=content,
            model=model,
            parent_tool_use_id=parent,
            usage=usage,
            message_id=f"msg_{next(_ids)}",
        )

    def _tool_result(self, tool_use_id: str, content, is_error: bool = False, parent: str | None = None):
        return UserMessage(
            content=[ToolResultBlock(tool_use_id, content, is_error)],
            parent_tool_use_id=parent,
        )

    async def _pre_tool_use(
        self, tool: str, args: dict[str, Any], tool_use_id: str, agent_type: str | None
    ) -> str | None:
        """Roda os hooks `PreToolUse`; devolve o motivo se algum negar."""
        assert self.options is not None
        hook_input: dict[str, Any] = {
            "session_id": "fake",
            "transcript_path": "",
            "cwd": "",
            "hook_event_name": "PreToolUse",
            "tool_name": tool,
            "tool_input": args,
            "tool_use_id": tool_use_id,
        }
        if agent_type is not None:
            hook_input |= {"agent_id": f"agent-{agent_type}", "agent_type": agent_type}
        for matcher in (self.options.hooks or {}).get("PreToolUse", []):
            for hook in matcher.hooks:
                output: Any = await hook(hook_input, tool_use_id, {"signal": None})  # type: ignore[arg-type]
                specific = (output or {}).get("hookSpecificOutput", {})
                if specific.get("permissionDecision") == "deny":
                    return specific.get("permissionDecisionReason", "negado")
        return None

    async def _call_mcp(self, tool: str, args: dict[str, Any]) -> dict[str, Any]:
        assert self.options is not None and isinstance(self.options.mcp_servers, dict)
        _, server, name = tool.split("__", 2)
        if server not in self._bridges:
            config: Any = self.options.mcp_servers[server]
            self._bridges[server] = SdkMcpBridge(server, config["instance"])
            await self._bridges[server].handle(
                {
                    "jsonrpc": "2.0",
                    "id": 0,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": "2025-06-18",
                        "capabilities": {},
                        "clientInfo": {"name": "fake", "version": "0"},
                    },
                }
            )
        response = await self._bridges[server].handle(
            {
                "jsonrpc": "2.0",
                "id": next(_ids),
                "method": "tools/call",
                "params": {"name": name, "arguments": args},
            }
        )
        assert response is not None
        return response["result"]
