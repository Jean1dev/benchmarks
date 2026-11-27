import os
import uuid

import psycopg

NAMESPACE = uuid.uuid5(uuid.NAMESPACE_DNS, "benchmarks-banco.local")


def account_id(index):
    return uuid.uuid5(NAMESPACE, str(index))


def main():
    count = int(os.environ.get("SEED_ACCOUNTS", "10"))
    balance = int(os.environ.get("SEED_BALANCE", "100000"))
    if count < 1 or balance < 0:
        raise SystemExit("SEED_ACCOUNTS >= 1 e SEED_BALANCE >= 0")
    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        with conn.cursor() as cur:
            for index in range(count):
                cur.execute(
                    """
                    INSERT INTO balances (account_id, balance)
                    VALUES (%s, %s)
                    ON CONFLICT (account_id) DO NOTHING
                    """,
                    (account_id(index), balance),
                )
        conn.commit()
    for index in range(count):
        print(account_id(index))


if __name__ == "__main__":
    main()
