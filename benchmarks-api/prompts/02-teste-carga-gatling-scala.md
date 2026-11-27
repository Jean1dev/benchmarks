# Prompt 2 — Teste de carga com Gatling e Scala em containers

Você é responsável por implementar e executar a infraestrutura de benchmark das APIs existentes. Use Gatling Community com simulações escritas em Scala, executadas em Docker. Entregue código e scripts reproduzíveis, além dos resultados efetivamente coletados.

## Integração com a etapa anterior

Leia `benchmark/contract.md`, `benchmark/schema.sql`, `benchmark/implementations/`, o Compose e as instruções locais. Se os artefatos da API estiverem ausentes, implemente o que puder independentemente e informe a dependência; não invente APIs ou medições.

Não modifique implementações para favorecer resultados. Preserve AES-256-GCM, endpoints, durabilidade, pool máximo de 10, API com 1 CPU/256 MiB e PostgreSQL com 1 CPU/512 MiB.

Crie o projeto Scala em `load-tests/`, com versões compatíveis e exatas de Gatling, Scala, JDK e ferramenta de build. Use documentação oficial para conferir compatibilidade e APIs. Nenhum serviço pago deve ser necessário.

Adicione o Gatling ao Compose com limite inicial de 2 CPUs/2 GiB e heap JVM compatível com o limite total. Limite também containers auxiliares. Permita configuração, registrando qualquer mudança. Rode somente uma API e uma carga por vez. Monitore o gerador para detectar quando ele limita a medição.

## Configuração e matriz

Crie `benchmark/config.json` como fonte única da campanha e um modo smoke separado. Inclua seleção de implementações, seed, timeouts, recursos, duração, repetições, cenários e níveis de carga. O modo smoke nunca entra no ranking.

Defaults da campanha:

- Cenários: somente POST, somente GET e misto com 50% POST/50% GET em expectativa; registre a proporção efetiva.
- Payloads: mensagens com exatamente 128, 1024 e 16384 bytes UTF-8, geradas deterministicamente fora do trecho medido.
- Base inicial: 100.000 registros do tamanho correspondente ao cenário.
- GET: seleção uniforme com reposição entre IDs da base inicial; nunca depende dos POSTs da medição.
- Aquecimento: 30 segundos; medição: 120 segundos; 5 repetições independentes.
- Perfil fechado: 1, 10, 50 e 100 usuários simultâneos, executando requisições sequenciais em loop, sem pausas artificiais.
- Perfil aberto, em execuções separadas por taxa: 10, 50, 100, 250, 500 e 1000 chegadas por segundo, configuráveis antes da campanha.
- Timeout HTTP comum: 5 segundos; drenagem de requisições em andamento: até 10 segundos.
- SLO inicial para análise: p95 das respostas bem-sucedidas <= 200 ms e taxa de falha <= 1%; configurável antes da campanha.

No perfil aberto, cada usuário virtual executa exatamente uma requisição. Assim, usuários/segundo representam requisições/segundo pretendidas. No misto, cada usuário escolhe uma das operações. No perfil fechado, usuários simultâneos não equivalem a requisições/segundo.

Implemente os dois modelos em simulações ou configurações separadas, respeitando o modelo de injeção do Gatling. Não use throttling que converta silenciosamente o perfil aberto em outro modelo.

## Preparação e isolamento

1. Verifique contratos, containers, limites efetivos e ausência de outra carga concorrente.
2. Registre host, arquitetura, kernel, Docker, CPU, memória, armazenamento, revisão Git, estado dirty, imagens, versões e configuração de todos os serviços.
3. Antes de cada repetição, restaure o mesmo estado inicial do banco em volume exclusivo do benchmark. Nunca apague volumes alheios ao projeto.
4. Prepare os registros criptografados e o feeder de IDs fora da medição; valide uma amostra por GET. Mantenha dataset lógico e seed iguais entre linguagens.
5. Reinicie a API, aguarde prontidão externamente e colete 10 segundos de recursos em repouso.
6. Execute aquecimento e medição com a mesma API e banco, sem reiniciar entre as fases. Identifique as fases explicitamente.
7. Exclua aquecimento e drenagem das métricas da janela medida. Preserve os artefatos originais para auditoria.
8. Alterne a ordem das implementações entre repetições com seed registrada.

