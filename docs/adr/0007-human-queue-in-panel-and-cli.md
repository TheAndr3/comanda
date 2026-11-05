# Fila do atendente humano no painel e na CLI

Reembolsos pendentes e tickets precisam de alguém que os resolva, senão o fluxo de aprovação humana do ADR-0001 nunca termina. O atendente humano trabalha numa fila com duas portas sobre as mesmas funções: uma página no painel e comandos de CLI (`comanda queue list`, `approve`, `reject`). Ele aprova ou recusa o reembolso pendente com um motivo, mas não altera o valor, que continua saindo da regra. A CLI vem primeiro porque permite testar e avaliar o fluxo completo sem o painel; a página chega com o painel.
