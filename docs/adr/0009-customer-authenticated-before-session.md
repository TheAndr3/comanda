# O cliente chega autenticado; a identidade vem do contexto, nunca da conversa

Todo atendimento começa com um cliente já identificado: a CLI recebe `--customer`, o painel tem um seletor de cliente e cada cenário declara o seu. Não há login nem verificação de identidade na conversa, e o que o cliente diz sobre quem é ("sou o C002") é ignorado. É assim que o chat de ajuda de um app de delivery funciona, já dentro da sessão autenticada, e isso mantém a autenticação fora do escopo e torna o cenário "pedido de outro cliente" um teste limpo do guardrail do ADR-0008.
