# Banco transacional

Cada conta é só um `account_id` e um saldo em centavos. Não há cadastro, nome, crédito avulso nem transferência. O saldo nasce no seed. O usuário envia um débito e consulta o que restou.

O débito cabe inteiro numa transação do PostgreSQL: trava a linha da conta, recusa se o saldo não cobre o valor e grava o `transaction_id`. A mesma requisição, repetida com o mesmo corpo, não debita de novo.

## Contrato

`GET /balances/{account_id}`

```json
{"account_id":"UUID","balance":100000}
```

`POST /debits`

```json
{"account_id":"UUID","transaction_id":"UUID","amount":1500}
```

`amount` é inteiro, em centavos, maior que zero. A resposta devolve o saldo **depois daquele débito**:

```json
{"account_id":"UUID","transaction_id":"UUID","balance":98500,"applied":true}
```

| Situação | HTTP | Corpo |
| --- | --- | --- |
| Débito aplicado | 201 | `applied: true` |
| Mesmo `transaction_id`, mesmo corpo | 200 | `applied: false` e o saldo daquela aplicação |
| Mesmo `transaction_id`, outro corpo | 409 | `transaction_conflict` |
| Saldo insuficiente | 409 | `insufficient_funds` |
| Conta ausente | 404 | `unknown_account` |
| JSON, UUID ou valor inválido | 400 | `invalid_request` |

`GET` devolve o saldo atual. Ele muda com débitos posteriores. O saldo dentro da resposta do `POST` fica o daquela transação.

A chave deste cenário é `account_id`. Consulta e débito dessa conta usam uma linha. Partir os dados, mais adiante, é colocar cada conta num PostgreSQL e mandar as duas rotas para o mesmo lugar.

## Executar

Na pasta `benchmarks-banco`, com Docker:

```bash
docker compose up -d --build
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
DATABASE_URL=postgresql://banco:banco@127.0.0.1:55432/banco?sslmode=disable \
  SEED_ACCOUNTS=10 SEED_BALANCE=100000 \
  .venv/bin/python scripts/seed.py
```

O script imprime os `account_id`. O de índice 0 é sempre `96aa190e-fd9f-5368-a930-ef2fdc0bbcfa`. A API fica em `http://127.0.0.1:8088`.

```bash
curl -sS http://127.0.0.1:8088/balances/ACCOUNT_ID
curl -sS http://127.0.0.1:8088/debits \
  -H 'Content-Type: application/json' \
  -d '{"account_id":"ACCOUNT_ID","transaction_id":"UUID","amount":1500}'
```

O PostgreSQL local usa usuário, senha e banco `banco`, publicados só em `127.0.0.1:55432`. O volume guarda os saldos. Subir de novo não reaplica o schema; apagar o volume recomeça as contas.

```bash
DATABASE_URL=postgresql://banco:banco@127.0.0.1:55432/banco?sslmode=disable \
  .venv/bin/python -m unittest discover -s tests -v
```
