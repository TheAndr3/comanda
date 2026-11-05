# Topologia de supervisor sobre o Claude Agent SDK

A Recepção conversa com o cliente e delega a dois especialistas, Pedidos e Reembolsos, definidos como subagentes do Claude Agent SDK. Os especialistas devolvem o resultado à Recepção e nunca falam direto com o cliente; cada um enxerga só as próprias ferramentas, e o escalonamento está disponível para todos. Escolhemos o SDK porque ele roda com a assinatura do autor e traz subagentes, ferramentas MCP e hooks nativos; frameworks como LangGraph exigiriam chave de API paga. A Recepção usa Haiku e os especialistas usam Sonnet, configuráveis por variável de ambiente.

O supervisor se chamava "agente de Atendimento", mas Atendimento já nomeia a conversa inteira; o nome Recepção evita frases ambíguas como "o atendimento delegou".
