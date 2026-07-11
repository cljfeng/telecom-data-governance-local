#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

.venv/bin/python -m ruff check src tests
.venv/bin/python -m mypy
.venv/bin/python -m pytest -q --cov=governance_app --cov-report=term-missing
.venv/bin/python -m compileall -q src
for module in src/governance_app/static/*.js; do
  node --check "$module"
done
bash -n scripts/start.sh
bash -n scripts/build_app.sh
