# O runtime de agentes age só pelo Atendimento

O runtime de agentes (o Claude Agent SDK ou o roteirizado) decide o caminho da conversa, mas cada fala, delegação, chamada de ferramenta e registro de tokens passa por uma interface `AgentHost` que o Atendimento implementa; o runtime nunca recebe o contexto das ferramentas, o repositório nem o arquivo do Rastro. Assim o cliente fica fixo sem depender do adaptador (ADR-0009), todo passo vira evento num lugar só e os guardrails têm um único ponto de aplicação para os dois runtimes. Só a Recepção fala com o cliente: `say` não recebe agente (ADR-0003).

Este ADR ajusta o ADR-0008 em dois pontos. A máscara é aplicada na criação de cada evento, não só na gravação do arquivo, para que o SSE do painel também nunca veja o dado real; as falas devolvidas ao cliente não passam por ela. E ela cobre os dados pessoais que o Cliente tem no domínio, nome, telefone e e-mail; o domínio ainda não guarda endereço, que entra na máscara quando existir.
