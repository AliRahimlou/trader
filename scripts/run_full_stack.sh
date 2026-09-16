#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")/.."
./scripts/run_backend.sh &
backend_pid=$!
trap 'kill "$backend_pid" 2>/dev/null || true' EXIT INT TERM
./scripts/run_frontend.sh
