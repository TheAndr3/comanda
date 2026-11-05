# O valor do reembolso é calculado por regra em código, nunca pelo modelo

Os agentes decidem *que* reembolso pedir (pedido, tipo de problema, itens), mas o valor e a necessidade de aprovação humana saem só de `evaluate_refund`, a partir dos dados do pedido. A ferramenta `issue_refund` não aceita valor e recalcula a regra no momento da execução. Escolhemos isso em vez de deixar o modelo propor valores dentro de limites porque um prompt manipulado não pode mudar o resultado, as regras ficam testáveis sem LLM e a avaliação confere o estado do banco por código.
