# Comanda

Sistema de agentes que atende clientes de um app de delivery: entende o problema relatado, consulta o pedido, aplica as regras de reembolso e encaminha a um humano quando necessário. Os termos abaixo são os nomes canônicos; entre crases, o nome usado no código.

## Pessoas

**Cliente** (`Customer`):
Pessoa que fez pedidos no app e conversa com o sistema para resolver um problema.
_Avoid_: usuário, consumidor, comprador

**Atendente humano** (`human`):
Pessoa da operação que decide os reembolsos pendentes e recebe os tickets.
_Avoid_: operador, suporte, moderador

## Agentes

**Recepção** (`reception`):
O agente que conversa com o cliente, entende o problema e delega aos especialistas.
_Avoid_: agente de atendimento, triagem, orquestrador

**Especialista** (`specialist`):
Agente que resolve uma parte do caso para a Recepção e nunca fala com o cliente; há dois: Pedidos e Reembolsos.
_Avoid_: subagente, worker

## Pedidos

**Pedido** (`Order`):
Compra de um cliente em um restaurante, com itens, taxa de entrega, horário previsto e status.
_Avoid_: compra, encomenda

**Item do pedido** (`OrderItem`):
Uma linha do pedido: um produto do cardápio, com quantidade e preço pago.
_Avoid_: produto (o produto é o item do cardápio)

**Horário previsto** (`eta`):
Momento em que o pedido deveria chegar, prometido ao cliente; referência para medir atraso.
_Avoid_: prazo, previsão

**Restaurante** (`Restaurant`):
Estabelecimento que prepara o pedido e mantém o cardápio e a lista de alérgenos.
_Avoid_: loja, parceiro

## Atendimento

**Atendimento** (`session`):
Uma conversa do cliente com o sistema, do primeiro contato até a resolução ou o encaminhamento.
_Avoid_: chat, conversa, sessão

**Tipo de problema** (`IssueType`):
A categoria do que o cliente relata: atraso, item faltando, item errado, qualidade ou pedido não entregue.
_Avoid_: reclamação, motivo

**Reembolso** (`Refund`):
Devolução de dinheiro ligada a um pedido, com valor calculado por uma regra.
_Avoid_: estorno, devolução, crédito

**Reembolso pendente** (`pending_approval`):
Reembolso registrado que só é pago depois da decisão de um atendente humano.
_Avoid_: reembolso em análise

**Regra** (`rule_id`):
Uma decisão de reembolso implementada em código, identificada por um código como `R-LATE-60`.
_Avoid_: política (política é o texto)

**Política** (`policies/politicas.md`):
O texto oficial de atendimento que o sistema consulta para responder dúvidas; as regras o implementam.
_Avoid_: regra, norma

**Ticket** (`Ticket`):
Registro de um caso encaminhado a um atendente humano, com resumo e prioridade.
_Avoid_: chamado, protocolo

**Escalonamento** (`escalate_to_human`):
O ato de abrir um ticket e passar o caso a um atendente humano.
_Avoid_: transferência, handoff

**Fila** (`queue`):
Os reembolsos pendentes e os tickets abertos que aguardam um atendente humano.
_Avoid_: backlog, caixa de entrada, pendências

**Guardrail** (`guardrail`):
Limite aplicado fora do modelo que impede um agente de agir fora do processo, seja qual for o pedido do cliente.
_Avoid_: trava, filtro, proteção

**Rastro** (`trace`):
O registro, em ordem, de cada passo de um atendimento: qual agente agiu, qual ferramenta chamou, com que dados e resultado.
_Avoid_: log, histórico

## Avaliação e demonstração

**Cenário** (`scenario`):
Situação de atendimento com cliente, pedido e resultado esperado, usada para avaliar o sistema.
_Avoid_: caso de teste, exemplo

**Cliente simulado** (`simulated customer`):
Modelo de linguagem que interpreta o cliente de um cenário e conversa com o sistema.
_Avoid_: usuário simulado, bot de teste

**Juiz** (`judge`):
Modelo de linguagem que avalia o que o sistema disse ao cliente; o resultado no banco é conferido por código.
_Avoid_: avaliador, grader

**Violação** (`violation`):
Algo que o sistema disse ou tentou fazer contra a política ou contra um guardrail durante um atendimento.
_Avoid_: erro, falha, infração

**Painel** (`web`):
Interface web que mostra o atendimento ao lado do rastro.
_Avoid_: dashboard, frontend

**Replay**:
Reprodução, no painel, de um atendimento gravado, sem chamar nenhum modelo.
_Avoid_: demo gravada, playback
