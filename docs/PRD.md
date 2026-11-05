# PRD: Comanda

> **English summary.** Comanda is a multi-agent customer support system for a food delivery app. A reception agent talks to the customer in Portuguese and delegates to two specialist agents (Orders and Refunds) built on the Claude Agent SDK. Refund amounts are always computed by coded rules, never by the model; guardrails are enforced in code; every step is recorded in a JSONL trace. A τ-bench-style evaluation (30 scenarios, 8 adversarial) checks the final database state by code and uses an LLM judge for the conversation, reporting resolution rate, violations, API-equivalent cost and latency per session, and pass^3. A Next.js panel shows the live chat next to the trace, a human approval queue, and a public replay of recorded sessions.

## Problem Statement

Um app de delivery recebe todos os dias um volume grande de contatos com o mesmo formato: "meu pedido atrasou", "veio sem o refrigerante", "a pizza chegou fria", "o pedido não chegou". Cada contato atendido por uma pessoa custa dinheiro e tempo, e o cliente espera. Um chatbot comum resolve parte disso, mas cria dois riscos que a operação não aceita: dar reembolso indevido porque o cliente insistiu ou manipulou o modelo, e prometer ao cliente algo que a política não prevê.

Do lado de quem constrói, falta uma forma confiável de saber se o sistema de atendimento funciona: quantos casos ele resolve sem humano, quanto custa cada atendimento, quantas vezes ele violou a política e se ele se comporta igual ao repetir o mesmo caso. Sem essa medição, cada mudança de prompt ou de modelo é um palpite.

O projeto também é portfólio para a trilha AI Engineer do estágio GenAI do iFood: precisa demonstrar orquestração de agentes, regras de negócio em código, guardrails, observabilidade e avaliação automática, e precisa ser explicável pelo autor numa entrevista.

## Solution

O Comanda atende o cliente com três agentes. A **Recepção** conversa com o cliente em português, entende o problema e delega a dois **especialistas**: **Pedidos**, que consulta pedido, entrega e restaurante, e **Reembolsos**, que consulta a elegibilidade e registra o reembolso. Os especialistas nunca falam com o cliente.

O modelo decide o caminho da conversa, mas o dinheiro é decidido por **regras** em código: o valor do reembolso e a necessidade de aprovação humana saem sempre da regra, a partir dos dados do pedido. Reembolsos acima do limite automático entram na **Fila**, onde um **atendente humano** aprova ou recusa com um motivo, pelo painel ou pela CLI. Casos fora do alcance dos agentes viram **tickets** por **escalonamento**.

**Guardrails** aplicados em código protegem o processo mesmo que o modelo seja manipulado: o cliente é fixo no atendimento, nenhum reembolso é registrado sem consulta de elegibilidade antes, e dados pessoais são mascarados no **rastro**. O rastro registra cada passo do atendimento num formato próprio e alimenta o painel ao vivo, o **replay** público e as métricas.

A **avaliação** roda 30 **cenários** com um **cliente simulado**, confere o estado final do banco por código e usa um **juiz** para apontar **violações** na fala. O resultado sai em números: taxa de resolução, violações, custo equivalente em API e latência por atendimento, e pass^3.

O **painel** mostra o chat ao lado do rastro ao vivo e a Fila do atendente humano. A versão pública é um replay de atendimentos gravados, sem chamar nenhum modelo.

## User Stories

### Cliente

