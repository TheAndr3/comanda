# Agentes só com Claude; cliente simulado e juiz atrás de uma interface de provedor

Os agentes usam apenas modelos Claude. O cliente simulado e o juiz da avaliação passam por uma interface de provedor com dois adaptadores: Gemini (camada gratuita do Google AI Studio) e Ollama (modelos open source locais). Os testes automáticos do CI usam um modelo roteirizado e determinístico. Rodar os próprios agentes em modelos open source pelo endpoint compatível do Ollama foi descartado: a Anthropic não dá suporte a modelos não-Claude no Claude Code, e modelos locais pequenos erram no uso de ferramentas.
