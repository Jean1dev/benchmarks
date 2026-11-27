import json
import os
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from psycopg import errors
from psycopg_pool import ConnectionPool

MAX_BODY = 8192
MAX_AMOUNT = 9_223_372_036_854_775_807


def accept_uuid(value):
    if not isinstance(value, str):
        return None
    try:
        return uuid.UUID(value)
    except (ValueError, AttributeError, TypeError):
        return None


def parse_amount(value):
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    if value < 1 or value > MAX_AMOUNT:
        return None
    return value


def apply_debit(conn, account_id, transaction_id, amount):
    try:
        with conn.transaction():
            balance = conn.execute(
                "SELECT balance FROM balances WHERE account_id = %s FOR UPDATE",
                (account_id,),
            ).fetchone()
            if balance is None:
                return "unknown_account", None
            existing = conn.execute(
                """
                SELECT account_id, amount, balance_after
                FROM debits
                WHERE transaction_id = %s
                """,
                (transaction_id,),
            ).fetchone()
            if existing is not None:
                if existing[0] != account_id or existing[1] != amount:
                    return "transaction_conflict", None
                return "replay", existing[2]
            updated = conn.execute(
                """
                UPDATE balances
                SET balance = balance - %s
                WHERE account_id = %s AND balance >= %s
                RETURNING balance
                """,
                (amount, account_id, amount),
            ).fetchone()
            if updated is None:
                return "insufficient_funds", None
            conn.execute(
                """
                INSERT INTO debits (transaction_id, account_id, amount, balance_after)
                VALUES (%s, %s, %s, %s)
                """,
                (transaction_id, account_id, amount, updated[0]),
            )
            return "applied", updated[0]
    except errors.UniqueViolation:
        return "transaction_conflict", None


def read_balance(conn, account_id):
    row = conn.execute(
        "SELECT balance FROM balances WHERE account_id = %s",
        (account_id,),
    ).fetchone()
    if row is None:
        return None
    return row[0]


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    pool = None

    def log_message(self, fmt, *args):
        return

    def send_json(self, status, payload):
        body = json.dumps(payload, separators=(",", ":")).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def read_body(self):
        raw_length = self.headers.get("Content-Length")
        if raw_length is None:
            return None
        try:
            length = int(raw_length)
        except ValueError:
            return None
        if length < 1 or length > MAX_BODY:
            return None
        return self.rfile.read(length)

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        prefix = "/balances/"
        if not path.startswith(prefix) or path.count("/") != 2:
            self.send_json(404, {"error": "not_found"})
            return
        account_id = accept_uuid(path[len(prefix):])
        if account_id is None:
            self.send_json(400, {"error": "invalid_request"})
            return
        with self.pool.connection() as conn:
            balance = read_balance(conn, account_id)
        if balance is None:
            self.send_json(404, {"error": "unknown_account"})
            return
        self.send_json(200, {"account_id": str(account_id), "balance": balance})

    def do_POST(self):
        if self.path.split("?", 1)[0] != "/debits":
            self.send_json(404, {"error": "not_found"})
            return
        raw = self.read_body()
        if raw is None:
            self.send_json(400, {"error": "invalid_request"})
            return
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            self.send_json(400, {"error": "invalid_request"})
            return
        if not isinstance(payload, dict):
            self.send_json(400, {"error": "invalid_request"})
            return
        account_id = accept_uuid(payload.get("account_id"))
        transaction_id = accept_uuid(payload.get("transaction_id"))
        amount = parse_amount(payload.get("amount"))
        if account_id is None or transaction_id is None or amount is None:
            self.send_json(400, {"error": "invalid_request"})
            return
        with self.pool.connection() as conn:
            status, balance = apply_debit(conn, account_id, transaction_id, amount)
        if status == "applied":
            self.send_json(201, {
                "account_id": str(account_id),
                "transaction_id": str(transaction_id),
                "balance": balance,
                "applied": True,
            })
            return
        if status == "replay":
            self.send_json(200, {
                "account_id": str(account_id),
                "transaction_id": str(transaction_id),
                "balance": balance,
                "applied": False,
            })
            return
        self.send_json(404 if status == "unknown_account" else 409, {"error": status})


def main():
    port = int(os.environ.get("PORT", "8080"))
    Handler.pool = ConnectionPool(
        os.environ["DATABASE_URL"],
        min_size=1,
        max_size=int(os.environ.get("DB_POOL_MAX", "10")),
        timeout=5,
        open=True,
    )
    ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()


if __name__ == "__main__":
    main()
