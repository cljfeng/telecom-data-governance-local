#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

if [ ! -x ".venv/bin/python" ]; then
  python3 -m venv .venv
fi

.venv/bin/python -m pip install -e ".[package]"
.venv/bin/python -m PyInstaller \
  --clean \
  --onefile \
  --name zufeidianfei-governance \
  --add-data "src/governance_app/static:governance_app/static" \
  src/governance_app/desktop.py

rm -rf build zufeidianfei-governance.spec

echo "打包完成：$ROOT_DIR/dist/zufeidianfei-governance"
