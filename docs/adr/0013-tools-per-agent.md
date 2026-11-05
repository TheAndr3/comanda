# Cada agente recebe só as ferramentas do seu papel

A Recepção tem `search_policies` e `escalate_to_human`; Pedidos tem `list_my_orders`, `get_order_details`, `track_delivery` e `get_restaurant_info`, que inclui alérgenos; Reembolsos tem `check_refund_eligibility`, `issue_refund` e `escalate_to_human`. A Recepção não consulta pedidos sozinha: precisa delegar, e a delegação aparece no rastro. Dar todas as ferramentas a um agente só seria mais simples, mas apagaria a separação de papéis que o ADR-0003 descreve e deixaria o modelo mais barato perto de `issue_refund`.
