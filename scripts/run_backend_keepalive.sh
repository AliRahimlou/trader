#!/bin/zsh
set -eu
ROOT="${0:A:h:h}"
cd "$ROOT"
exec "$ROOT/.venv/bin/python" -u backend_server.py --host 127.0.0.1 --port 8011
