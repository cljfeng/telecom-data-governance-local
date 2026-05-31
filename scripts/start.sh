#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PORT="${PORT:-8765}"

cd "$ROOT_DIR"

if [ ! -x ".venv/bin/python" ]; then
  python3 -m venv .venv
fi

.venv/bin/python -m pip install -e ".[test]"

echo "Local governance app running at http://127.0.0.1:${PORT}"
PYTHONPATH=src .venv/bin/python -m governance_app.server --workspace . --port "$PORT"
