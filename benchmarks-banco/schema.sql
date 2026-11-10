CREATE TABLE balances (
    account_id UUID PRIMARY KEY,
    balance BIGINT NOT NULL CHECK (balance >= 0)
);

CREATE TABLE debits (
    transaction_id UUID PRIMARY KEY,
    account_id UUID NOT NULL REFERENCES balances (account_id),
    amount BIGINT NOT NULL CHECK (amount > 0),
    balance_after BIGINT NOT NULL CHECK (balance_after >= 0)
);
