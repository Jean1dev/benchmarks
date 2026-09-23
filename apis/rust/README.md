# API Rust

Implementação direta com Rust 1.90.0, Hyper (HTTP/1.1), Tokio, deadpool-postgres/tokio-postgres e AES-GCM RustCrypto. Sem framework web, ORM, cache ou logs de acesso. O contrato e os testes integrados são compartilhados em `benchmark/`.

## Execução

Na raiz:

```sh
docker build -t benchmark-api-rust:local apis/rust
```

O Compose compartilhado seleciona a implementação `rust`. Variáveis obrigatórias: `DATABASE_URL` e `ENCRYPTION_KEY_BASE64` (Base64 de 32 bytes). O processo valida a chave antes de conectar ao banco. `PORT` padrão 8080; `DB_POOL_MAX` padrão 10, aceitando apenas 1–10. O schema deve existir antes da API iniciar.

## Recursos e concorrência

Um processo nativo, um worker Tokio e tarefas assíncronas por conexão. A thread principal aguarda conexões; o runtime pode criar threads auxiliares para operações bloqueantes/DNS. Sem GC ou JIT. Compilação `cargo build --release --locked`, otimização padrão de release (`opt-level=3`), sem flags específicas do host. O Dockerfile roda como UID 10001.

O pool recicla conexões sem consultas extras de verificação (`Fast`). INSERT em autocommit e SELECT parametrizados; nenhuma repetição de operação. PostgreSQL recebe `statement_timeout=5000` e `synchronous_commit=on`. Aquisição de conexão + consulta compartilham deadline de 5 s; conexão inicial tem deadline de 5 s. Leitura dos headers tem timeout de 5 s e leitura completa do corpo tem timeout independente de 5 s. Hyper não expõe timeout de escrita diretamente; esta implementação não configura deadline de escrita. Essa diferença precisa constar no relatório. Keep-alive habilitado e TCP_NODELAY ativo.

Limites de corpo são verificados tanto por Content-Length quanto durante coleta de corpos chunked. UUIDs aceitos no GET têm representação canônica hifenizada, com letras em qualquer caixa; respostas normalizam para minúsculas.

## Verificação

```sh
cd apis/rust
cargo test --locked
cargo build --release --locked
```

Sem Rust instalado, use container auxiliar (limites de compilação, fora do benchmark):

```sh
docker run --rm --cpus=1 --memory=768m --memory-swap=768m \
  -v "$PWD/apis/rust:/work" -w /work rust:1.90.0-bookworm cargo test --locked
```

Os testes unitários verificam criptografia, rejeição de adulteração de ciphertext/tag, UTF-8, strings vazias, limites em bytes e validação de entrada/chave. Os testes HTTP/PostgreSQL são executados pelo runner compartilhado; não há campanha de carga nesta etapa.

Versão do compilador: https://blog.rust-lang.org/2025/09/18/Rust-1.90.0/
Documentação HTTP: https://docs.rs/hyper/1.7.0/hyper/server/conn/http1/struct.Builder.html
