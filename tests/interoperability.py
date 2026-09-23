#!/usr/bin/env python3
"""Verify AES-GCM interoperability across five built APIs using the Compose DB.

Run from any directory: python3 tests/interoperability.py
Requires initialized .env, running PostgreSQL and all five images built.
No load test is performed. Only records created by this run are deleted.
"""
import argparse
import http.client
import json
from pathlib import Path
import subprocess
import time
import uuid
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
LANGUAGES = ('go', 'rust', 'python', 'node', 'java')
SERVICES = tuple('api-' + language for language in LANGUAGES)


def compose(*args, capture=False):
    return subprocess.run(
        ['docker', 'compose', '--profile', '*', *args],
        cwd=ROOT, text=True, check=True,
        stdout=subprocess.PIPE if capture else None,
        timeout=120,
    )


def sql(statement):
    return compose(
        'exec', '-T', 'postgres', 'psql', '-U', 'benchmark', '-d', 'benchmark',
        '-At', '-v', 'ON_ERROR_STOP=1', '-c', statement, capture=True,
    ).stdout.strip()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', default='http://127.0.0.1:8080',
                        help='URL published by Compose (adjust for API_PORT)')
    parser.add_argument('--ready-timeout', type=float, default=60)
    args = parser.parse_args()
    address = urlsplit(args.url)
    if address.scheme != 'http' or not address.hostname or address.path not in ('', '/'):
        parser.error('--url must be an HTTP origin, without a path')
    if args.ready_timeout <= 0:
        parser.error('--ready-timeout must be positive')
    records = {}
    created_ids = []

    def request(method, path, payload=None):
        connection = http.client.HTTPConnection(address.hostname, address.port or 80, timeout=10)
        body = json.dumps(payload, ensure_ascii=False).encode('utf-8') if payload is not None else None
        try:
            connection.request(method, path, body, {'Content-Type': 'application/json'})
            response = connection.getresponse()
            data = response.read()
            if not response.getheader('Content-Type', '').startswith('application/json'):
                raise RuntimeError('API returned a non-JSON Content-Type')
            return response.status, json.loads(data)
        finally:
            connection.close()

    def stop_apis():
        # Explicit services and repository cwd restrict the operation to this project.
        compose('stop', *SERVICES)

    def select(language):
        stop_apis()
        compose('up', '-d', '--no-deps', '--no-build', '--pull', 'never', 'api-' + language)
        deadline = time.monotonic() + args.ready_timeout
        probe_id = str(uuid.uuid4())
        while time.monotonic() < deadline:
            try:
                status, body = request('GET', '/messages/' + probe_id)
                if status == 404 and body == {'error': 'not_found'}:
                    return
            except (OSError, ValueError, http.client.HTTPException, RuntimeError):
                pass
            time.sleep(0.25)
        raise RuntimeError(f'{language}: API did not become ready')

    try:
        if sql('SELECT 1') != '1':
            raise RuntimeError('PostgreSQL is not ready')
        # Write all records first; every reader subsequently observes all writers.
        for writer in LANGUAGES:
            select(writer)
            message = f'interoperabilidade {writer}: ação 日本語 😀 "aspas" \\ \n\t {uuid.uuid4()}'
            status, body = request('POST', '/messages', {'message': message})
            if status != 201 or not isinstance(body, dict) or not isinstance(body.get('id'), str):
                raise RuntimeError(f'{writer}: POST did not return 201 with an ID')
            identifier = str(uuid.UUID(body['id']))
            created_ids.append(identifier)
            if body['id'] != identifier or uuid.UUID(identifier).version != 4:
                raise RuntimeError(f'{writer}: POST returned a noncanonical UUID v4')
            records[writer] = (identifier, message)
            print(f'WRITE OK: {writer}', flush=True)

        checks = 0
        for reader in LANGUAGES:
            select(reader)
            for writer, (identifier, message) in records.items():
                status, body = request('GET', '/messages/' + identifier)
                if status != 200 or body != {'id': identifier, 'message': message}:
                    raise RuntimeError(f'interoperability failed: writer={writer}, reader={reader}')
                checks += 1
                print(f'READ OK: {writer} -> {reader}', flush=True)
        print(f'PASS: {len(records)} escritores e {checks} combinações escritor/leitor.', flush=True)
    finally:
        # Stop APIs even if SQL cleanup fails; never truncate tables or stop PostgreSQL.
        try:
            if created_ids:
                sql('DELETE FROM messages WHERE id IN (' +
                    ','.join("'" + identifier + "'" for identifier in created_ids) + ')')
        finally:
            stop_apis()


if __name__ == '__main__':
    main()
