# A avaliação completa roda localmente; o CI roda só testes determinísticos

Uma rodada completa tem cerca de 50 atendimentos com agentes, cliente simulado e juiz, o que não cabe no CI e consome cota da assinatura Pro. O CI roda os testes com o modelo roteirizado do ADR-0004; `comanda eval` roda a avaliação localmente, com `--subset` para iterar em poucos cenários, e grava cada resultado em `evals/results/` com data, modelos e commit. A primeira rodada também mede quanto da cota do Pro uma avaliação consome, para planejar quantas rodadas cabem até a entrega.
