# Ferramentas de cada agente isoladas por servidor MCP e por hook

No runtime do Claude Agent SDK, cada agente tem um servidor MCP em processo com só as ferramentas do seu papel (ADR-0013), então os nomes que o modelo vê dizem o dono (`mcp__orders__get_order_details`), e cada ferramenta chega ao Atendimento já atribuída ao agente certo. Isso não basta sozinho: `allowed_tools` vale para a sessão inteira do SDK, e o `tools` de um subagente não impede a Recepção de chamar as ferramentas dele. Por isso um hook `PreToolUse` nega, antes de rodar, qualquer ferramenta cujo servidor não seja o do agente que chama (o SDK informa `agent_type` dentro de um subagente), qualquer delegação a um subagente que não seja especialista, como o `general-purpose` embutido no CLI, e qualquer ferramenta nativa além de `Agent`. Cada negação vira um evento `guardrail_blocked` no Rastro, pela interface `AgentHost` (ADR-0017), para que a avaliação conte como violação um agente que tenta sair do papel.

Um servidor só, com a atribuição feita pelo hook, foi descartado: o handler MCP não recebe o id da chamada nem o agente, então a ligação entre o hook e a execução dependeria da ordem das mensagens do CLI.

Este ADR segue o ADR-0013 onde ele diverge do ADR-0003. O ADR-0003 diz que o escalonamento está disponível para todos; o ADR-0013 dá `escalate_to_human` à Recepção e a Reembolsos, não a Pedidos, que só consulta e devolve o resultado à Recepção, que escalona. Pedidos tentar escalonar é, portanto, um bloqueio no Rastro.

## Revisão após a revisão do PR #17

A checagem do ADR-0013 (a ferramenta está na lista do agente) passou para `Session.call_tool`, a tabela `AGENT_TOOLS` em `comanda/agents/roles.py`. Assim o ADR-0017 vale para os dois runtimes: um agente roteirizado que chama uma ferramenta de outro também vira `guardrail_blocked`, sem `tool_call`. O hook do adaptador do Claude ficou com o que só ele sabe: quem chama (`agent_type`) e se o servidor MCP da ferramenta é o dele, porque o handler MCP não sabe quem chamou (decisão acima). Ele também nega `Agent` com `run_in_background`, que deixaria o especialista rodar depois do fim do turno, e a delegação em segundo plano não é registrada como `delegation`.

Um `agent_type` que não é a Recepção nem um especialista nosso (o `general-purpose` do CLI, por exemplo) não tem ferramenta nenhuma e a violação é registrada com agente `system`, e não na conta da Recepção; o Rastro já aceita `system` como autor.

A fala do cliente segue ao modelo com `verbatim_prompts=True`: sem isso o CLI expande `@caminho` e executa `/comando` no texto do cliente antes de qualquer hook, o que deixaria o cliente ler o banco do atendimento (ADR-0008, ADR-0009).

Não dá para ligar o servidor MCP de Pedidos só ao subagente: o SDK registra servidores em processo só em `mcp_servers`, no nível da sessão, e `AgentDefinition.mcpServers` não carrega a instância. A Recepção continua recebendo os schemas das ferramentas de Pedidos em toda chamada (mais tokens de entrada) e uma tentativa de usá-las vira violação leve, negada pelo hook (PRD 48). Fica registrado como custo conhecido; reavaliar se o SDK passar a aceitar servidor em processo por subagente.

## Segunda revisão do PR #17

- **Delegação:** o hook aceita em `Agent` só `subagent_type`, `prompt` e `description` (e `run_in_background` falso). `model`, `isolation`, `cwd`, `effort` e `name` são negados: o cliente poderia pedir "use o opus" ou criar worktrees. No CLI 2.1 omitir `run_in_background` lança o subagente em segundo plano; na rodada ao vivo a Recepção passava `run_in_background: false` por conta própria, porque a descrição da ferramenta manda, mas isso é comportamento do modelo e não garantia. O runtime passa `CLAUDE_CODE_DISABLE_BACKGROUND_TASKS=1` ao CLI, e o hook ainda nega `run_in_background` verdadeiro. A mesma regra de delegação válida decide o hook e o registro de `delegation`.
- **Hook que falha:** o `HookMatcher` tem `on_failure="block"`, e o hook devolve a negação mesmo que gravar o `guardrail_blocked` falhe (o erro vai para o log). Sem isso, um hook que levanta exceção deixaria o CLI seguir a permissão normal (`allowed_tools` com `dontAsk`).
- **Cliente do SDK:** o runtime descarta a conexão em qualquer saída que não seja o fim normal de um turno: cancelamento (timeout da avaliação ou da API, Ctrl+C), exceção, erro de transporte e resultado com `is_error`. Reaproveitá-la responderia com um turno de atraso. O custo é que o turno seguinte começa uma conexão nova, sem o histórico da conversa; retomar com `resume` fica para depois.
- **Resultado de delegação com erro** (limite de subagentes, validação do CLI) não vira `specialist_returned`: não houve relatório.
