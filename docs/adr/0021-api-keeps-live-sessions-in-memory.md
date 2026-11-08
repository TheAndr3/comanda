# A API guarda os atendimentos vivos na memória e transmite o Rastro pela posição

A API do painel mantém cada atendimento iniciado num dicionário do próprio processo, junto do seu runtime, e não persiste nada além do que o Atendimento já grava: o banco e o JSONL do Rastro. Reiniciar a API perde os atendimentos em andamento, e os encerrados ficam no dicionário até lá, o que é aceitável para um painel local de um Desenvolvedor só; um armazenamento externo de sessões exigiria serializar o runtime do SDK, que mantém uma conexão aberta com o Claude Code.

O SSE lê do próprio Atendimento, que oferece `follow(after)`: os eventos depois de uma posição, em ordem, esperando os próximos até o fim do atendimento. Cada evento SSE leva a linha do JSONL como `data` e o `seq` como `id`, então ao reconectar o navegador manda `Last-Event-ID` e o stream continua do evento seguinte, sem repetir nem pular nada. Vários painéis podem seguir o mesmo atendimento. O SSE é o único caminho do Rastro: enviar uma fala devolve só as falas ao cliente.

Um atendimento aceita uma fala por vez: uma segunda fala durante um turno recebe 409, em vez de esperar na fila. Encerrar (`POST /sessions/{id}/close`) espera o turno em andamento, para que `session_ended` seja sempre o último evento. Um SSE aberto só termina com o atendimento, então `comanda serve` limita a espera do desligamento a poucos segundos e depois encerra todos os atendimentos.

O seletor de cliente do painel (ADR-0009) recebe só id e nome. A máscara do ADR-0008 continua valendo para o Rastro, inclusive pelo SSE; e-mail e telefone não saem pela API.
