# API Python

CPython **3.14.7**, aiohttp **3.14.3**, asyncpg **0.31.0** e cryptography **50.0.1**. O contrato e o schema ficam em `benchmark/contract.md` e `benchmark/schema.sql`. aiohttp fornece somente HTTP/roteamento; não há ORM nem framework de aplicação completo. Todas as dependências, inclusive transitivas, estão fixadas em `requirements.txt`.

Da raiz do repositório:

```sh
docker build -t benchmark-api-python:local apis/python
python3 scripts/init_env.py
docker compose --profile python up --build -d api-python
```

Use a configuração compartilhada para fornecer `DATABASE_URL`, `DB_POOL_MAX=10`, `PORT=8080` e uma chave Base64 de 32 bytes em `ENCRYPTION_KEY_BASE64`. A chave é validada antes da conexão ao banco. Schema e prontidão do PostgreSQL são responsabilidade do Compose compartilhado. Nunca embuta uma chave na imagem.

## Modelo de execução

Um processo CPython, um event loop asyncio e um worker; GIL habilitado, GC padrão, sem ativação explícita de JIT. Entrada `python -O app.py`, sem logs de acesso. Bibliotecas de HTTP, PostgreSQL e criptografia usam extensões nativas; isso faz parte da implementação medida. AES-GCM utiliza a biblioteca cryptography, sem implementação própria de primitivas.

Pool com uma conexão inicial e máximo configurável de 1 a 10, queries parametrizadas sem cache de statements. INSERT autocommit aguarda confirmação antes de responder. O timeout de 5 segundos inclui aquisição do pool e operação; conexão e statement_timeout também são 5 segundos. Leitura do corpo HTTP: 5 segundos. Keep-alive: 60 segundos. Encerramento: 10 segundos. aiohttp não oferece deadline independente para escrita da resposta; aplica backpressure padrão do asyncio. Erros sintáticos do protocolo HTTP anteriores ao middleware podem usar o formato padrão da biblioteca; os erros de aplicação seguem JSON.

Sem compressão de resposta, descompressão automática de entrada, caches de dados, retries de operações ou batching. O pool pode restabelecer conexões entre operações; não reexecuta uma escrita falha. Corpo acima de 128 KiB fecha a conexão depois de responder 413 para não reutilizar um stream não consumido.

## Verificação

```sh
python3 -m venv /tmp/benchmark-python-test
/tmp/benchmark-python-test/bin/pip install -r apis/python/requirements.txt
(cd apis/python && /tmp/benchmark-python-test/bin/python -m unittest -v)
```

Os testes locais usam um pool em memória para exercitar HTTP, limites, JSON, UUID, round-trip criptográfico, adulteração da tag e chave inválida. A suíte compartilhada `tests/contract.py` verifica PostgreSQL real e persistência. Build da imagem e os três testes locais passaram durante a implementação; a integração é executada pelo coordenador do repositório. Não foi executada carga.

Fontes: [CPython](https://www.python.org/downloads/), [aiohttp](https://docs.aiohttp.org/en/stable/web_reference.html), [asyncpg](https://magicstack.github.io/asyncpg/current/api/index.html), [AESGCM](https://cryptography.io/en/latest/hazmat/primitives/aead/#cryptography.hazmat.primitives.ciphers.aead.AESGCM).
