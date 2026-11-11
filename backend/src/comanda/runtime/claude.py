"""Runtime do Claude Agent SDK: a Recepção e os especialistas como agentes do Claude Code.

A Recepção é o agente principal da sessão do SDK e os especialistas são subagentes
chamados pela ferramenta `Agent` (ADR-0003). Cada agente tem um servidor MCP em processo
com só as ferramentas do seu papel (ADR-0013), e cada ferramenta executa pelo `AgentHost`
(ADR-0017): o runtime não vê repositório, cliente nem Rastro.

A sessão do SDK é isolada: sem ferramentas nativas além de `Agent`, sem configurações do
usuário ou do projeto, só os servidores MCP declarados aqui e sem pedidos de permissão.
"""

from __future__ import annotations

import contextlib
import json
import logging
import re
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from typing import Any, Protocol

from claude_agent_sdk import (
    AgentDefinition,
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    ClaudeSDKError,
    HookContext,
    HookInput,
    HookJSONOutput,
    HookMatcher,
    Message,
    RateLimitEvent,
    ResultMessage,
    SdkMcpTool,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
    create_sdk_mcp_server,
)

from ..agents import RECEPTION, SPECIALISTS, AgentSpec
from ..agents.roles import Agent, Specialist
from ..config import Settings
from ..tools import TOOLS
from .base import AgentHost, QuotaReading, Usage
from .pricing import estimate_cost_usd, price_for
from .trace import Actor

logger = logging.getLogger(__name__)

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

    @property
    def cache_write(self) -> int:
        return self.cache_write_5m + self.cache_write_1h

    def merge(self, other: _Tokens) -> None:
        self.input += other.input
        self.output += other.output
        self.cache_read += other.cache_read
        self.cache_write_5m += other.cache_write_5m
        self.cache_write_1h += other.cache_write_1h

    def table_cost(self, model: str) -> float | None:
        return estimate_cost_usd(
            model,
            input_tokens=self.input,
            output_tokens=self.output,
            cache_read_tokens=self.cache_read,
            cache_write_5m_tokens=self.cache_write_5m,
            cache_write_1h_tokens=self.cache_write_1h,
        )


@dataclass
class _Reported:
    """O que o SDK informou de um modelo neste turno: já a diferença para o turno anterior."""

    tokens: _Tokens
    cost_usd: float
    canonical: str | None


_USAGE_FIELDS = ("inputTokens", "outputTokens", "cacheReadInputTokens", "cacheCreationInputTokens", "costUSD")
# O CLI anexa ao resultado do subagente um rodapé de controle que não é do especialista.
_USAGE_FOOTER = re.compile(r"\s*<usage>.*?</usage>\s*$", re.DOTALL)
_AGENT_ID_FOOTER = re.compile(r"\s*agentId:[^\n]*\s*$")
_TOOLS_USED = re.compile(r"\s*<tools-used-before-summary>.*?</tools-used-before-summary>\s*", re.DOTALL)
_HAND_BACK = re.compile(r"\[Subagent hand-back\].*?The report follows:\n", re.DOTALL)


# Parâmetros da ferramenta `Agent` que a Recepção pode usar. `model`, `isolation`, `cwd`, `effort`
# e `name` deixariam o cliente escolher o modelo (custo) ou criar worktrees.
_DELEGATION_PARAMS = frozenset({"subagent_type", "prompt", "description", "run_in_background"})
_DATED_ID = re.compile(r"-\d{8}")


def _delegation_problem(args: dict[str, Any]) -> str | None:
    """Por que uma chamada a `Agent` não é uma delegação válida, ou `None` se é."""
    if args.get("subagent_type") not in SPECIALISTS:
        return f"Não há especialista {args.get('subagent_type')!r}; use: {', '.join(SPECIALISTS)}."
    if extra := sorted(set(args) - _DELEGATION_PARAMS):
        allowed = "subagent_type, prompt, description"
        return f"Parâmetros não permitidos em Agent: {', '.join(extra)}. Use só {allowed}."
    if args.get("run_in_background"):
        return "Delegue em primeiro plano: o especialista precisa devolver o resultado neste turno."
    return None


def _price_model(key: str, reported: _Reported | None) -> str:
    """O id do preço: a chave de `model_usage`, ou o `canonicalModel` se a chave não estiver na tabela."""
    if price_for(key) or reported is None or not reported.canonical:
        return key
    return reported.canonical


