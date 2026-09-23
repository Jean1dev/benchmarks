# API Node.js

JavaScript executado em Node.js 24.21.0 LTS, HTTP/JSON/criptografia nativos e
`pg` 8.23.0 para PostgreSQL. Sem framework ou ORM. Dependências transitivas
fixadas no package-lock.json. Contrato: `benchmark/contract.md`.

## Execução

Na raiz do repositório:

```sh
docker build -t benchmark-api-node:local apis/node
```

Selecione `node` no Compose compartilhado. O container executa
`node server.js` como usuário sem privilégios. Use `PORT=8080`,
`DATABASE_URL`, `DB_POOL_MAX=10` e `ENCRYPTION_KEY_BASE64` conforme o
`.env.example` compartilhado. A chave é obrigatória, Base64 canônico,
exatamente 32 bytes; a inicialização falha se inválida. Não há chave na imagem.
O PostgreSQL precisa estar pronto e com schema aplicado antes da API.

Testes locais (Node.js 24.21.0):

```sh
cd apis/node
npm ci --ignore-scripts
npm test
```

Os testes locais usam banco substituto em memória somente no teste e cobrem
round-trip HTTP/criptográfico, Unicode/escapes, string vazia, limites,
JSON/UTF-8 inválido, UUID, registro inexistente e adulteração de tag.
Os testes compartilhados verificam o PostgreSQL real e adulteração persistida.
O container de produção não contém esse substituto nem arquivos de teste.

## Runtime e medição

Um processo, um event loop de JavaScript, sem cluster/workers adicionais.
V8 executa JIT e GC com parâmetros padrão; Node/libuv/V8 podem criar threads
auxiliares. AES-GCM usa OpenSSL por `node:crypto`, sincronamente no event loop.
Nenhum ajuste de heap ou GC; `NODE_ENV=production`, sem outras flags de runtime.

Pool máximo 10 conexões (configurável entre 1 e 10). Aquisição e consulta têm
prazo combinado de 5 segundos; conexões com erro são descartadas. O servidor
PostgreSQL também recebe statement_timeout de 5 segundos. Conexões ociosas
expiram após 30 segundos. Sem retries, sem nomes de prepared statements, sem
cache de resultados, compressão ou logs de acesso. INSERT usa autocommit.

HTTP/1.1 keep-alive sem TLS. Timeout de recebimento do request/cabeçalhos de
5 segundos, verificado pelo Node a cada 1 segundo; conexão keep-alive ociosa
por 60 segundos. O timeout de escrita do Node é de **inatividade** de socket
por 10 segundos, não um prazo absoluto como no servidor Go. Registre essa
diferença em cenários com clientes lentos. Respostas normais são JSON; falhas
no parser HTTP e timeout antes do handler seguem a resposta nativa do Node.

CPU, memória e swap ficam definidos no Compose comum. Memória deve incluir
V8, OpenSSL, buffers externos e todas as threads do container.

Fontes das versões:
- https://nodejs.org/en/blog/release/v24.21.0
- https://www.npmjs.com/package/pg
