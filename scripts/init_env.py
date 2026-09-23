#!/usr/bin/env python3
"""Generate a local benchmark key once; never overwrite an existing key."""
import base64
import os
from pathlib import Path
import secrets

target = Path(__file__).resolve().parents[1] / '.env'
try:
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
except FileExistsError:
    print('.env já existe; preservado.')
else:
    with os.fdopen(fd, 'w') as stream:
        stream.write('ENCRYPTION_KEY_BASE64=' + base64.b64encode(secrets.token_bytes(32)).decode() + '\nAPI_PORT=8080\n')
    print('.env criado com chave aleatória (não versionado).')