def _same_model(message_model: str, name: str) -> bool:
    """Mesmo modelo: ids iguais, ou um é o outro com a data (`-YYYYMMDD`) no fim."""
    if message_model == name:
        return True
    shorter, longer = sorted((message_model, name), key=len)
    return longer.startswith(shorter) and _DATED_ID.fullmatch(longer[len(shorter) :]) is not None


def _split(total: int, weights: list[float]) -> list[int]:
    """Reparte `total` em inteiros na proporção dos pesos, sem perder nem criar unidades."""
    if total <= 0:
        return [0] * len(weights)
    if sum(weights) <= 0:
        weights = [1.0] * len(weights)
    shares = [int(total * w / sum(weights)) for w in weights]
    shares[max(range(len(weights)), key=lambda i: weights[i])] += total - sum(shares)
    return shares


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
        # `model_usage` do SDK é o acumulado da conexão: guardo o último para tirar a diferença.
        self._usage_seen: dict[str, dict[str, Any]] = {}

    # --- AgentRuntime ------------------------------------------------------------

    async def respond(self, message: str, host: AgentHost) -> None:
        self._host = host
        self._delegations, self._tokens, self._counted = {}, {}, set()
        try:
            client = await self._connected()
            await client.query(message)
            async for sdk_message in client.receive_response():
                self._handle(sdk_message, host)
        except (ClaudeSDKError, OSError) as exc:
            # Cliente morto (CLI caiu, JSON inválido): descarta para o próximo turno reconectar.
            await self._discard_client()
            raise AgentRuntimeError(f"Falha na conexão com o Claude Code: {exc}") from exc
        except BaseException:
            # Cancelamento (timeout, Ctrl+C) ou turno que o SDK encerrou com erro: o CLI pode
            # ter um turno pendente ou ter saído, e reaproveitar o cliente responderia com
            # um turno de atraso. O turno seguinte reconecta.
            await self._discard_client()
            raise
        finally:
            self._host = None

    async def aclose(self) -> None:
        client, self._client = self._client, None
        self._usage_seen = {}
        if client is not None:
            await client.disconnect()

    async def _discard_client(self) -> None:
        # O cliente já falhou: só precisa deixar de ser reaproveitado.
        with contextlib.suppress(Exception):
            await self.aclose()

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
                elif (
                    isinstance(block, ToolUseBlock)
                    and block.name in DELEGATION_TOOLS
                    and _delegation_problem(block.input) is None  # senão o hook a nega
                ):
                    specialist: Specialist = block.input["subagent_type"]
                    self._delegations[block.id] = specialist
                    host.delegate(specialist, str(block.input.get("prompt", "")))
        elif isinstance(message, UserMessage) and isinstance(message.content, list):
            for block in message.content:
                if isinstance(block, ToolResultBlock) and block.tool_use_id in self._delegations:
                    if block.is_error:
                        continue  # a delegação falhou (limite, validação): não houve relatório
                    specialist = self._delegations[block.tool_use_id]
                    host.specialist_returned(specialist, _specialist_report(_text(block.content)))
        elif isinstance(message, RateLimitEvent):
            info = message.rate_limit_info
            host.record_quota(
                QuotaReading(
                    window=info.rate_limit_type or "unknown",
                    status=info.status,
                    utilization=info.utilization,
                    resets_at=info.resets_at,
                )
            )
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

    def _turn_usage(self, result: ResultMessage) -> dict[str, _Reported]:
        """Por modelo, o que o SDK contou neste turno: o acumulado menos o do turno anterior."""
        turn: dict[str, _Reported] = {}
        for key, current in (result.model_usage or {}).items():
            previous = self._usage_seen.get(key) or {}
            now = {f: current.get(f) or 0 for f in _USAGE_FIELDS}
            before = {f: previous.get(f) or 0 for f in _USAGE_FIELDS}
            self._usage_seen[key] = dict(now)
            # Se algum contador diminuiu, a conexão recomeçou: o valor atual já é só deste turno.
            restarted = any(now[f] < before[f] for f in _USAGE_FIELDS)
            delta = now if restarted else {f: now[f] - before[f] for f in _USAGE_FIELDS}
            if not any(delta.values()):
                continue  # o modelo não foi usado neste turno
            turn[key] = _Reported(
                tokens=_Tokens(
                    input=delta["inputTokens"],
                    output=delta["outputTokens"],
                    cache_read=delta["cacheReadInputTokens"],
                    cache_write_5m=delta["cacheCreationInputTokens"],
                ),
                cost_usd=round(delta["costUSD"], 8),
                canonical=current.get("canonicalModel"),
            )
        return turn

    @staticmethod
    def _usage_key(message_model: str, reported: dict[str, _Reported]) -> str:
        """A chave de `model_usage` do modelo de uma mensagem: igual, `canonicalModel` ou o id com data."""
        for key, rep in reported.items():
            names = [n for n in (key, rep.canonical) if n]
            if any(_same_model(message_model, n) for n in names):
                return key
        return message_model

    def _record_usage(self, result: ResultMessage, host: AgentHost) -> None:
        """Registra os tokens do turno por agente e modelo, com o custo equivalente em API.

        O total de cada modelo é o que o SDK contou no turno. As mensagens dizem quem usou:
        o que elas somam fica com cada agente, e o que faltou (a resposta final de texto de
        um especialista, os `output_tokens` finais) é repartido entre os agentes do modelo
        na proporção do que já tinham. Se o SDK informa o custo, ele é o custo usado,
        dividido na proporção do cálculo pela tabela (ADR-0014, ADR-0019).
        """
        reported = self._turn_usage(result)
        by_key: dict[str, dict[Agent, _Tokens]] = {}
        for (agent, message_model), tokens in self._tokens.items():
            key = self._usage_key(message_model, reported)
            by_key.setdefault(key, {}).setdefault(agent, _Tokens()).merge(tokens)
        for key, rep in reported.items():
            agents = by_key.setdefault(key, {})
            if not agents:
                # O SDK contou um modelo que não apareceu nas mensagens: atribui ao
                # especialista do turno, se houve um só, ou à Recepção.
                specialists = set(self._delegations.values())
                owner: Agent = specialists.pop() if len(specialists) == 1 else "reception"
                agents[owner] = _Tokens()
            self._share_missing(agents, rep.tokens, _price_model(key, rep))
        for key, agents in by_key.items():
            rep = reported.get(key)
            price_model = _price_model(key, rep)
            tables = {agent: tokens.table_cost(price_model) for agent, tokens in agents.items()}
            weights = {a: tables[a] or (t.input + t.output) for a, t in agents.items()}
            for agent, tokens in agents.items():
                cost = tables[agent]
                if rep is not None and rep.cost_usd > 0:
                    total = sum(weights.values())
                    share = weights[agent] / total if total else 1 / len(agents)
                    cost = round(rep.cost_usd * share, 8)
                host.record_usage(
                    Usage(
                        agent=agent,
                        model=key,
                        input_tokens=tokens.input,
                        output_tokens=tokens.output,
                        cache_read_tokens=tokens.cache_read,
                        cache_write_tokens=tokens.cache_write,
                        cost_usd=cost,
                        price_table_cost_usd=tables[agent],
                    )
                )

    @staticmethod
    def _share_missing(agents: dict[Agent, _Tokens], total: _Tokens, model: str) -> None:
        """Soma aos agentes o que o SDK contou e as mensagens não mostraram."""
        held = list(agents.values())
        weights = [t.table_cost(model) or (t.input + t.output) for t in held]
        missing = {
            "input": total.input - sum(t.input for t in held),
            "output": total.output - sum(t.output for t in held),
            "cache_read": total.cache_read - sum(t.cache_read for t in held),
            "cache_write_5m": total.cache_write - sum(t.cache_write for t in held),
        }
        for field, amount in missing.items():
            for tokens, share in zip(held, _split(amount, weights), strict=True):
                setattr(tokens, field, getattr(tokens, field) + share)

    # --- sessão do SDK -----------------------------------------------------------

    async def _connected(self) -> SdkClient:
        if self._client is None:
            client = self._client_factory(self._options())
            await client.connect()
            self._client = client
            self._usage_seen = {}  # conexão nova: os contadores do SDK recomeçam do zero
        return self._client

    def _options(self) -> ClaudeAgentOptions:
        specs = [RECEPTION, *SPECIALISTS.values()]
        return ClaudeAgentOptions(
            system_prompt=RECEPTION.prompt,
            # O CLI expande `@caminho` e executa `/comando` no texto do cliente, sem passar pelo
            # hook; com isto a fala chega ao modelo como foi escrita (ADR-0008, ADR-0009).
            verbatim_prompts=True,
            # No CLI 2.1 omitir `run_in_background` em `Agent` lança o especialista em segundo
            # plano; o resultado só chegaria depois do fim do turno. O hook também o nega.
            env={"CLAUDE_CODE_DISABLE_BACKGROUND_TASKS": "1"},
            # Sem as falas dos subagentes, o consumo de cada especialista fica incompleto.
            forward_subagent_text=True,
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
            # `on_failure="block"`: se o hook falhar, o CLI nega em vez de seguir a permissão normal.
            hooks={"PreToolUse": [HookMatcher(hooks=[self._pre_tool_use], on_failure="block")]},
        )

    async def _pre_tool_use(
        self, hook_input: HookInput, tool_use_id: str | None, context: HookContext
    ) -> HookJSONOutput:
        """Nega, antes de rodar, qualquer ferramenta fora do papel de quem chama (ADR-0013).

        `allowed_tools` vale para a sessão inteira, então sozinho deixaria a Recepção chamar
        as ferramentas de Pedidos; o hook sabe quem chama porque o SDK informa `agent_type`
        dentro de um subagente. Também barra delegar a subagentes que não são especialistas,
        como o `general-purpose` embutido no CLI. Uma ferramenta dentro do papel ainda passa
        pelos guardrails do Atendimento, como o de elegibilidade antes do reembolso (ADR-0008).
        """
        data: dict[str, Any] = dict(hook_input)
        caller_type = data.get("agent_type")
        raw_name: str = data.get("tool_name", "")
        args: dict[str, Any] = data.get("tool_input") or {}
        caller: Actor
        if caller_type is None:
            caller = "reception"
        elif caller_type in SPECIALISTS:
            caller = caller_type
        else:
            caller = "system"  # subagente que não é nosso: não é a Recepção nem um especialista
        tool, reason = raw_name, None
        own_tool_of: Agent | None = None
        if caller == "system":
            reason = f"O agente {caller_type!r} não é um agente do atendimento e não tem ferramentas."
        elif raw_name in DELEGATION_TOOLS:
            reason = "Só a Recepção delega." if caller != "reception" else _delegation_problem(args)
        elif raw_name.startswith("mcp__") and raw_name.count("__") >= 2:
            _, owner, tool = raw_name.split("__", 2)
            if owner != caller:
                reason = f"A ferramenta {tool} não pertence a este agente."
            else:
                own_tool_of = caller
        else:
            reason = f"Ferramenta {raw_name} indisponível neste atendimento."
        try:
            if self._host is not None:
                if reason is not None:
                    self._host.guardrail_blocked(caller, "agent_tools", tool, args, reason)
                elif own_tool_of is not None:
                    # Ferramenta do próprio papel: os guardrails do Atendimento decidem e registram.
                    reason = self._host.authorize(own_tool_of, tool, args)
        except Exception:
            # Gravar a violação ou consultar o guardrail falhou (arquivo travado, disco cheio):
            # a negação vale do mesmo jeito, e a ferramenta não roda sem a checagem.
            logger.exception("Não consegui checar ou registrar o guardrail de %s", tool)
            reason = reason or "Não foi possível verificar esta ferramenta agora."
        if reason is None:
            return {}
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


def _specialist_report(text: str) -> str:
    """O relatório do especialista, sem o que o CLI acrescenta ao devolvê-lo à Recepção.

    O CLI 2.1 antepõe a lista de ferramentas e um aviso de autoridade, indenta o relatório em
    duas colunas e acrescenta `agentId` e `<usage>`. Nada disso é fala do especialista, e iria
    ao Rastro, ao SSE e ao replay público (ADR-0010, ADR-0012).
    """
    text = _TOOLS_USED.sub("", text)
    if (frame := _HAND_BACK.search(text)) is not None:
        report = text[frame.end() :]
        text = "\n".join(line.removeprefix("  ") for line in report.split("\n"))
    return _AGENT_ID_FOOTER.sub("", _USAGE_FOOTER.sub("", text)).strip()


def _text(content: str | list[dict[str, Any]] | None) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    return "\n".join(part.get("text", "") for part in content if part.get("type") == "text")
