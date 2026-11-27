# API Go

Implementação do contrato em `benchmark/contract.md`, usando Go 1.27.1,
`net/http`, `encoding/json`, `crypto/aes`, `crypto/cipher`, `crypto/rand` e
pgx v5.11.0 (única dependência direta). Dependências transitivas estão fixadas
em go.mod/go.sum. Não usa ORM ou framework.

## Execução

A partir da raiz:

```sh
docker build -t benchmark-api-go:local apis/go
```

Selecione `go` no Compose compartilhado. O container executa `/api`, escuta
`0.0.0.0:${PORT:-8080}` e exige `DATABASE_URL` e `ENCRYPTION_KEY_BASE64`.
`DB_POOL_MAX` aceita 1 a 10, padrão 10. O schema deve existir antes da execução.
A chave deve ser Base64 padrão e decodificar exatamente 32 bytes. Nenhuma
chave é incluída na imagem. Use o `.env.example` compartilhado.

Compilação e verificações locais (Go 1.27.1):

```sh
cd apis/go
go test ./...
go vet ./...
CGO_ENABLED=0 go build -trimpath -ldflags='-s -w' -o /tmp/benchmark-api-go .
```

Execute os testes funcionais compartilhados contra a API iniciada para
validar PostgreSQL, round-trip e adulteração de registros. Os testes locais
cobrem validação HTTP, UUID, chave, round-trip criptográfico e tag adulterada.

## Runtime e condições de medição

Um processo nativo, sem JIT. O servidor padrão usa goroutines para concorrência;
o runtime gerencia as threads, inclusive auxiliares de GC e I/O. GOMAXPROCS,
GOGC e GOMEMLIMIT permanecem nos padrões do runtime; não há tuning implícito.
O runtime Go 1.27 considera limites de CPU do container, mas sua política pode
manter mais de uma thread apta a executar mesmo com quota de uma CPU. A quota
efetiva continua sendo a configurada no Compose. A memória medida deve incluir
todo o processo/container, não somente o heap Go.

Build otimizado padrão, CGO desabilitado, `-trimpath` e remoção dos símbolos
com `-ldflags='-s -w'`. Imagem final scratch, usuário 65532, sem shell.
Pool pgx com máximo 10 conexões, mínimo zero; sem cache de statements, usando
protocolo estendido parametrizado. Autocommit confirma o INSERT antes do 201.
Sem retries de operações, cache de dados, compressão ou logs de acesso.

Timeout de conexão, aquisição+consulta e prontidão PostgreSQL: 5 segundos.
Timeout de leitura de cabeçalhos/corpo HTTP: 5 segundos; escrita: 10 segundos;
conexão keep-alive ociosa: 60 segundos. HTTP/1.1 sem TLS. O banco deve estar
pronto antes da API; a inicialização falha se o ping inicial não concluir.

Limites de CPU, memória e swap são responsabilidade do Compose compartilhado.
Não executar a campanha de carga simultaneamente com builds ou outras APIs.

Versões verificadas nas fontes oficiais:
- https://go.dev/doc/devel/release
- https://github.com/jackc/pgx/releases/tag/v5.11.0