1. Como Cliente, quero relatar um atraso em linguagem natural, para receber o reembolso que a política prevê sem precisar conhecer as regras.
2. Como Cliente, quero relatar um item faltando e dizer qual item foi, para ser reembolsado pelo valor pago naquele item.
3. Como Cliente, quero relatar um item errado, para ser reembolsado pelo item que não recebi como pedi.
4. Como Cliente, quero relatar um problema de qualidade (comida fria, estragada, derramada), para receber a compensação parcial prevista.
5. Como Cliente, quero relatar que o pedido não chegou, para receber o reembolso integral quando a entrega de fato falhou.
6. Como Cliente, quero perguntar sobre um pedido cancelado, para saber se e quanto vou receber de volta.
7. Como Cliente, quero relatar dois problemas na mesma conversa (atraso e item faltando), para não precisar abrir dois atendimentos.
8. Como Cliente, quero que o sistema identifique sozinho meu pedido mais recente quando eu não souber o número, para não precisar procurar códigos.
9. Como Cliente, quero escolher entre meus pedidos recentes quando o sistema não souber de qual estou falando, para resolver o pedido certo.
10. Como Cliente, quero saber onde está meu pedido em andamento, para decidir se espero ou reclamo.
11. Como Cliente, quero perguntar sobre a política (prazo para reclamar, como funciona o reembolso, privacidade), para entender meus direitos sem abrir um caso.
12. Como Cliente, quero perguntar se um prato do meu pedido contém um alérgeno, para decidir se posso comer.
13. Como Cliente com alergia grave, quero ser lembrado de confirmar direto com o restaurante, porque a lista de alérgenos é informada pelo restaurante.
14. Como Cliente que relata reação alérgica ou risco à saúde, quero ser encaminhado a um atendente humano imediatamente, para ter atenção de uma pessoa.
15. Como Cliente, quero pedir para falar com um humano e ser atendido nesse pedido, para não ficar preso ao robô.
16. Como Cliente, quero ouvir o valor exato do reembolso em reais, para saber quanto vou receber.
17. Como Cliente com reembolso acima do limite automático, quero saber o valor já definido e que um atendente vai confirmar, para não ficar sem resposta.
18. Como Cliente com reembolso negado, quero ouvir o motivo em linguagem simples, para entender por que não tenho direito.
19. Como Cliente que contesta uma negativa pela segunda vez, quero ser encaminhado a um atendente humano, para ter uma segunda opinião.
20. Como Cliente, quero que o sistema nunca me peça senha, número de cartão ou código de verificação, para confiar que a conversa é segura.
21. Como Cliente, quero ser atendido em português natural e cordial, para sentir que estou falando com um atendimento de verdade.
22. Como Cliente, quero respostas rápidas, para não abandonar a conversa.
23. Como Cliente que pede algo fora do escopo (endereço, pagamento, cupom, cancelar pedido em andamento), quero ser informado de que isso não é tratado ali e ser encaminhado, para não perder tempo.
24. Como Cliente, quero que o mesmo item não seja reembolsado duas vezes, para que o sistema seja justo também com a operação.

### Atendente humano

25. Como Atendente humano, quero ver a Fila de reembolsos pendentes com cliente, pedido, regra aplicada e valor, para decidir rápido.
26. Como Atendente humano, quero ver o resumo da conversa e o rastro de um reembolso pendente, para entender o caso antes de decidir.
27. Como Atendente humano, quero aprovar um reembolso pendente, para que ele seja pago.
28. Como Atendente humano, quero recusar um reembolso pendente com um motivo obrigatório, para registrar por que ele não foi pago.
29. Como Atendente humano, quero que não seja possível alterar o valor de um reembolso, para que o valor continue saindo sempre da regra.
30. Como Atendente humano, quero ver os tickets abertos com resumo, prioridade e cliente, para atender os casos escalonados.
31. Como Atendente humano, quero marcar um ticket como resolvido, para que a Fila mostre só o que falta.
32. Como Atendente humano, quero usar a Fila pela CLI, para trabalhar sem o painel.
33. Como Atendente humano, quero usar a Fila no painel, para ver casos e rastros com conforto.
34. Como Atendente humano, quero que um reembolso já decidido não possa ser decidido de novo, para evitar pagamento em dobro.

### Desenvolvedor

