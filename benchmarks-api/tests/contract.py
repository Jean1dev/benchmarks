#!/usr/bin/env python3
"""Shared functional checks. Requires the selected API and Compose PostgreSQL."""
import argparse
import concurrent.futures
import http.client
import json
import subprocess
import time
import uuid
from urllib.parse import urlsplit

ROOT = __import__('pathlib').Path(__file__).resolve().parents[1]


def sql(statement):
    return subprocess.check_output(
        ['docker', 'compose', 'exec', '-T', 'postgres', 'psql', '-U', 'benchmark', '-d', 'benchmark', '-At', '-v', 'ON_ERROR_STOP=1', '-c', statement],
        cwd=ROOT, text=True).strip()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--url', default='http://127.0.0.1:8080')
    args = parser.parse_args()
    address = urlsplit(args.url)
    ids = []
    checks = 0

    def request(method, path, payload=None, raw=None, connection=None):
        owned = connection is None
        conn = connection or http.client.HTTPConnection(address.hostname, address.port or 80, timeout=15)
        body = raw if raw is not None else (json.dumps(payload, ensure_ascii=False).encode() if payload is not None else None)
        try:
            conn.request(method, path, body, {'Content-Type': 'application/json'})
            response = conn.getresponse()
            data = response.read()
            assert response.getheader('Content-Type', '').startswith('application/json'), (response.status, data)
            return response.status, json.loads(data)
        finally:
            if owned:
                conn.close()

    def expect(method, path, status, payload=None, raw=None, error=None):
        nonlocal checks
        actual, data = request(method, path, payload, raw)
        assert actual == status, (method, path, status, actual, data)
        if error:
            assert data == {'error': error}, data
        checks += 1
        return data

    def create(message, extras=None):
        data = expect('POST', '/messages', 201, {'message': message, **(extras or {})})
        identifier = data['id']
        assert str(uuid.UUID(identifier)) == identifier and uuid.UUID(identifier).version == 4
        ids.append(identifier)
        return identifier

    deadline = time.monotonic() + 60
    while True:
        try:
            status, _ = request('GET', '/messages/00000000-0000-0000-0000-000000000000')
            if status == 404:
                break
        except (OSError, ValueError):
            pass
        if time.monotonic() > deadline:
            raise RuntimeError('API não ficou pronta em 60 segundos')
        time.sleep(0.5)

    try:
        for message in ['', 'olá 🌊 "aspas" \\ \n\t', 'a' * 16384, 'é' * 8192, '😀' * 4096]:
            identifier = create(message, {'ignored': True})
            assert expect('GET', '/messages/' + identifier, 200) == {'id': identifier, 'message': message}
            assert expect('GET', '/messages/' + identifier.upper(), 200)['message'] == message
        for payload in [{}, {'message': None}, {'message': 1}, {'message': True}, {'message': []}, [], 'text']:
            expect('POST', '/messages', 400, payload, error='invalid_request')
        for body in [b'{', b'{"message":"ok"} trailing', b'{"message":"\xff"}']:
            expect('POST', '/messages', 400, raw=body, error='invalid_request')
        for message in ['a' * 16385, 'é' * 8193]:
            expect('POST', '/messages', 413, {'message': message}, error='payload_too_large')
        expect('POST', '/messages', 413, raw=b' ' * (131072 + 1), error='payload_too_large')
        expect('GET', '/messages/not-a-uuid', 400, error='invalid_id')
        expect('GET', '/messages/' + str(uuid.uuid4()), 404, error='not_found')
        expect('GET', '/unknown', 404, error='not_found')

        marker = 'benchmark-secret-' + uuid.uuid4().hex
        identifier = create(marker)
        row = sql(f"SELECT octet_length(nonce), octet_length(tag), octet_length(ciphertext), encode(ciphertext, 'hex') FROM messages WHERE id='{identifier}'")
        nonce_len, tag_len, cipher_len, ciphertext = row.split('|')
        assert (nonce_len, tag_len, int(cipher_len)) == ('12', '16', len(marker.encode()))
        assert marker.encode() not in bytes.fromhex(ciphertext)
        checks += 1
        for column in ['ciphertext', 'tag']:
            tampered = create(marker)
            sql(f"UPDATE messages SET {column}=set_byte({column}, 0, get_byte({column}, 0) # 1) WHERE id='{tampered}'")
            expect('GET', '/messages/' + tampered, 500, error='internal_error')

        # Sequential requests on the same connection exercise HTTP/1.1 framing.
        conn = http.client.HTTPConnection(address.hostname, address.port or 80, timeout=15)
        try:
            for _ in range(3):
                status, data = request('GET', '/messages/' + identifier, connection=conn)
                assert status == 200 and data['message'] == marker
                checks += 1
        finally:
            conn.close()
        with concurrent.futures.ThreadPoolExecutor(max_workers=20) as pool:
            replies = list(pool.map(lambda _: request('GET', '/messages/' + identifier), range(40)))
        assert all(status == 200 and data['message'] == marker for status, data in replies)
        checks += 1
        print(f'PASS: {checks} verificações compartilhadas, incluindo 40 GETs concorrentes.')
    finally:
        if ids:
            sql('DELETE FROM messages WHERE id IN (' + ','.join("'" + str(uuid.UUID(i)) + "'" for i in ids) + ')')


if __name__ == '__main__':
    main()
