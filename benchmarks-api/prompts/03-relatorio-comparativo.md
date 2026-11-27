# Prompt 3 — Relatórios comparativos do benchmark

Você é responsável por analisar os resultados reais do benchmark e gerar relatórios reproduzíveis de comparação entre implementações. Implemente o processamento e gere os arquivos finais; não se limite a descrever uma metodologia.

## Entradas e escopo

Leia `benchmark/contract.md`, `benchmark/config.json`, `benchmark/implementations/`, `results/schema.md` e os diretórios `results/<campaign_id>/<run_id>/`.

Use manifests, requests.csv, resources.csv, summary.json e artefatos Gatling como evidência. Não execute novamente a carga nem altere APIs nesta etapa. Não invente resultados ou substitua dados ausentes por zero. Se não houver medições, entregue o gerador e um relatório explícito de ausência de dados, sem rankings fictícios.

A comparação representa linguagem, runtime, servidor HTTP, driver, biblioteca criptográfica e configuração medidos em conjunto. Não atribua causalidade exclusivamente à linguagem nem extrapole para outros ambientes.

## Validação antes da comparação

Verifique schema, tipos, unidades, duplicatas de run_id, fases, timestamps, contagens, duração, status e integridade dos arquivos. Cruze resumos com dados brutos e informe divergências.

Compare somente execuções equivalentes quanto a cenário, payload, modelo de carga, nível, duração, aquecimento, dataset, pool, durabilidade, recursos, versões/configuração do PostgreSQL e ambiente de execução.

Identifique mudanças de código, imagem ou configuração dentro da campanha. Separe variantes de uma implementação. Sinalize execuções concorrentes, gerador saturado, dados incompletos, OOM, reinícios, erros de coleta e repetições ausentes.

Não exclua silenciosamente falhas ou outliers. Separe falhas de infraestrutura que invalidam medições de falhas da própria API sob carga, que são resultados relevantes. Liste todos os critérios de exclusão e todas as execuções afetadas.

## Métricas

Calcule por execução e endpoint:

- Throughput: respostas bem-sucedidas concluídas dentro da janela / duração da janela.
- Latência p50, p95 e p99 da coorte iniciada na janela, separando sucessos e falhas, com drenagem conforme contrato.
- Quantidade e proporção de erros, timeouts e status HTTP disponíveis.
- Taxa oferecida, iniciada e concluída, sem tratar usuários concorrentes como RPS.
- Memória em repouso, mediana e p95 das amostras sob carga e pico observado, por serviço.
- CPU utilizada em cores: delta de CPU em segundos / tempo observado em segundos; normalize pelo limite efetivo quando exibir percentual.
- CPU-segundos por sucesso na mesma janela, quando houver dados coerentes e sucessos suficientes.
- OOM, reinícios e throttling disponíveis.

Não misture memória total de cgroup, working set e RSS. Preserve definições e unidades do coletor. Distinga pico amostrado de pico reportado pelo kernel. CPU/memória de API, banco e Gatling aparecem separadas.

Agregue repetições equivalentes com mediana e intervalo interquartil, exibindo também os valores individuais e quantidade de repetições válidas. Não calcule percentis globais fazendo média dos percentis de cada execução. Se combinar amostras brutas, identifique explicitamente o resultado combinado e sua ponderação.

Com poucas repetições, seja cauteloso com pequenas diferenças. Não afirme significância estatística sem análise apropriada. Dados de demonstração só podem existir como fixtures de teste claramente identificadas e nunca entram no relatório real.

## Comparações e interpretação

Responda com evidência:

1. Qual implementação teve maior throughput em cada cenário, payload e nível de carga?
2. Qual apresentou menor p95/p99 sob a mesma carga, considerando erros?
3. Qual consumiu menos memória em repouso e em cargas equivalentes?
4. Qual foi a maior taxa aberta testada que cumpriu o SLO definido antes da campanha?
5. Como cada implementação se comportou ao saturar ou atingir limites?
6. Existem indícios de limitação pelo banco, pelo gerador ou pelo host?

Para considerar uma taxa como sustentada, exija que todas as repetições válidas previstas nessa combinação cumpram o SLO e que a carga realmente iniciada fique dentro de 5% da oferecida. Informe repetições ausentes e não declare capacidade confirmada com campanha incompleta.

Leia SLO e tolerâncias da configuração quando definidos; registre a regra aplicada. O default é p95 de sucessos <= 200 ms e falhas <= 1%. Se todas as taxas passarem, diga que a maior taxa testada passou; não invente o ponto de saturação. Se nenhuma passar, informe isso.

Não declare vencedor geral automaticamente. Apresente lideranças por dimensão e diferenças relativas com baseline explícito. Para percentuais, mostre a fórmula e trate baseline zero. Aponte trocas entre memória, CPU, latência e throughput. Gargalos são hipóteses quando faltarem evidências para confirmá-los.

## Entregáveis

Crie scripts em `analysis/` com dependências fixadas, Dockerfile e integração com Compose. O container de análise deve ter limite configurável, inicialmente 2 CPUs/2 GiB. Os resultados brutos são montados somente para leitura; relatórios usam diretório separado.

Gere `reports/<campaign_id>/` com:

- `report.md`: resumo em português, metodologia, ambiente, tabelas, interpretação, limitações e links para evidências.
- `report.html`: versão navegável e autocontida, sem dependências de CDN.
- `comparison.csv` e `comparison.json`: métricas agregadas e dimensões de comparação.
- `runs.csv`: resultados individuais e classificações de validade.
- `charts/`: gráficos em SVG ou PNG.
- `validation.json`: dados ausentes, inconsistências, exclusões e critérios utilizados.

Inclua gráficos de throughput por carga, p95/p99 por carga, erros por carga, memória por carga e CPU por carga; séries temporais devem incluir API, PostgreSQL e gerador separadamente. Separe perfil aberto e fechado, cenários e payloads para evitar comparações incorretas. Use eixos/unidades explícitos e cores consistentes. Não conecte dados ausentes como se fossem observações.

O relatório deve começar pelos achados efetivamente sustentados, apresentar as condições de teste e permitir rastrear cada conclusão até run_ids e dados brutos. Registre revisão do código de análise e configuração utilizada.

## Verificação e conclusão

Teste o processamento com fixtures pequenas que cubram erros, arquivos ausentes, baseline zero, múltiplas repetições e fronteiras entre aquecimento, medição e drenagem. Verifique que o processamento não altera dados de entrada.

Execute a análise sobre os resultados disponíveis. Entregue comandos para reproduzir a análise em Docker, caminhos dos relatórios, validações realizadas e limitações concretas. Se a campanha estiver incompleta, identifique o relatório como parcial.