35. Como Desenvolvedor, quero conversar com o sistema pelo terminal escolhendo o cliente, para testar mudanças rapidamente.
36. Como Desenvolvedor, quero recriar o banco a partir do seed determinístico, para partir sempre do mesmo estado.
37. Como Desenvolvedor, quero que o "agora" do sistema seja fixo, para que as regras de atraso e prazo deem sempre o mesmo resultado.
38. Como Desenvolvedor, quero um rastro por atendimento com cada mensagem, delegação, chamada de ferramenta, resultado, bloqueio de guardrail, tokens e custo, para entender exatamente o que aconteceu.
39. Como Desenvolvedor, quero que o rastro nunca guarde nome, telefone ou endereço reais, para poder mostrá-lo publicamente.
40. Como Desenvolvedor, quero ver no rastro qual agente agiu em cada passo, para depurar a delegação.
41. Como Desenvolvedor, quero que o rastro tenha um esquema versionado, para que mudanças futuras não quebrem replays antigos sem aviso.
42. Como Desenvolvedor, quero rodar a avaliação completa com um comando, para medir o sistema depois de cada mudança.
43. Como Desenvolvedor, quero rodar a avaliação num subconjunto de cenários, para iterar rápido sem gastar cota.
44. Como Desenvolvedor, quero que cada cenário declare cliente, objetivo do cliente simulado e estado final esperado, para que o critério de sucesso seja explícito.
45. Como Desenvolvedor, quero cenários adversariais (pedido de outro cliente, manipulação do agente, mentira sobre o atraso), para provar que os guardrails funcionam.
46. Como Desenvolvedor, quero que o estado final do banco seja conferido por código, para que o resultado não dependa da opinião de um modelo.
47. Como Desenvolvedor, quero que o juiz aponte violações graves na fala, para reprovar um agente que acerta o banco mas engana o cliente.
48. Como Desenvolvedor, quero que bloqueios de guardrail seguidos de correção apareçam como violações leves sem reprovar o cenário, para medir o processo sem punir o guardrail funcionando.
49. Como Desenvolvedor, quero a taxa de resolução sem humano, para medir o principal ganho de negócio.
50. Como Desenvolvedor, quero o custo equivalente em API por atendimento, com tokens por modelo, para comparar configurações de modelos.
51. Como Desenvolvedor, quero a latência por atendimento, para saber se a experiência é aceitável.
52. Como Desenvolvedor, quero pass^3 em 10 cenários, para medir a consistência do sistema ao repetir o mesmo caso.
53. Como Desenvolvedor, quero que cada resultado de avaliação seja versionado com data, modelos e commit, para comparar rodadas ao longo do tempo.
54. Como Desenvolvedor, quero saber quanto da cota do Claude Pro uma rodada de avaliação consome, para planejar quantas rodadas cabem até a entrega.
55. Como Desenvolvedor, quero trocar o provedor do cliente simulado e do juiz entre Gemini e Ollama, para não depender de um só fornecedor.
56. Como Desenvolvedor, quero trocar os modelos de cada papel por variável de ambiente, para testar Haiku e Sonnet em posições diferentes.
57. Como Desenvolvedor, quero testes automáticos que rodem sem nenhum modelo de linguagem, para que o CI seja rápido, gratuito e determinístico.
58. Como Desenvolvedor, quero um runtime de agentes roteirizado, para testar guardrails, rastro e avaliação sem chamar o Claude.
59. Como Desenvolvedor, quero um CI no GitHub Actions com lint, checagem de tipos e testes do backend e do painel, para manter o projeto saudável.
60. Como Desenvolvedor, quero exportar atendimentos escolhidos para o replay, para controlar o que fica público.
61. Como Desenvolvedor, quero templates de issue e de pull request, para manter o histórico do projeto legível.
62. Como Desenvolvedor, quero publicar a release v0.1.0 com o resultado da avaliação, para marcar a versão entregue.

### Visitante do portfólio (recrutador)

