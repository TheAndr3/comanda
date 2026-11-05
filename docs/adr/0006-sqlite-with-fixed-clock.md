# SQLite com dados sintéticos e relógio fixo

A base é SQLite, recriada por um seed determinístico, e o "agora" do sistema é fixado na tabela `meta` (5/11/2026 19h30, horário de Brasília). Assim cada cenário da avaliação parte do mesmo estado e as regras de atraso e prazo dão sempre o mesmo resultado. Dados sintéticos são o caminho normal aqui, porque nenhuma empresa publica dados de atendimento. PostgreSQL não traria nada que este projeto precise e exigiria infraestrutura para rodar a avaliação.
