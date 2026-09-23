CREATE TABLE messages (
    id UUID PRIMARY KEY,
    nonce BYTEA NOT NULL,
    ciphertext BYTEA NOT NULL,
    tag BYTEA NOT NULL
);
