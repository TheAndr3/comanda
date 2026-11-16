# O cliente simulado confirma o reembolso que quer antes de encerrar

Na rodada final (`evals/results/20261116T214239Z-164804e.json`), 12 dos 16 atendimentos reprovados terminaram do mesmo jeito. A Recepção consultou a elegibilidade, disse o valor certo e perguntou "Posso registrar o reembolso de R$ X?", como pede o fluxo em duas etapas. O cliente simulado (`gemini-3.1-flash-lite`) respondeu `[FIM]`, porque o prompt manda encerrar "quando o seu objetivo for atendido" e o modelo leve entendia ouvir o valor como objetivo cumprido. Sem a confirmação, o reembolso não é registrado e o estado reprova o cenário (ADR-0022). Isso media o cliente simulado, não o agente.

O prompt do cliente simulado ganha uma regra: quando o atendimento perguntar se pode registrar algo que atende ao objetivo, ele responde que sim, e um reembolso só conta como atendido depois que o atendimento confirma o registro. A regra não decide nada pelo cliente fora do objetivo do cenário. Um cliente que quer o reembolso continua podendo recusar um valor ou uma negativa que não atende ao objetivo, e os cenários adversariais continuam pressionando do mesmo jeito.

O fluxo em duas etapas da Recepção não muda. Pedir a confirmação antes de registrar é o comportamento certo de atendimento, e tirar essa etapa para facilitar a medição trocaria um defeito do instrumento por um defeito do produto.

Consequência: as rodadas feitas a partir desta mudança não são diretamente comparáveis às anteriores. O README apresenta as duas com essa ressalva.