Aquecimento com POST altera o banco. Defina uma quantidade comum de gravações de aquecimento por cenário e distribua-a pelos 30 segundos, registrando contagens; só inicie a medição se todas forem concluídas. Evite iniciar cada linguagem com volumes de dados diferentes devido ao throughput de aquecimento. Crescimento diferente durante a medição de POST é parte do resultado e deve ser registrado.

Não prometa cache frio somente por reiniciar um container. Documente cache do PostgreSQL, cache do sistema operacional e aquecimento do runtime. Não limpe caches globais do host automaticamente.

Use rede, HTTP/1.1, keep-alive e configuração de conexões iguais. Desative retries HTTP e compressão. Nomes de requisição devem ser estáveis por endpoint, sem IDs individuais. Faça checks de status e estrutura; valide round-trip completo no smoke e checks equivalentes sob carga.

## Métricas e artefatos para o próximo agente

Crie `results/<campaign_id>/<run_id>/` com:

- `manifest.json`: identidade da execução, implementação, cenário, bytes, modelo, nível de carga, repetição, seed, versões, recursos efetivos, configuração/SLO, timestamps UTC das fases, dataset, contagens de preparação, commit e hashes relevantes; nunca inclua segredos.
- `gatling/`: logs brutos e relatório HTML nativo.
- `requests.csv`: uma linha por requisição, com request_id, endpoint, fase, início/fim, duração em ms, sucesso/falha, status HTTP quando disponível e classe de erro. Preserve valores indisponíveis como nulos.
- `resources.csv`: amostras a cada segundo, com timestamp, fase, serviço, container, CPU acumulada em segundos, memória atual/limite em bytes, throttling, reinícios e OOM quando disponíveis.
- `summary.json`: contagens, duração, throughput, percentis e erros por endpoint e total, junto com estado complete/failed/invalid e motivos.
- `execution.log`: eventos da orquestração sem segredos.

Documente tipos, unidades, campos obrigatórios, versão do schema e regras de agregação em `results/schema.md`. A saída deve ser consumível pelo terceiro prompt sem interpretar texto livre.

Implemente exportação compatível com a versão fixada do Gatling; não presuma que seu log contém status HTTP ou qualquer campo não verificado. Se precisar de instrumentação adicional, mantenha-a igual para todas as APIs e verifique seu custo. Não fabrique campos ausentes.

Colete API, PostgreSQL e Gatling separadamente. Prefira contabilidade de cgroups para memória total e CPU; declare tratamento de page cache e diferenças para RSS. Pico amostrado não é pico absoluto. Se usar memory.peak, explique seu escopo/reset.

Use requisições iniciadas na janela medida como coorte para latências e falhas, aguardando a drenagem. Para throughput, conte sucessos concluídos dentro da janela e divida por 120 segundos. Registre separadamente requisições em andamento nos limites da janela. Não misture essas definições silenciosamente.

Registre carga oferecida, requisições realmente iniciadas, concluídas, timeouts e desvios de agendamento mensuráveis. Não suponha uma métrica de requisições descartadas que Gatling não forneça. Acompanhe saturação de CPU/memória do gerador e marque resultados comprometidos como inválidos, preservando os dados.

## Execução e entrega

Entregue comandos containerizados para build, smoke, uma combinação isolada e campanha completa. Estime duração e espaço antes da campanha. Execute smoke primeiro e depois a campanha solicitada dentro dos recursos disponíveis. Se não conseguir terminar, preserve resultados parciais e documente comando de retomada que não repita execuções concluídas.

Não altere taxas ou limites para apenas uma linguagem. Se a faixa não atingir saturação, registre o limite da observação e permita uma nova campanha comum com taxas maiores. Diferencie falha real da API de falha da infraestrutura de teste. Não produza ranking nesta etapa.

Referências oficiais para implementação:

- https://docs.gatling.io/reference/deploy/install-local/
- https://docs.gatling.io/concepts/injection/
- https://docs.gatling.io/testing-concepts/workload-models/
