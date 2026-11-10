import json
import os
import threading
import unittest
import uuid
from http.server import ThreadingHTTPServer
from urllib import request
from urllib.error import HTTPError

import psycopg
from psycopg_pool import ConnectionPool

from api.app import Handler, apply_debit


def database_url():
    return os.environ["DATABASE_URL"]


def open_account(balance):
    account_id = uuid.uuid4()
    with psycopg.connect(database_url()) as conn:
        conn.execute(
            "INSERT INTO balances (account_id, balance) VALUES (%s, %s)",
            (account_id, balance),
        )
        conn.commit()
    return account_id


class DebitTests(unittest.TestCase):
    def test_debit_then_balance_and_replay(self):
        account_id = open_account(1000)
        first = uuid.uuid4()
        second = uuid.uuid4()
        with psycopg.connect(database_url()) as conn:
            status, balance = apply_debit(conn, account_id, first, 400)
            self.assertEqual(("applied", 600), (status, balance))
            status, balance = apply_debit(conn, account_id, second, 100)
            self.assertEqual(("applied", 500), (status, balance))
            status, balance = apply_debit(conn, account_id, first, 400)
            self.assertEqual(("replay", 600), (status, balance))
            current = conn.execute(
                "SELECT balance FROM balances WHERE account_id = %s",
                (account_id,),
            ).fetchone()[0]
        self.assertEqual(500, current)

    def test_same_transaction_with_other_amount_conflicts(self):
        account_id = open_account(1000)
        transaction_id = uuid.uuid4()
        with psycopg.connect(database_url()) as conn:
            apply_debit(conn, account_id, transaction_id, 100)
            status, balance = apply_debit(conn, account_id, transaction_id, 200)
            current = conn.execute(
                "SELECT balance FROM balances WHERE account_id = %s",
                (account_id,),
            ).fetchone()[0]
        self.assertEqual("transaction_conflict", status)
        self.assertIsNone(balance)
        self.assertEqual(900, current)

    def test_insufficient_funds_leaves_balance(self):
        account_id = open_account(100)
        with psycopg.connect(database_url()) as conn:
            status, _ = apply_debit(conn, account_id, uuid.uuid4(), 101)
            current = conn.execute(
                "SELECT balance FROM balances WHERE account_id = %s",
                (account_id,),
            ).fetchone()[0]
        self.assertEqual("insufficient_funds", status)
        self.assertEqual(100, current)

    def test_unknown_account(self):
        with psycopg.connect(database_url()) as conn:
            status, balance = apply_debit(conn, uuid.uuid4(), uuid.uuid4(), 1)
        self.assertEqual("unknown_account", status)
        self.assertIsNone(balance)

    def test_exact_balance_reaches_zero(self):
        account_id = open_account(250)
        with psycopg.connect(database_url()) as conn:
            status, balance = apply_debit(conn, account_id, uuid.uuid4(), 250)
        self.assertEqual(("applied", 0), (status, balance))

    def test_concurrent_debits_stop_at_zero(self):
        account_id = open_account(1000)
        barrier = threading.Barrier(10)
        results = []

        def worker():
            barrier.wait()
            with psycopg.connect(database_url()) as conn:
                results.append(apply_debit(conn, account_id, uuid.uuid4(), 200)[0])

        threads = [threading.Thread(target=worker) for _ in range(10)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        with psycopg.connect(database_url()) as conn:
            current = conn.execute(
                "SELECT balance FROM balances WHERE account_id = %s",
                (account_id,),
            ).fetchone()[0]
            stored = conn.execute(
                "SELECT COUNT(*) FROM debits WHERE account_id = %s",
                (account_id,),
            ).fetchone()[0]
        self.assertEqual(5, results.count("applied"))
        self.assertEqual(5, results.count("insufficient_funds"))
        self.assertEqual(0, current)
        self.assertEqual(5, stored)

    def test_concurrent_replay_debits_once(self):
        account_id = open_account(1000)
        transaction_id = uuid.uuid4()
        barrier = threading.Barrier(8)
        results = []

        def worker():
            barrier.wait()
            with psycopg.connect(database_url()) as conn:
                results.append(apply_debit(conn, account_id, transaction_id, 100)[0])

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        with psycopg.connect(database_url()) as conn:
            current = conn.execute(
                "SELECT balance FROM balances WHERE account_id = %s",
                (account_id,),
            ).fetchone()[0]
        self.assertEqual(1, results.count("applied"))
        self.assertEqual(7, results.count("replay"))
        self.assertEqual(900, current)


class HttpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pool = ConnectionPool(database_url(), min_size=1, max_size=4, open=True)
        Handler.pool = cls.pool
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f"http://127.0.0.1:{cls.httpd.server_address[1]}"

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.pool.close()

    def call(self, method, path, payload=None):
        data = None if payload is None else json.dumps(payload).encode()
        headers = {} if data is None else {"Content-Type": "application/json"}
        req = request.Request(self.base + path, data=data, headers=headers, method=method)
        try:
            with request.urlopen(req) as response:
                return response.status, json.loads(response.read().decode())
        except HTTPError as error:
            try:
                return error.code, json.loads(error.read().decode())
            finally:
                error.close()

    def test_get_and_post(self):
        account_id = str(open_account(800))
        transaction_id = str(uuid.uuid4())
        status, body = self.call("GET", f"/balances/{account_id}")
        self.assertEqual(200, status)
        self.assertEqual({"account_id": account_id, "balance": 800}, body)
        status, body = self.call("POST", "/debits", {
            "account_id": account_id,
            "transaction_id": transaction_id,
            "amount": 300,
        })
        self.assertEqual(201, status)
        self.assertEqual(500, body["balance"])
        self.assertTrue(body["applied"])
        status, body = self.call("POST", "/debits", {
            "account_id": account_id,
            "transaction_id": transaction_id,
            "amount": 300,
        })
        self.assertEqual(200, status)
        self.assertFalse(body["applied"])
        self.assertEqual(500, body["balance"])
        status, body = self.call("GET", f"/balances/{account_id}")
        self.assertEqual(500, body["balance"])

    def test_invalid_and_missing(self):
        missing = str(uuid.uuid4())
        status, body = self.call("GET", f"/balances/{missing}")
        self.assertEqual((404, "unknown_account"), (status, body["error"]))
        status, body = self.call("GET", "/balances/nao-e-uuid")
        self.assertEqual((400, "invalid_request"), (status, body["error"]))
        status, body = self.call("POST", "/debits", {
            "account_id": missing,
            "transaction_id": str(uuid.uuid4()),
            "amount": 1.5,
        })
        self.assertEqual((400, "invalid_request"), (status, body["error"]))
        status, body = self.call("POST", "/debits", {
            "account_id": missing,
            "transaction_id": str(uuid.uuid4()),
            "amount": True,
        })
        self.assertEqual(400, status)
        status, body = self.call("GET", "/outra")
        self.assertEqual((404, "not_found"), (status, body["error"]))


if __name__ == "__main__":
    unittest.main()
