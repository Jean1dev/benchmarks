# APIs comparáveis

Implementações: Go, Rust, Python, Node.js (JavaScript) e Java. Cada uma usa o mesmo [contrato](benchmark/contract.md), schema PostgreSQL e limite de recursos. Bibliotecas, runtime e concorrência são registrados em `benchmark/implementations/` e nos READMEs de `apis/`.

## Executar uma implementação

Pré-requisitos: Docker com Compose e limites de cgroups disponíveis, Python 3 para scripts auxiliares. Não é necessário instalar os cinco runtimes no host.

Na raiz do repositório:

```bash
bash scripts/api.sh go
python3 tests/contract.py
python3 tests/startup.py go
```

Substitua `go` por `rust`, `python`, `node` ou `java`. O script preserva a chave local, para outras APIs deste projeto e inicia a selecionada em `http://127.0.0.1:8080`. Não execute o script em paralelo. A primeira execução cria `.env` com uma chave aleatória e permissões restritas; preserve esse arquivo enquanto usar o mesmo volume.

```bash
curl -sS http://127.0.0.1:8080/messages -H 'Content-Type: application/json' -d '{"message":"olá"}'
curl -sS http://127.0.0.1:8080/messages/UUID_RETORNADO
docker compose --profile '*' stop
```

O PostgreSQL não expõe porta no host e usa credenciais exclusivas de desenvolvimento local. O schema é inicializado em volume novo. Não remova volumes para alternar linguagens: o formato criptográfico e a chave são compartilhados.

## Limites e verificação

API: 256 MiB/1 CPU; PostgreSQL: 512 MiB/1 CPU; memória+swap igual à memória, portanto swap zero. Política de restart desativada para não esconder falhas. Pool máximo de 10 conexões e statement_timeout do PostgreSQL de 5 segundos. Fsync, synchronous_commit e full_page_writes permanecem ligados.

```bash
docker compose --profile '*' config --quiet
docker inspect language-benchmark-postgres-1 --format '{{.HostConfig.Memory}} {{.HostConfig.MemorySwap}} {{.HostConfig.NanoCpus}}'
docker compose exec -T postgres sh -c 'cat /sys/fs/cgroup/memory.max /sys/fs/cgroup/memory.swap.max /sys/fs/cgroup/cpu.max'
```

Em cgroups v2, PostgreSQL deve apresentar `536870912`, `0` e `100000 100000`. Para API, a memória deve ser `268435456`. Inspecione limites efetivos, não apenas o YAML. Memória inclui runtime e todos os processos do container. Imagens têm versões fixadas; para campanhas, registre também seus digests/IDs.

## Testes

`tests/contract.py` é a suíte comum contra a API selecionada: round-trip, Unicode, limites UTF-8/corpo, validação, persistência criptografada, adulteração, keep-alive e concorrência. Ela cria registros próprios e remove somente esses registros ao finalizar. `tests/startup.py <linguagem>` verifica chaves inválidas em containers limitados e sem rede, além da configuração de recursos dos serviços ativos.

Após construir as cinco imagens, `python3 tests/interoperability.py` faz cinco escritas e 25 leituras cruzadas, alternando as APIs e deixando-as paradas ao terminar. O PostgreSQL permanece disponível. Não execute esse teste junto com uma campanha de carga.

Os testes funcionais não medem desempenho. A carga Gatling está em `load-tests/` e o processamento de relatórios em `analysis/`. Compare implementações completas; diferenças de bibliotecas, runtime e timeouts documentados também influenciam resultados.

## Carga Gatling

O projeto em `load-tests/` fixa Gatling, Scala, Maven e JDK, separa os modelos aberto e fechado e fornece o orquestrador de smoke/campanha. Consulte [load-tests/README.md](load-tests/README.md). O smoke é executável com `python3 load-tests/scripts/run_campaign.py --campaign smoke --implementation go --scenario post`; uma campanha completa exige tempo e armazenamento proporcionais à matriz definida em `benchmark/config.json`.

Java usa heap máximo de 128 MiB, SerialGC e limites de memória auxiliares explícitos; os demais runtimes mantêm as configurações de GC/heap descritas em seus manifests. A aquisição de conexão e execução da consulta Java têm limites separados de 5 segundos; nas outras implementações, o prazo combinado é 5 segundos. Python e Rust não configuram deadline independente de escrita HTTP, e Node usa timeout de inatividade. Essas diferenças devem acompanhar os resultados de saturação e clientes lentos.

## Relatórios comparativos

Execute `mkdir -p reports` e `docker compose -f analysis/compose.yaml run --build --rm analysis`. O serviço independente usa inicialmente 2 CPUs/2 GiB, lê `results/` somente para leitura e grava `reports/<campaign_id>/`. Não executa carga. Instruções, critérios de validade e testes: [analysis/README.md](analysis/README.md). Relatórios sem medições válidas são explicitamente parciais e não apresentam rankings.