63. Como recrutador, quero abrir o replay público e ver um atendimento real ao lado do rastro, para entender o sistema sem instalar nada.
64. Como recrutador, quero ver no replay a delegação entre Recepção e especialistas, para entender a orquestração.
65. Como recrutador, quero ver no replay um guardrail bloqueando uma ação, para entender como o sistema se protege.
66. Como recrutador, quero ler no README a arquitetura e os números da avaliação, para avaliar o resultado em poucos minutos.
67. Como recrutador, quero clonar o repositório e rodar ao vivo com minha própria chave, para verificar que o sistema funciona de verdade.

## Implementation Decisions

### Domínio e dados

- O domínio existente é mantido como ponto de partida: Cliente, Restaurante com cardápio e alérgenos, Pedido com itens, Horário previsto e status (em preparo, saiu para entrega, entregue, cancelado), Reembolso com status (aprovado, pendente, recusado) e Ticket.
- Valores monetários são inteiros em centavos em todo o sistema.
- A base é SQLite recriada por um seed determinístico, com o "agora" fixo em 5/11/2026 19h30 (ADR-0006). O seed cobre um cliente por cenário de avaliação; os clientes atuais C001 a C008 crescem para cobrir os 30 cenários.
- O Reembolso ganha o registro da decisão humana: quem decidiu, quando e o motivo da recusa. Um reembolso só sai de pendente uma vez.
- O Ticket ganha status (aberto, resolvido).

### Regras de reembolso

- As regras atuais são mantidas, cada uma com um código: atraso de 30 a 59 minutos devolve a taxa de entrega; atraso de 60 minutos ou mais devolve taxa mais 25% dos itens; item faltando ou errado devolve o valor do item; qualidade devolve uma fração do item; pedido cancelado ou perdido devolve o total; reclamações valem até 48 horas após a entrega; um item não é reembolsado duas vezes; o total reembolsado nunca passa do total do pedido.
- Reembolsos acima do limite automático (R$ 80,00 por padrão, configurável) e clientes com reembolsos frequentes exigem aprovação humana.
- O valor e a necessidade de aprovação saem só da regra; a ferramenta de registrar reembolso não aceita valor e recalcula a regra na execução (ADR-0001).
- Um atendimento pode tratar vários tipos de problema; cada um passa pela regra separadamente.

### Política e busca

- A Política é um texto oficial consultado por busca BM25 por seções. A busca passa a reduzir as palavras ao radical em português, para que "atrasou" encontre "atraso".

### Ferramentas

- Oito ferramentas independentes de framework, todas funções sobre um contexto com o cliente fixo do atendimento: listar meus pedidos, detalhes do pedido, rastrear entrega, informações do restaurante (com alérgenos), buscar na política, consultar elegibilidade de reembolso, registrar reembolso e escalonar.
- Nenhuma ferramenta aceita identificador de cliente; todo pedido consultado passa por checagem de dono (ADR-0008, ADR-0009).
- As ferramentas são expostas aos agentes por um servidor MCP em processo do Claude Agent SDK.
- A Fila (listar pendentes e tickets, aprovar, recusar com motivo, resolver ticket) é um conjunto de operações de domínio usado pela CLI e pela API, não uma ferramenta dos agentes.

### Agentes (ADR-0003, ADR-0013)

- Recepção (Haiku por padrão): conversa com o cliente, tem buscar na política e escalonar, e delega aos especialistas.
- Pedidos (Sonnet por padrão): listar pedidos, detalhes, rastrear entrega, informações do restaurante.
- Reembolsos (Sonnet por padrão): consultar elegibilidade, registrar reembolso, escalonar.
- Os especialistas são subagentes do SDK chamados pela Recepção e nunca falam com o cliente.
- A sessão do SDK é isolada: só as ferramentas declaradas, sem configurações do usuário, sem servidores MCP externos e sem pedidos de permissão.
- Escalonamento é obrigatório quando o cliente pede um humano, relata reação alérgica ou risco à saúde, contesta uma negativa pela segunda vez ou traz um problema fora do escopo. Escalonar fora desses casos conta como violação leve.
- Num reembolso pendente, a Recepção informa o valor e que um atendente vai confirmar; não há aviso posterior ao cliente.

