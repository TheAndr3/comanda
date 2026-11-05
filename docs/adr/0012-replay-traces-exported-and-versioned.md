# Rastros do replay são exportados e versionados à parte

Os rastros de execução ficam em `runs/`, locais e fora do git. O comando `comanda export-replay` copia atendimentos escolhidos para `web/public/replays/`, que é versionado e publicado com o painel estático (ADR-0002). Assim só entra no repositório público o que foi escolhido a dedo, e o replay não depende de execuções locais de quem clonar o projeto.
