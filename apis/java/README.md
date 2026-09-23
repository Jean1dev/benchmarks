# API Java

Eclipse Temurin **25.0.2+10**, servidor HTTP nativo `jdk.httpserver`, criptografia JCA AES/GCM/NoPadding e `SecureRandom`. Dependências mínimas: PostgreSQL JDBC **42.7.13**, HikariCP **7.1.0**, Gson **2.14.0**, SLF4J API/NOP **2.0.17**. Sem framework web ou ORM. JARs têm versões e SHA-256 registrados em `dependencies.txt` e `dependencies.sha256`.

Da raiz:

```sh
docker build -t benchmark-api-java:local apis/java
python3 scripts/init_env.py
docker compose --profile java up -d api-java
docker run --rm --cpus=1 --memory=256m --memory-swap=256m benchmark-api-java:local java -Xmx128m -cp 'classes:lib/*' SelfTest
```

`DATABASE_URL=postgresql://benchmark:benchmark@postgres:5432/benchmark?sslmode=disable` é convertido para JDBC, separando usuário e senha. Usa `PORT=8080`, `DB_POOL_MAX=10` e `ENCRYPTION_KEY_BASE64`; valida chave antes de conectar. O schema é aplicado pelo Compose compartilhado.

Um processo JVM, virtual thread por requisição, scheduler e threads auxiliares da JVM/pool. Pool Hikari mínimo 1 e máximo 10 (configurável até 10). INSERT em autocommit confirmado antes da resposta. Sem cache de statements/preparação persistente, cache de dados, retries de operação ou compressão HTTP.

Heap inicial 16 MiB e máximo 128 MiB, SerialGC, JIT HotSpot padrão, um processador visível, code cache de 32 MiB e memória direta máxima de 16 MiB. Os limites externos continuam 256 MiB/1 CPU/sem swap; heap não representa a memória total do processo. Flags completas no Dockerfile/manifesto.

Timeouts: aquisição de conexão 5 s, conexão TCP 5 s, statement 5 s, socket JDBC 5 s. A espera do pool e execução são prazos separados, podendo somar aproximadamente 10 s. `sun.net.httpserver.maxReqTime=5` e `maxRspTime=10` controlam duração de leitura/escrita; o timer interno aplica esses limites com granularidade própria. Keep-alive ocioso de 60 s. Esses parâmetros pertencem à implementação do JDK e estão fixados junto à versão. Erros de protocolo antes do handler podem usar respostas nativas; erros de aplicação seguem o contrato JSON.

`SelfTest` cobre round-trip, autenticação da tag, chave inválida e UTF-8 inválido. Testes HTTP e PostgreSQL reais são realizados pela suíte compartilhada `tests/contract.py`; não execute carga nesta etapa.

Fontes: [Temurin](https://adoptium.net/temurin/releases/), [JDBC PostgreSQL](https://jdbc.postgresql.org/download/), [HikariCP](https://github.com/brettwooldridge/HikariCP), [Gson](https://github.com/google/gson), [JDK HTTP server](https://docs.oracle.com/en/java/javase/25/docs/api/jdk.httpserver/module-summary.html).