### Runtime de agentes

- O atendimento depende de uma interface de runtime de agentes com dois adaptadores: o Claude Agent SDK e um runtime roteirizado, que reproduz uma sequência fixa de delegações, chamadas de ferramenta e falas sem chamar modelo. O roteirizado viabiliza testar tudo acima das ferramentas no CI.

### Atendimento

- Interface principal do sistema: iniciar um atendimento para um cliente com um runtime, enviar uma fala do cliente, receber as falas do sistema e os eventos do rastro, e encerrar. CLI, API e avaliação usam só essa interface.

### Guardrails (ADR-0008)

- Cliente fixo por atendimento, garantido pelo contexto das ferramentas.
- Um hook antes de cada ferramenta nega o registro de reembolso quando não houve consulta de elegibilidade para o mesmo pedido e tipo de problema no mesmo atendimento. O bloqueio volta ao agente como resultado negado e é gravado no rastro.
- Nome, telefone e endereço do cliente são mascarados no momento da gravação do rastro.

### Rastro (ADR-0010)

- Um arquivo JSONL por atendimento, um evento por linha, com versão de esquema, identificador do atendimento, horário e agente.
- Tipos de evento: início do atendimento, fala do cliente, fala do sistema, delegação a especialista, retorno do especialista, chamada de ferramenta, resultado de ferramenta, bloqueio de guardrail, escalonamento, uso de tokens e custo, fim do atendimento.
- O mesmo formato alimenta o SSE do painel, o replay e as métricas.

### Custo (ADR-0014)

- O custo por atendimento é reportado como custo equivalente em API: tokens de entrada e saída por modelo multiplicados pela tabela pública de preços, rotulado como estimativa. Se o SDK informar um custo estimado, ele é usado e conferido contra esse cálculo.

### Provedores do cliente simulado e do juiz (ADR-0004)

- Uma interface de provedor com três adaptadores: Gemini (camada gratuita), Ollama (local) e roteirizado (testes).

### Avaliação (ADR-0011, ADR-0015)

- 30 cenários em YAML, 8 adversariais. Cada cenário declara cliente, objetivo e personalidade do cliente simulado, estado final esperado do banco e se é adversarial.
- Um cenário passa quando o estado final confere e o juiz não aponta violação grave (prometer reembolso, valor ou prazo fora da política, revelar dado de outro cliente). Bloqueios de guardrail corrigidos e escalonamentos desnecessários são violações leves.
- Métricas por rodada: taxa de resolução sem humano, violações graves e leves, custo e latência por atendimento, pass^3 em 10 cenários.
- A avaliação completa roda localmente; existe um modo de subconjunto. Cada resultado é versionado com data, modelos e commit. A primeira rodada mede o consumo de cota do Claude Pro.

### CLI

- Comandos: recriar o banco, conversar escolhendo o cliente, Fila (listar, aprovar, recusar com motivo, resolver ticket), rodar avaliação (completa ou subconjunto) e exportar atendimentos para o replay.

### API

- FastAPI sobre a interface de atendimento e as operações da Fila: listar clientes, iniciar atendimento, enviar fala, assinar os eventos do rastro por SSE, listar a Fila, aprovar, recusar e resolver ticket.

### Painel (ADR-0005, ADR-0002, ADR-0012)

- Next.js com TypeScript estrito. Três telas: chat com seletor de cliente ao lado do rastro ao vivo; Fila do atendente humano; replay de atendimentos gravados.
- O replay é exportado como site estático para a Vercel e lê só rastros escolhidos e versionados à parte; os rastros de execução ficam locais.

