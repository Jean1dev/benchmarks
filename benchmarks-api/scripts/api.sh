#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
language="${1:-}"
case "$language" in go|rust|python|node|java) ;; *) echo 'Uso: bash scripts/api.sh go|rust|python|node|java' >&2; exit 2 ;; esac
python3 scripts/init_env.py
docker compose --profile '*' stop api-go api-rust api-python api-node api-java
docker compose --profile "$language" up -d --build "api-$language"
