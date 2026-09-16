#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")/.."
exec .venv/bin/python backend_server.py --host 127.0.0.1 --port 8011 "$@"
