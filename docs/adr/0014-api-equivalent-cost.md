# Custo por atendimento medido como custo equivalente em API

Os agentes rodam na assinatura Claude Pro, que não cobra por token, mas custo por atendimento é uma métrica de negócio central. O relatório mostra os tokens de entrada e saída por modelo e o custo que eles teriam na tabela pública da API, rotulado como estimativa; se o SDK informar um custo estimado, ele é usado e conferido contra esse cálculo. Omitir o custo esconderia o principal argumento para usar Haiku na Recepção, e apresentar o valor como gasto real seria falso.
