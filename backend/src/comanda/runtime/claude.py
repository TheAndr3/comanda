"""Runtime do Claude Agent SDK: a Recepção e os especialistas como agentes do Claude Code.

A Recepção é o agente principal da sessão do SDK e os especialistas são subagentes
chamados pela ferramenta `Agent` (ADR-0003). Cada agente tem um servidor MCP em processo
com só as ferramentas do seu papel (ADR-0013), e cada ferramenta executa pelo `AgentHost`
(ADR-0017): o runtime não vê repositório, cliente nem Rastro.

A sessão do SDK é isolada: sem ferramentas nativas além de `Agent`, sem configurações do
usuário ou do projeto, só os servidores MCP declarados aqui e sem pedidos de permissão.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from typing import Any, Protocol

from claude_agent_sdk import (
    AgentDefinition,
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    HookContext,
    HookInput,
    HookJSONOutput,
    HookMatcher,
    Message,
    ResultMessage,
    SdkMcpTool,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
    create_sdk_mcp_server,
)

from ..agents import RECEPTION, SPECIALISTS, AgentSpec
from ..config import Settings
from ..tools import TOOLS
from .base import AgentHost, Usage
from .pricing import estimate_cost_usd
from .trace import Agent, Specialist

# O CLI chama a ferramenta de subagentes de `Agent`; versões antigas, de `Task`.
DELEGATION_TOOLS = frozenset({"Agent", "Task"})


class SdkClient(Protocol):
    """O pedaço do `ClaudeSDKClient` que o runtime usa; os testes passam um falso."""

    async def connect(self) -> None: ...
    async def query(self, prompt: str) -> None: ...
    def receive_response(self) -> AsyncIterator[Message]: ...
    async def disconnect(self) -> None: ...


ClientFactory = Callable[[ClaudeAgentOptions], SdkClient]


class AgentRuntimeError(Exception):
    """O SDK encerrou o turno com erro (sem login, limite de uso, falha da API)."""


@dataclass
class _Tokens:
    input: int = 0
    output: int = 0
    cache_read: int = 0
    cache_write_5m: int = 0
    cache_write_1h: int = 0

    def add(self, usage: dict[str, Any]) -> None:
        self.input += usage.get("input_tokens") or 0
        self.output += usage.get("output_tokens") or 0
        self.cache_read += usage.get("cache_read_input_tokens") or 0
        written = usage.get("cache_creation_input_tokens") or 0
        by_ttl = usage.get("cache_creation") or {}
        one_hour = by_ttl.get("ephemeral_1h_input_tokens") or 0
        self.cache_write_1h += one_hour
        self.cache_write_5m += written - one_hour

    def table_cost(self, model: str) -> float | None:
        return estimate_cost_usd(
            model,
            input_tokens=self.input,
            output_tokens=self.output,
            cache_read_tokens=self.cache_read,
            cache_write_5m_tokens=self.cache_write_5m,
            cache_write_1h_tokens=self.cache_write_1h,
        )


def tool_name(agent: Agent, tool: str) -> str:
    """Nome que o modelo vê: `mcp__<servidor>__<ferramenta>`, um servidor por agente."""
    return f"mcp__{agent}__{tool}"


class ClaudeAgentRuntime:
    def __init__(self, settings: Settings, client_factory: ClientFactory = ClaudeSDKClient) -> None:
        self._settings = settings
        self._client_factory = client_factory
        self._client: SdkClient | None = None
        self._host: AgentHost | None = None
        # Delegações deste turno: id da chamada a `Agent` -> especialista.
        self._delegations: dict[str, Specialist] = {}
        # Tokens deste turno por agente e modelo, e as mensagens já contadas.
        self._tokens: dict[tuple[Agent, str], _Tokens] = {}
        self._counted: set[str] = set()

    # --- AgentRuntime ------------------------------------------------------------

    async def respond(self, message: str, host: AgentHost) -> None:
        self._host = host
        self._delegations, self._tokens, self._counted = {}, {}, set()
        try:
            client = await self._connected()
            await client.query(message)
            async for sdk_message in client.receive_response():
                self._handle(sdk_message, host)
        finally:
            self._host = None

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.disconnect()
            self._client = None

    # --- mensagens do SDK -> AgentHost -------------------------------------------

    def _handle(self, message: Message, host: AgentHost) -> None:
        if isinstance(message, AssistantMessage):
            if message.error is not None or message.model == "<synthetic>":
                return  # aviso do próprio CLI (sem login, limite de uso), não fala da Recepção
            self._count(message)
            for block in message.content:
                if message.parent_tool_use_id is not None:
                    continue  # fala interna do especialista: não vai ao cliente
                if isinstance(block, TextBlock) and block.text.strip():
                    host.say(block.text)
                elif isinstance(block, ToolUseBlock) and block.name in DELEGATION_TOOLS:
                    specialist = block.input.get("subagent_type")
                    if specialist in SPECIALISTS:
                        self._delegations[block.id] = specialist
                        host.delegate(specialist, str(block.input.get("prompt", "")))
        elif isinstance(message, UserMessage) and isinstance(message.content, list):
            for block in message.content:
                if isinstance(block, ToolResultBlock) and block.tool_use_id in self._delegations:
                    specialist = self._delegations[block.tool_use_id]
                    host.specialist_returned(specialist, _text(block.content))
        elif isinstance(message, ResultMessage):
            self._record_usage(message, host)
            if message.is_error:
                raise AgentRuntimeError(
                    message.result or f"O SDK encerrou o turno com erro ({message.subtype})."
                )

    def _count(self, message: AssistantMessage) -> None:
        """Soma os tokens de uma resposta do modelo ao agente que a produziu.

        O SDK pode repetir a mesma resposta em várias mensagens, uma por bloco, todas com o
        mesmo `usage`; por isso cada `message_id` conta uma vez só.
        """
        if not message.usage:
            return
        if message.message_id is not None:
            if message.message_id in self._counted:
                return
            self._counted.add(message.message_id)
        parent = message.parent_tool_use_id
        agent: Agent = "reception" if parent is None else self._delegations.get(parent, "reception")
        self._tokens.setdefault((agent, message.model), _Tokens()).add(message.usage)

    def _record_usage(self, result: ResultMessage, host: AgentHost) -> None:
        """Registra os tokens do turno por agente e modelo, com o custo equivalente em API.

        Se o SDK informa o custo de um modelo, ele é o custo usado, dividido entre os agentes
        que usaram o modelo na proporção do cálculo pela tabela (ADR-0014, ADR-0019).
        """
        by_model = result.model_usage or {}
        for model, reported in by_model.items():
            if not any(m == model for _, m in self._tokens):
                # O SDK contou um modelo que não apareceu nas mensagens: atribui ao
                # especialista do turno, se houve um só, ou à Recepção.
                specialists = set(self._delegations.values())
                agent: Agent = specialists.pop() if len(specialists) == 1 else "reception"
                self._tokens[(agent, model)] = _Tokens(
                    input=reported.get("inputTokens", 0),
                    output=reported.get("outputTokens", 0),
                    cache_read=reported.get("cacheReadInputTokens", 0),
                    cache_write_5m=reported.get("cacheCreationInputTokens", 0),
                )
        for (agent, model), tokens in self._tokens.items():
            table_cost = tokens.table_cost(model)
            sdk_cost = (by_model.get(model) or {}).get("costUSD") or 0
            cost = table_cost
            if sdk_cost > 0:
                peers = [t for (_, m), t in self._tokens.items() if m == model]
                weights = [t.table_cost(model) or (t.input + t.output) for t in peers]
                own = table_cost or (tokens.input + tokens.output)
                cost = round(sdk_cost * own / sum(weights), 8) if sum(weights) else sdk_cost
            host.record_usage(
                Usage(
                    agent=agent,
                    model=model,
                    input_tokens=tokens.input,
                    output_tokens=tokens.output,
                    cache_read_tokens=tokens.cache_read,
                    cache_write_tokens=tokens.cache_write_5m + tokens.cache_write_1h,
                    cost_usd=cost,
                    price_table_cost_usd=table_cost,
                )
            )

    # --- sessão do SDK -----------------------------------------------------------

    async def _connected(self) -> SdkClient:
        if self._client is None:
            client = self._client_factory(self._options())
            await client.connect()
            self._client = client
        return self._client

    def _options(self) -> ClaudeAgentOptions:
        specs = [RECEPTION, *SPECIALISTS.values()]
        return ClaudeAgentOptions(
            system_prompt=RECEPTION.prompt,
            model=self._settings.reception_model,
            tools=["Agent"],
            allowed_tools=["Agent", *(tool_name(s.name, t) for s in specs for t in s.tools)],
            mcp_servers={s.name: create_sdk_mcp_server(s.name, tools=self._mcp_tools(s)) for s in specs},
            agents={
                name: AgentDefinition(
                    description=spec.description,
                    prompt=spec.prompt,
                    tools=[tool_name(name, t) for t in spec.tools],
                    model=self._settings.specialist_model,
                )
                for name, spec in SPECIALISTS.items()
            },
            setting_sources=[],
            strict_mcp_config=True,
            permission_mode="dontAsk",
            max_turns=self._settings.max_turns,
            hooks={"PreToolUse": [HookMatcher(hooks=[self._pre_tool_use])]},
        )

    async def _pre_tool_use(
        self, hook_input: HookInput, tool_use_id: str | None, context: HookContext
    ) -> HookJSONOutput:
        """Nega, antes de rodar, qualquer ferramenta fora do papel de quem chama (ADR-0013).

        `allowed_tools` vale para a sessão inteira, então sozinho deixaria a Recepção chamar
        as ferramentas de Pedidos; o hook sabe quem chama porque o SDK informa `agent_type`
        dentro de um subagente. Também barra delegar a subagentes que não são especialistas,
        como o `general-purpose` embutido no CLI.
        """
        data: dict[str, Any] = dict(hook_input)
        caller_type = data.get("agent_type")
        caller: Agent = caller_type if caller_type in SPECIALISTS else "reception"
        raw_name: str = data.get("tool_name", "")
        args: dict[str, Any] = data.get("tool_input") or {}
        tool, reason = raw_name, None
        if raw_name in DELEGATION_TOOLS:
            if caller != "reception":
                reason = "Só a Recepção delega."
            elif args.get("subagent_type") not in SPECIALISTS:
                reason = f"Não há especialista {args.get('subagent_type')!r}; use: {', '.join(SPECIALISTS)}."
        elif raw_name.startswith("mcp__") and raw_name.count("__") >= 2:
            _, owner, tool = raw_name.split("__", 2)
            if owner != caller or caller_type not in (None, *SPECIALISTS):
                reason = f"A ferramenta {tool} não pertence a este agente."
        else:
            reason = f"Ferramenta {raw_name} indisponível neste atendimento."
        if reason is None:
            return {}
        if self._host is not None:
            self._host.guardrail_blocked(caller, "agent_tools", tool, args, reason)
        return {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": reason,
            }
        }

    def _mcp_tools(self, spec: AgentSpec) -> list[SdkMcpTool[Any]]:
        return [self._mcp_tool(spec.name, name) for name in spec.tools]

    def _mcp_tool(self, agent: Agent, name: str) -> SdkMcpTool[Any]:
        spec = TOOLS[name]

        async def handler(args: dict[str, Any]) -> dict[str, Any]:
            if self._host is None:
                raise RuntimeError("Ferramenta chamada fora de um turno do atendimento.")
            result = self._host.call_tool(agent, name, args)
            return {
                "content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False)}],
                "is_error": "error" in result,
            }

        return SdkMcpTool(
            name=spec.name, description=spec.description, input_schema=spec.input_schema, handler=handler
        )


def _text(content: str | list[dict[str, Any]] | None) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    return "\n".join(part.get("text", "") for part in content if part.get("type") == "text")
