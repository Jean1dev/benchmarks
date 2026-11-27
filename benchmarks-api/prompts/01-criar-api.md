# Prompt 1 — Implementar uma API para o benchmark

Você é responsável por implementar uma API mínima para um benchmark entre linguagens. Trabalhe no repositório atual e entregue código executável, containers, testes funcionais e documentação. Não se limite a sugerir uma solução.

## Parâmetros de entrada

- Linguagem: `[LINGUAGEM]`.
- Versão: `[VERSÃO]`. Se não informada, escolha uma versão estável e fixe a versão exata.
- Identificador da implementação: `[IMPLEMENTACAO]`.

Inspecione o repositório e suas instruções antes de editar. Implemente somente a linguagem solicitada. Não substitua outras implementações.

## Contrato compartilhado

Este trabalho é a primeira de três etapas: API, carga com Gatling/Scala e relatório comparativo. As próximas etapas devem reutilizar o contrato aqui definido.

Organize, adaptando à estrutura existente:

- `apis/<implementacao>/`: código, Dockerfile, dependências fixadas e README.
- `benchmark/contract.md`: contrato HTTP, criptográfico e regras comuns.
- `benchmark/schema.sql`: schema único.
- `benchmark/implementations/<implementacao>.json`: linguagem, runtime, versões, bibliotecas, comando de build, concorrência, flags e identificação da imagem.
- `compose.yaml`: PostgreSQL compartilhado e seleção de uma API por execução.
- `.env.example`: configurações sem segredos reais.

Se esses contratos já existirem, reutilize-os. Não modifique regras comuns unilateralmente para favorecer uma linguagem. Registre incompatibilidades concretas.

## Implementação direta na linguagem

Prefira a biblioteca padrão. Permita bibliotecas essenciais e consolidadas para HTTP, PostgreSQL e criptografia quando necessário. Não use ORM nem framework web completo. Nunca implemente primitivas criptográficas manualmente.

Use consultas parametrizadas e pool com máximo de 10 conexões. Não use cache de dados, compressão HTTP, batching, retries automáticos de operações ou persistência assíncrona. Use HTTP/1.1 com keep-alive, sem TLS no ambiente controlado.

Use build de produção e otimizações normais da linguagem. Documente processos, threads, workers, GC, JIT e flags aplicáveis. Desative logs por requisição, mantendo erros operacionais sem conteúdo sensível.

## Exatamente dois endpoints de aplicação

### POST /messages

Entrada: `{"message":"texto de exemplo"}`.

Valide que `message` existe e é string. Aceite string vazia e ignore campos extras. Limite a mensagem a 16 KiB após decodificação JSON, em bytes UTF-8, e o corpo HTTP a 128 KiB.

Gere UUID v4, criptografe os bytes UTF-8 e execute um INSERT parametrizado. Responda somente após confirmação da transação, inclusive quando usar autocommit.

Resposta HTTP 201: `{"id":"UUID"}`.

### GET /messages/{id}

Valide o UUID, execute um SELECT pela chave primária, autentique e descriptografe o conteúdo.

Resposta HTTP 200: `{"id":"UUID","message":"texto de exemplo"}`.

### Erros comuns

Todas as respostas têm `Content-Type: application/json`. Use `{"error":"codigo"}`:

| Condição | HTTP | Código |
| --- | --- | --- |
| JSON inválido ou message ausente/não string | 400 | invalid_request |
| UUID inválido | 400 | invalid_id |
| Registro inexistente | 404 | not_found |
| Mensagem ou corpo acima do limite | 413 | payload_too_large |
| Falha de banco, criptografia ou erro interno | 500 | internal_error |

Não exponha detalhes internos. Não adicione endpoints de health check, métricas ou documentação; faça verificações de prontidão externamente.

## Criptografia e persistência

Use AES-256-GCM, chave de 32 bytes fornecida em Base64 por `ENCRYPTION_KEY_BASE64`, nonce de 12 bytes gerado por CSPRNG para cada mensagem e tag de 16 bytes, sem AAD. Armazene nonce, ciphertext e tag separadamente. Não derive chaves por requisição nem reutilize deliberadamente nonces.

Valide a chave ao iniciar e encerre se ausente ou inválida. A mesma chave de teste deve estar disponível para preparar e ler os dados de uma execução. Não a registre nem a inclua no Git ou na imagem.

Schema comum:

```sql
CREATE TABLE messages (
    id UUID PRIMARY KEY,
    nonce BYTEA NOT NULL,
    ciphertext BYTEA NOT NULL,
    tag BYTEA NOT NULL
);
```

Não adicione índices. Não armazene texto puro. Inicialize o schema fora das requisições. Preserve as garantias de durabilidade do PostgreSQL, inclusive fsync e synchronous_commit.

## Containers e recursos

Use Docker Compose com versões exatas de imagens e dependências, sem `latest`:

| Serviço | Limite de CPU | Limite de memória |
| --- | --- | --- |
| API | 1 CPU | 256 MiB |
| PostgreSQL | 1 CPU | 512 MiB |

Mantenha a mesma versão e configuração do PostgreSQL para todas as linguagens. Configure limites efetivamente aplicados pelo Docker Compose local e documente a verificação com inspect/cgroups. Fixe e registre a política de swap. Não aumente limites silenciosamente diante de OOM.

Use `PORT=8080`, `DATABASE_URL`, `DB_POOL_MAX=10` e `ENCRYPTION_KEY_BASE64`. A API escuta em `0.0.0.0`. PostgreSQL deve estar pronto antes de iniciar a API. Documente outros timeouts/configurações e mantenha-os iguais entre implementações quando comparáveis.

Use múltiplos estágios no Dockerfile quando adequado. Disponibilize comandos para selecionar uma implementação por vez. Limite também qualquer container auxiliar acrescentado ao projeto.

## Verificações e entrega

Execute testes funcionais para round-trip POST/GET, string vazia, Unicode/emoji/escapes, limites em bytes, JSON/UUID inválidos, campo ausente/não string e registro inexistente. Verifique ausência de texto puro no banco, rejeição de ciphertext/tag adulterados e falha de inicialização com chave inválida. Use registros isolados para testes destrutivos.

Valide build e configuração do Compose quando o ambiente permitir. Não execute a campanha de carga nesta etapa. Entregue os comandos de reprodução, arquivos alterados, verificações realizadas e limitações concretas. Não invente resultados.
