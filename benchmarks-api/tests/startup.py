#!/usr/bin/env python3
"""Check invalid keys without a database or network and inspect runtime limits."""
import json
import subprocess
import sys

for language in sys.argv[1:] or ['go', 'rust', 'python', 'node', 'java']:
    for key in ['', 'invalid-base64!', 'YQ==']:
        result = subprocess.run(
            ['docker', 'run', '--rm', '--network', 'none', '--memory', '256m',
             '--memory-swap', '256m', '--cpus', '1', '-e', f'ENCRYPTION_KEY_BASE64={key}',
             f'benchmark-api-{language}:local'], capture_output=True, text=True, timeout=30)
        assert result.returncode not in (0, 125, 126, 127, 137), (language, result.returncode, result.stderr)
    print(f'PASS {language}: chave ausente, Base64 inválido e tamanho incorreto impedem inicialização.')

containers = subprocess.check_output(['docker', 'compose', '--profile', '*', 'ps', '-q'], text=True).split()
for container in containers:
    data = json.loads(subprocess.check_output(['docker', 'inspect', container], text=True))[0]
    config = data['HostConfig']
    service = data['Config']['Labels']['com.docker.compose.service']
    expected = (512 if service == 'postgres' else 256) * 1024 * 1024
    assert config['Memory'] == expected and config['MemorySwap'] == expected
    assert config['NanoCpus'] == 1_000_000_000
    print(f'PASS {service}: limite {expected} bytes, 1 CPU, swap desabilitada.')