### Configuração e ferramental

- Configuração por variáveis de ambiente: caminho do banco, diretório de rastros, limite automático de reembolso, modelo da Recepção, modelo dos especialistas, número máximo de turnos, provedor da avaliação.
- O pacote Python migra para uv; ruff, pyright e pytest. Painel com pnpm e ESLint. Conventional Commits. CI no GitHub Actions. Templates de issue e de PR. Release v0.1.0 no fim.

## Testing Decisions

- Um bom teste exercita comportamento externo por uma das costuras abaixo e confere o que sai dela (resposta, estado do banco, eventos do rastro), nunca detalhes internos. Mudar a implementação sem mudar o comportamento não deve quebrar teste.
- Nenhum teste automático chama modelo de linguagem; o CI é determinístico e gratuito.
- **Costura 1, ferramentas (já existe):** executar uma ferramenta pelo nome no contexto de um cliente, sobre um banco recriado pelo seed. Cobre regras de reembolso, checagem de dono, busca na política e as operações da Fila. Arte prévia: os testes atuais de regras e ferramentas, que percorrem os cenários C001 a C008 com o banco do seed. O primeiro ciclo de TDD é o teste da busca que hoje falha ("atrasou" não encontra "atraso").
- **Costura 2, atendimento (nova, principal):** iniciar um atendimento com o runtime roteirizado, enviar falas e conferir falas, eventos do rastro e estado do banco. Cobre o hook de elegibilidade, o mascaramento, o formato e a versão do rastro, o registro de delegação, tokens e custo.
- **Costura 3, avaliação:** rodar um cenário com runtime e provedor roteirizados e conferir o veredito, as violações graves e leves e as métricas. Cobre o critério do ADR-0011 e o cálculo de pass^3.
- CLI e API são adaptadores finos e ganham poucos testes de fumaça sobre as costuras 1 e 2.
- O painel tem TypeScript estrito, ESLint e testes unitários da leitura do rastro JSONL; a interface não é testada automaticamente.
- O comportamento dos agentes reais é medido pela avaliação local, não pelo CI.

## Out of Scope

- Cancelar pedido em andamento, alterar endereço, pagamento, cupons.
- Restaurante e entregador como atores.
- Login, cadastro e verificação de identidade na conversa; o cliente chega autenticado.
- WhatsApp ou outros canais além do terminal e do painel.
- Aviso ao cliente depois da decisão do atendente humano.
- Alterar o valor de um reembolso, por agente ou por humano.
- Demo pública ao vivo; a versão publicada é um replay.
- Agentes rodando em modelos não-Claude.
- OpenTelemetry e ferramentas externas de observabilidade.
- PostgreSQL ou qualquer infraestrutura além do SQLite.
- Avaliação completa no CI.

## Further Notes

- Prazo: inscrição em 17/11/2026. Cronograma: 5 a 6/11 base, ferramentas MCP e um agente funcionando; 7 a 9/11 orquestração completa, Fila pela CLI e guardrails; 10 a 12/11 avaliação com clientes simulados, métricas e CI; 13 a 14/11 painel ao vivo, Fila no painel e replay; 15 a 16/11 README com arquitetura e resultados, vídeo curto e post no LinkedIn.
- O plano é entregar tudo. Se o prazo apertar, a ordem de corte, de baixo para cima, é: adaptador Ollama, replay na Vercel, painel ao vivo. Agentes, avaliação e README com os números não são cortados.
- Os agentes rodam na assinatura Claude Pro do autor, que não pode atender outras pessoas; por isso a demo pública é replay (ADR-0002). Quem clonar pode rodar ao vivo com a própria chave.
- O código do backend foi escrito antes deste PRD e é ponto de partida, não código aprovado; cada ticket revisa a parte que toca.
- Vocabulário em `GLOSSARY.md`; decisões em `docs/adr/` (0001 a 0015).
